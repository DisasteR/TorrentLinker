"""Context discovery through owner setup and job APIs, with external HTTP fixtures."""

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.test_assistant import login, wait_run
from torrentlinker.web.app import create_app


def test_followup_receives_execution_evidence_for_unexecuted_success_claim(tmp_path):
    import json

    requests = []

    def server(request):
        if request.method == "GET":
            return httpx.Response(404)
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Téléchargement lancé, statut sent (1/1).",
                        }
                    }
                ]
            },
        )

    with TestClient(
        create_app(
            tmp_path, password="test-password-long", assistant_transport=httpx.MockTransport(server)
        )
    ) as browser:
        login(browser)
        provider = browser.post(
            "/api/setup/assistant/providers",
            json={
                "name": "Replay",
                "kind": "compatible",
                "url": "http://model.invalid/v1",
                "model": "test",
            },
        ).json()
        chat = browser.post(
            "/api/assistant/conversations",
            json={
                "provider_id": provider["id"],
                "policy": {"name": "Read", "clients": ["default"]},
            },
        ).json()
        identifiers = []
        for message in ("lance le telechargement", "retry"):
            submitted = browser.post(
                "/api/assistant/jobs", json={"conversation_id": chat["id"], "message": message}
            ).json()
            identifiers.append(submitted["id"])
            result = wait_run(browser, submitted["id"])
            assert result["status"] == "completed"
            assert result["events"] == []
        context = next(
            m["content"]
            for m in requests[1]["messages"]
            if str(m.get("content", "")).startswith("Application execution evidence")
        )
        ledger = json.loads(context.split("\n", 1)[1])
        assert ledger == [
            {
                "run_id": identifiers[0],
                "reply_status": "completed",
                "tool_calls": 0,
                "recent_tools": [],
            }
        ]
        assert "Zero tool calls means" in context
        assert "did not measure disk space" in context
        assert browser.get("/api/queue").json() == []
        assert browser.get("/api/agents").json()["proposals"] == []


def test_active_server_context_is_used_for_each_run(tmp_path):
    window = 262144
    requests = []

    def server(request):
        requests.append(request.url.path)
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "other", "max_model_len": 1000000},
                        {"id": "test", "max_model_len": window},
                    ]
                },
            )
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(
                200, json={"choices": [{"message": {"role": "assistant", "content": "Ready."}}]}
            )
        return httpx.Response(404)

    with TestClient(
        create_app(
            tmp_path, password="test-password-long", assistant_transport=httpx.MockTransport(server)
        )
    ) as browser:
        login(browser)
        provider = browser.post(
            "/api/setup/assistant/providers",
            json={
                "name": "Server",
                "kind": "compatible",
                "url": "http://model.invalid/v1",
                "model": "test",
            },
        ).json()
        detected = browser.get(f"/api/setup/assistant/providers/{provider['id']}/context")
        assert detected.is_success, detected.text
        assert detected.json()["context_window"] == 262144
        assert detected.json()["source"] == "server"
        chat = browser.post(
            "/api/assistant/conversations",
            json={
                "provider_id": provider["id"],
                "policy": {"name": "Read", "clients": ["default"]},
            },
        ).json()
        # A reconfigured server must not leave a stale, larger context in the runner.
        window = 16384
        requests.clear()
        run = browser.post(
            "/api/assistant/jobs",
            json={"conversation_id": chat["id"], "message": "Check this connection."},
        ).json()
        result = wait_run(browser, run["id"])
        assert result["status"] == "completed", result["error"]
        assert result["context"]["context_window"] == 16384
        assert result["context"]["source"] == "server"
        assert requests.index("/v1/models") < requests.index("/v1/chat/completions")


