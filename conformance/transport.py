"""A small HTTP client that checks every response against the contract."""
from __future__ import annotations

from dataclasses import dataclass, field
import http.client
import json
from typing import Any
from urllib.parse import urlencode, urlsplit

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
    def detail(self) -> Any:
        value = self.json
        return value.get("detail") if isinstance(value, dict) else None

    def describe(self) -> str:
        return f"{self.method} {self.path.split('?')[0]} returned {self.status}"


class Client:
    """Send requests to one server. Paths are absolute, such as `/api/v1/health`."""

    def __init__(self, server: str, contract: Contract, timeout: float = 60):
        parts = urlsplit(server.rstrip("/"))
        if parts.scheme not in ("http", "https"):
            raise ValueError("the server address must be an http or https URL")
        self.scheme, self.netloc, self.prefix = parts.scheme, parts.netloc, parts.path
        self.contract = contract
        self.timeout = timeout
        self.issues: list[str] = []

    def request(self, method: str, path: str, token: str | None = None, json_body: Any = None,
                query: dict[str, Any] | None = None, body: bytes | None = None,
                headers: dict[str, str] | None = None) -> Response:
        h = dict(headers or {})
        if token is not None:
            h["Authorization"] = f"Bearer {token}"
        if json_body is not None:
            body = json.dumps(json_body).encode()
            h.setdefault("Content-Type", "application/json")
        if query:
            path = f"{path}?{urlencode(query)}"
        connection = http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
        conn = connection(self.netloc, timeout=self.timeout)
        try:
            conn.request(method, self.prefix + path, body=body, headers=h)
            raw = conn.getresponse()
            response = Response(method, path, raw.status,
                                {k.lower(): v for k, v in raw.getheaders()}, raw.read())
        finally:
            conn.close()
        response.contract_issues = self.contract.check_response(
            method, path, response.status, response.headers, response.body)
        self.issues += [f"{response.describe()}: {issue}" for issue in response.contract_issues]
        return response

    def get(self, path: str, token: str | None = None, **query: Any) -> Response:
        return self.request("GET", path, token, query={k: v for k, v in query.items() if v is not None})

    def post(self, path: str, token: str | None = None, body: Any = None) -> Response:
        return self.request("POST", path, token, json_body=body if body is not None else {})
