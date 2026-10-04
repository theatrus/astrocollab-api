"""Small HTTP client that checks every response against the contract."""
from __future__ import annotations

from dataclasses import dataclass, field
import http.client
import json
from typing import Any
from urllib.parse import urlsplit
import uuid

from .contract import Contract


@dataclass
class Response:
    method: str
    path: str
    status: int
    headers: dict[str, str]
    body: bytes
    contract_issues: list[str] = field(default_factory=list)

    @property
    def json(self) -> Any:
        try:
            return json.loads(self.body)
        except ValueError:
            return None

    @property
    def code(self) -> str | None:
        value = self.json
        return value.get("code") if isinstance(value, dict) else None

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())

    def describe(self) -> str:
        text = f"{self.method} {self.path.split('?')[0]} returned {self.status}"
        return text + (f" {self.code}" if self.code else "")


class Client:
    """Send requests under one API root. Records contract issues per response."""

    def __init__(self, api_root: str, contract: Contract, timeout: float = 60):
        parts = urlsplit(api_root.rstrip("/"))
        if parts.scheme not in ("http", "https"):
            raise ValueError("API root must be an http or https URL")
        self.scheme, self.netloc, self.prefix = parts.scheme, parts.netloc, parts.path
        self.contract = contract
        self.timeout = timeout
        self.issues: list[str] = []

    def request(self, method: str, path: str, key: str | None = None, json_body: Any = None,
                body: bytes | None = None, headers: dict[str, str] | None = None,
                idempotent: bool | None = None) -> Response:
        """Send one request. POSTs get a fresh Idempotency-Key unless one is given
        or `idempotent` is False."""
        h = dict(headers or {})
        if key is not None:
            h["Authorization"] = f"Bearer {key}"
        if json_body is not None:
            body = json.dumps(json_body).encode()
            h.setdefault("Content-Type", "application/json")
        if method == "POST" and idempotent is not False and "Idempotency-Key" not in h:
            h["Idempotency-Key"] = str(uuid.uuid4())
        conn_type = http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
        conn = conn_type(self.netloc, timeout=self.timeout)
        try:
            conn.request(method, self.prefix + path, body=body, headers=h)
            raw = conn.getresponse()
            data = raw.read()
            response = Response(method, path, raw.status, {k.lower(): v for k, v in raw.getheaders()}, data)
        finally:
            conn.close()
        response.contract_issues = self.contract.check_response(method, path, response.status,
                                                                response.headers, response.body)
        self.issues += [f"{response.describe()}: {issue}" for issue in response.contract_issues]
        return response