@pytest.mark.parametrize(
    ("kind", "path", "body", "expected", "source"),
    [
        (
            "ollama",
            "/proxy/api/ps",
            {
                "models": [
                    {"name": "other:latest", "context_length": 131072},
                    {"name": "test:latest", "context_length": 16384},
                ]
            },
            16384,
            "server",
        ),
        (
            "lmstudio",
            "/proxy/api/v1/models",
            {
                "models": [
                    {
                        "key": "test",
                        "max_context_length": 262144,
                        "loaded_instances": [{"id": "test", "config": {"context_length": 8192}}],
                    }
                ]
            },
            8192,
            "server",
        ),
        (
            "lmstudio",
            "/proxy/api/v1/models",
            {
                "models": [
                    {
                        "key": "test",
                        "max_context_length": 262144,
                        "loaded_instances": [],
                    }
                ]
            },
            32768,
            "fallback",
        ),
        (
            "compatible",
            "/proxy/props",
            {"model_alias": "test", "default_generation_settings": {"n_ctx": 16384}},
            16384,
            "server",
        ),
        (
            "compatible",
            "/proxy/props",
            {"model_alias": "different", "default_generation_settings": {"n_ctx": 131072}},
            32768,
            "fallback",
        ),
        (
            "openrouter",
            "/proxy/v1/models",
            {
                "data": [
                    {
                        "id": "test",
                        "context_length": 131072,
                        "top_provider": {"context_length": 65536},
                    }
                ]
            },
            65536,
            "provider",
        ),
        (
            "gemini",
            "/proxy/v1/models/test",
            {"name": "models/test", "inputTokenLimit": 1048576},
            1048576,
            "provider",
        ),
    ],
)
def test_context_metadata_uses_selected_loaded_model(tmp_path, kind, path, body, expected, source):
    def server(request):
        assert request.url.host == "model.invalid"
        if request.url.path == path:
            return httpx.Response(200, json=body)
        if request.url.path == "/proxy/v1/models":
            return httpx.Response(
                200, json={"data": [{"id": "test", "meta": {"n_ctx_train": 262144}}]}
            )
        return httpx.Response(404)

    with TestClient(
        create_app(
            tmp_path, password="test-password-long", assistant_transport=httpx.MockTransport(server)
        )
    ) as browser:
        login(browser)
        result = browser.post(
            "/api/setup/assistant/context",
            json={
                "name": "Server",
                "kind": kind,
                "url": "http://model.invalid/proxy/v1",
                "model": "test",
            },
        )
        assert result.is_success, result.text
        assert result.json()["context_window"] == expected
        assert result.json()["source"] == source


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(404),
        httpx.Response(401),
        httpx.Response(200, json={"data": [{"id": "other", "max_model_len": 262144}]}),
        httpx.Response(200, json={"data": [{"id": "test", "max_model_len": -1}]}),
        httpx.Response(200, json={"data": [{"id": "test", "max_model_len": "262144"}]}),
        httpx.Response(200, json={"data": None}),
        httpx.Response(200, content=b"not json"),
        httpx.Response(302, headers={"Location": "https://elsewhere.invalid/models"}),
    ],
)
def test_missing_or_invalid_context_metadata_uses_fallback(tmp_path, response):
    seen = []

    def server(request):
        seen.append(str(request.url))
        return response

    with TestClient(
        create_app(
            tmp_path, password="test-password-long", assistant_transport=httpx.MockTransport(server)
        )
    ) as browser:
        login(browser)
        result = browser.post(
            "/api/setup/assistant/context",
            json={
                "name": "Server",
                "kind": "compatible",
                "url": "http://model.invalid/v1",
                "model": "test",
                "context_auto": True,
                "context_window": 16384,
            },
        )
        assert result.is_success, result.text
        assert result.json()["context_window"] == 16384
        assert result.json()["source"] == "fallback"
        assert all(url.startswith("http://model.invalid/") for url in seen)


def test_fallback_names_the_models_a_server_serves_instead_of_the_configured_one(tmp_path):
    # Strata answers any model name, so a stale setting only shows as a lost context window.
    def server(request):
        if request.url.path == "/v1/models":
            return httpx.Response(
                200, json={"data": [{"id": "qwen-new", "meta": {"n_ctx": 262144}}]}
            )
        if request.url.path == "/props":
            return httpx.Response(
                200,
                json={"model_alias": "qwen-new", "default_generation_settings": {"n_ctx": 262144}},
            )
        return httpx.Response(404)

    with TestClient(
        create_app(
            tmp_path, password="test-password-long", assistant_transport=httpx.MockTransport(server)
        )
    ) as browser:
        login(browser)
        result = browser.post(
            "/api/setup/assistant/context",
            json={
                "name": "Server",
                "kind": "compatible",
                "url": "http://model.invalid/v1",
                "model": "qwen-old",
            },
        ).json()
        assert (result["context_window"], result["source"]) == (32768, "fallback")
        assert result["served_models"] == ["qwen-new"]


def test_manual_context_and_saved_key_are_preserved(tmp_path):
    seen = []

    def server(request):
        seen.append((request.url.host, request.headers.get("authorization")))
        return httpx.Response(200, json={"data": [{"id": "test", "max_model_len": 262144}]})

    with TestClient(
        create_app(
            tmp_path, password="test-password-long", assistant_transport=httpx.MockTransport(server)
        )
    ) as browser:
        assert browser.post("/api/setup/assistant/context", json={}).status_code in {401, 403}
        login(browser)
        body = {
            "name": "Server",
            "kind": "compatible",
            "url": "http://one.invalid/v1",
            "model": "test",
            "context_window": 65536,
            "api_key": "private-test-key",
        }
        # Legacy custom values without the auto flag remain manual.
        saved = browser.post("/api/setup/assistant/providers", json=body).json()
        assert saved["context_auto"] is False
        url = f"/api/setup/assistant/providers/{saved['id']}/context"
        result = browser.get(url).json()
        assert result["source"] == "manual"
        assert result["context_window"] == 65536
        assert seen == []
        body.pop("api_key")
        body["context_auto"] = True
        assert browser.post(url, json=body).json()["context_window"] == 262144
        assert seen == [("one.invalid", "Bearer private-test-key")]
        body["url"] = "http://two.invalid/v1"
        assert browser.post(url, json=body).is_success
        assert seen[-1] == ("two.invalid", None)
        assert "private-test-key" not in browser.get("/api/setup/assistant").text
        # Previewing never changes the saved manual preference or its credentials.
        assert browser.get(url).json()["source"] == "manual"
