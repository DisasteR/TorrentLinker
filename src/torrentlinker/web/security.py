"""Single-owner sessions and encrypted server credentials, independent of desktop keyring."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import secrets
import threading
import time
from pathlib import Path

from cryptography.fernet import Fernet


def atomic_private(path: Path, data: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)
    path.chmod(0o600)


class ServerSecrets:
    """Encrypt UI-entered secrets at rest; deployment secret files override them.

    Back up both the key and vault. Protect the entire configuration volume: encryption
    cannot protect against someone who can read both files.
    """

    def __init__(self, directory: Path, files: dict[str, Path] | None = None):
        self.directory = directory
        self.files = files or {}
        key = directory / "credentials.key"
        if not key.exists():
            atomic_private(key, Fernet.generate_key())
        self.cipher = Fernet(key.read_bytes())
        self.path = directory / "credentials.enc"
        self.lock = threading.Lock()
        self.values: dict[str, str] = (
            json.loads(self.cipher.decrypt(self.path.read_bytes())) if self.path.exists() else {}
        )

    def get(self, name: str) -> str:
        with self.lock:
            return (
                self.files[name].read_text().strip()
                if name in self.files
                else self.values.get(name, "")
            )

    def redact(self, text: str, *, public_urls: bool = False) -> str:
        from torrentlinker.torrent_management import safe_error

        if not text:
            return text
        with self.lock:
            values = list(self.values.values())
            for path in self.files.values():
                try:
                    values.append(path.read_text().strip())
                except OSError:
                    pass
        for value in values:
            if value:
                text = text.replace(value, "[secret]")
        if public_urls:
            from torrentlinker.assistant.privacy import safe_content

            return safe_content(text)
        return safe_error(ValueError(text))

    def update(self, values: dict[str, str]) -> None:
        with self.lock:
            if any(name in self.files for name in values):
                raise ValueError(
                    "This credential is managed by a secret file. Edit that file instead."
                )
            updated = {**self.values, **values}
            atomic_private(self.path, self.cipher.encrypt(json.dumps(updated).encode()))
            self.values = updated


class Sessions:
    def __init__(self, password: str):
        if len(password) < 12:
            raise ValueError("Set a Web UI password of at least 12 characters.")
        self.salt = secrets.token_bytes(16)
        self.digest = self.hash(password)
        self.tokens: dict[str, float] = {}
        self.attempts: list[float] = []
        self.lock = threading.Lock()

    def hash(self, password: str) -> bytes:
        return hashlib.scrypt(password.encode(), salt=self.salt, n=16384, r=8, p=1)

    def login(self, password: str) -> str:
        with self.lock:
            now = time.monotonic()
            self.attempts = [t for t in self.attempts if now - t < 60]
            if len(self.attempts) >= 10:
                raise ValueError("Too many attempts. Wait one minute.")
            if not secrets.compare_digest(self.hash(password), self.digest):
                self.attempts.append(now)
                raise PermissionError("Incorrect password.")
            self.tokens = {k: v for k, v in self.tokens.items() if v > now}
            if len(self.tokens) >= 32:
                del self.tokens[next(iter(self.tokens))]
            token = secrets.token_urlsafe(32)
            self.tokens[token] = now + 12 * 3600
            return token

    def valid(self, token: str) -> bool:
        with self.lock:
            return self.tokens.get(token, 0) > time.monotonic()

    def logout(self, token: str) -> None:
        with self.lock:
            self.tokens.pop(token, None)


class TrustedProxies:
    """Let the reverse proxy that signs users in stand for the owner, and nobody else."""

    def __init__(self, networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]):
        if not networks:
            raise ValueError("Set the trusted proxy networks for reverse-proxy sign-in.")
        self.networks = networks

    def allows(self, host: str | None) -> bool:
        try:
            address = ipaddress.ip_address(host or "")
        except ValueError:
            return False
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return any(address in network for network in self.networks)
