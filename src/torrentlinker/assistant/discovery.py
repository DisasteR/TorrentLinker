"""Discover active context limits without loading models or changing server settings."""

from urllib.parse import quote


def valid_limit(value):
    return type(value) is int and 512 <= value <= 2_000_000


def discover_context(models, provider, key):
    fallback = {
        "context_window": provider.context_window,
        "source": "fallback" if provider.context_auto else "manual",
        "detail": "",
    }
    if not provider.context_auto:
        return fallback

    def read(path, native=False):
        # Keep reverse-proxy prefixes and the configured origin. Never follow redirects.
        target = (
            provider.model_copy(update={"url": provider.url.removesuffix("/v1")})
            if native
            else provider
        )
        return models.request(target, key, path, timeout=3)

    def found(values, detail, source="server"):
        limits = [v for v in values if valid_limit(v)]
        return (
            {"context_window": min(limits), "source": source, "detail": detail}
            if limits
            else fallback
        )

    try:
        if provider.kind == "ollama":
            # /show describes trained capacity; /ps reports the loaded runner's actual size.
            wanted = (
                provider.model
                if ":" in provider.model.rsplit("/", 1)[-1]
                else provider.model + ":latest"
            )
            rows = read("/api/ps", native=True).get("models", [])
            return found(
                [
                    r.get("context_length")
                    for r in rows
                    if r.get("name") in {provider.model, wanted}
                    or r.get("model") in {provider.model, wanted}
                ],
                "Ollama running model",
            )
        if provider.kind == "lmstudio":
            rows = read("/api/v1/models", native=True).get("models", [])
            exact, by_key = [], []
            for row in rows:
                for instance in row.get("loaded_instances", []):
                    value = instance.get("config", {}).get("context_length")
                    if instance.get("id") == provider.model:
                        exact.append(value)
                    if row.get("key") == provider.model:
                        by_key.append(value)
            return found(exact or by_key, "LM Studio loaded instance")
        if provider.kind == "gemini":
            model = provider.model.removeprefix("models/")
            row = read("/models/" + quote(model, safe=""))
            if row.get("name") != "models/" + model:
                return fallback
            return found([row.get("inputTokenLimit")], "Gemini input limit", "provider")
        if provider.kind not in {"compatible", "openrouter"}:
            return fallback
        rows = read("/models").get("data", [])
        matches = [r for r in rows if r.get("id") == provider.model]
        if provider.kind == "openrouter":
            limits = [
                value
                for r in matches
                for value in (
                    r.get("context_length"),
                    (r.get("top_provider") or {}).get("context_length"),
                )
            ]
            return found(limits, "OpenRouter model metadata", "provider")
        result = found([r.get("max_model_len") for r in matches], "max_model_len")
        if result is not fallback:
            return result
        # llama.cpp reports trained size in /models; only /props gives its active slot size.
        if not matches:
            # Some servers answer any model name, so name what they serve instead.
            served = [r["id"] for r in rows if isinstance(r.get("id"), str)][:5]
            return {**fallback, "served_models": served} if served else fallback
        props = read("/props", native=True)
        aliases = props.get("model_aliases", [])
        if props.get("model_alias"):
            aliases = [*aliases, props["model_alias"]]
        if aliases:
            if provider.model not in aliases:
                return fallback
        elif len(rows) != 1:
            return fallback
        return found(
            [props.get("default_generation_settings", {}).get("n_ctx")], "llama.cpp active slot"
        )
    except (ValueError, TypeError, AttributeError, KeyError):
        # Missing, malformed, unauthorized or unavailable metadata never blocks a run.
        return fallback
