"""Check HTTP requests and responses against the AstroCollab OpenAPI contract.

The contract only describes message shape. Stateful rules (ETags, idempotency,
credit) live in the server suite and the client proxy.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker
import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "openapi/astrocollab.yaml"
METHODS = ("get", "put", "post", "delete", "patch")
LOOPBACK_URL = r"^(https://|http://(127\.0\.0\.1|localhost|\[::1\])(:[0-9]+)?(/|$))"


@dataclass
class Operation:
    id: str
    method: str
    template: str
    regex: re.Pattern[str]
    spec: dict[str, Any]
    parameters: list[dict[str, Any]]

    @property
    def token_context(self) -> str:
        return self.spec.get("x-token-context", "account")

    def param(self, location: str, name: str) -> dict[str, Any] | None:
        for p in self.parameters:
            if p["in"] == location and p["name"].lower() == name.lower():
                return p
        return None


def _headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    return {k.lower(): v for k, v in (headers or {}).items()}


class Contract:
    def __init__(self, path: Path | str = DEFAULT_CONTRACT, allow_http_loopback: bool = False):
        self.doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        self.components = copy.deepcopy(self.doc["components"])
        if allow_http_loopback:
            _relax_https(self.components)
        self.format_checker = FormatChecker()
        self.operations: list[Operation] = []
        for template, item in self.doc["paths"].items():
            shared = item.get("parameters", [])
            for method, spec in item.items():
                if method not in METHODS:
                    continue
                params = [self._resolve(p) for p in shared + spec.get("parameters", [])]
                pattern = "^" + re.sub(r"\\\{([^}]+)\\\}", r"(?P<\1>[^/]+)", re.escape(template)) + "$"
                self.operations.append(Operation(spec["operationId"], method.upper(), template,
                                                 re.compile(pattern), spec, params))
        # Literal segments win over templated ones.
        self.operations.sort(key=lambda op: -len(re.sub(r"\{[^}]+\}", "", op.template)))
        self.by_id = {op.id: op for op in self.operations}

    def _resolve(self, node: dict[str, Any]) -> dict[str, Any]:
        while "$ref" in node:
            parts = node["$ref"][2:].split("/")
            node = self.doc
            for part in parts:
                node = node[part]
        return node

    # Matching and schema validation

    def match(self, method: str, path: str) -> tuple[Operation, dict[str, str]] | None:
        """Match a path relative to the API root, such as `/projects/<id>`."""
        path = path.split("?", 1)[0]
        for op in self.operations:
            if op.method == method.upper():
                m = op.regex.match(path)
                if m:
                    return op, m.groupdict()
        return None

    def path_known(self, path: str) -> bool:
        path = path.split("?", 1)[0]
        return any(op.regex.match(path) for op in self.operations)

    def validate(self, schema: dict[str, Any] | str, value: Any) -> list[str]:
        if isinstance(schema, str):
            schema = {"$ref": f"#/components/schemas/{schema}"}
        validator = Draft202012Validator({**schema, "components": self.components},
                                         format_checker=self.format_checker)
        errors = sorted(validator.iter_errors(value), key=lambda e: list(e.absolute_path))
        return [f"{_pointer(e.absolute_path)}: {e.message[:200]}" for e in errors[:10]]

    def schema_name(self, schema: dict[str, Any]) -> str:
        ref = schema.get("$ref", "")
        return ref.rsplit("/", 1)[-1] if ref else "inline schema"

    # Requests

    def check_request(self, method: str, path: str, headers: Mapping[str, str] | None,
                      body: bytes | None) -> list[str]:
        """Return client faults in one request. `path` is relative to the API root."""
        h = _headers(headers)
        found = self.match(method, path)
        if not found:
            if self.path_known(path):
                return [f"{method} is not defined for {path.split('?')[0]}"]
            return [f"unknown route {method} {path.split('?')[0]}"]
        op, path_params = found
        issues: list[str] = []
        body = body or b""

        auth = h.get("authorization")
        context = op.token_context
        if not context.startswith("public") and not auth:
            issues.append("missing Authorization header")
        if auth and not re.match(r"^Bearer \S+$", auth):
            issues.append("Authorization must use the Bearer scheme")

        query = _query(path)
        for name, value in query.items():
            p = op.param("query", name)
            if p is None:
                issues.append(f"unknown query parameter {name}")
                continue
            issues += [f"query {name} {m}" for m in self.validate(p.get("schema", {}), _coerce(value, p))]
        for p in op.parameters:
            loc, name = p["in"], p["name"]
            if loc == "path":
                issues += [f"path {name} {m}" for m in self.validate(p.get("schema", {}),
                                                                     _coerce(path_params.get(name, ""), p))]
            elif loc == "query" and p.get("required") and name not in query:
                issues.append(f"missing query parameter {name}")
            elif loc == "header" and name.lower() not in ("content-type",):
                value = h.get(name.lower())
                if value is None:
                    if p.get("required"):
                        issues.append(f"missing {name} header")
                    continue
                issues += [f"{name} header {m}" for m in self.validate(p.get("schema", {}), _coerce(value, p))]

        if op.param("header", "If-Match") and op.param("header", "If-None-Match") and op.method == "PUT":
            sent = [n for n in ("if-match", "if-none-match") if n in h]
            if len(sent) != 1:
                issues.append("send exactly one of If-Match or If-None-Match: *")

        request_body = op.spec.get("requestBody")
        if request_body is None:
            if body:
                issues.append("sent a body to an operation that takes none")
            return issues
        content = request_body.get("content", {})
        ctype = h.get("content-type", "").split(";")[0].strip().lower()
        if "application/octet-stream" in content:
            if ctype != "application/octet-stream":
                issues.append("part upload must use Content-Type application/octet-stream")
            length = h.get("content-length")
            if length is not None and length.isdigit() and int(length) != len(body):
                issues.append("Content-Length does not match the body")
            digest = h.get("x-part-sha256")
            if digest and digest != hashlib.sha256(body).hexdigest():
                issues.append("X-Part-SHA256 does not match the part bytes")
            return issues
        if ctype != "application/json":
            issues.append("JSON body must use Content-Type application/json")
        try:
            value = json.loads(body)
        except ValueError:
            return issues + ["body is not valid JSON"]
        schema = content["application/json"]["schema"]
        issues += [f"body {m}" for m in self.validate(schema, value)]
        return issues

    # Responses

    def check_response(self, method: str, path: str, status: int, headers: Mapping[str, str] | None,
                       body: bytes | None) -> list[str]:
        """Return server faults in one response."""
        h = _headers(headers)
        found = self.match(method, path)
        if not found:
            return []
        op, _ = found
        body = body or b""
        responses = op.spec["responses"]
        declared = responses.get(str(status))
        issues: list[str] = []
        if declared is None:
            if status < 400:
                return [f"undeclared status {status} for {op.id}"]
            declared = responses.get("default")
            if declared is None:
                return [f"undeclared status {status} for {op.id}"]
        declared = self._resolve(declared)
        ctype = h.get("content-type", "").split(";")[0].strip().lower()

        if status == 304 or status == 204:
            if body:
                issues.append(f"{status} response must have no body")
            return issues
        if status >= 400:
            if ctype != "application/problem+json":
                issues.append(f"error {status} must use application/problem+json, got {ctype or 'none'}")
            try:
                problem = json.loads(body)
            except ValueError:
                return issues + ["error body is not valid JSON"]
            issues += [f"problem {m}" for m in self.validate("Problem", problem)]
            if isinstance(problem, dict) and problem.get("status") not in (None, status):
                issues.append(f"problem status {problem.get('status')} differs from HTTP {status}")
            if status == 429 and "retry-after" not in h:
                issues.append("429 response needs Retry-After")
            return issues
        for name, spec in declared.get("headers", {}).items():
            spec = self._resolve(spec)
            value = h.get(name.lower())
            if value is None:
                if spec.get("required"):
                    issues.append(f"missing {name} response header")
            elif name.lower() != "etag":
                issues += [f"{name} header {m}" for m in self.validate(spec.get("schema", {}), value)]
        content = declared.get("content", {})
        if not content:
            return issues
        media = content.get("application/json")
        if media is None:
            return issues
        if ctype != "application/json":
            issues.append(f"response must use application/json, got {ctype or 'none'}")
        try:
            value = json.loads(body)
        except ValueError:
            return issues + ["response body is not valid JSON"]
        issues += [f"response {m}" for m in self.validate(media["schema"], value)]
        return issues


def _relax_https(node: Any) -> None:
    if isinstance(node, dict):
        if node.get("pattern") == "^https://":
            node["pattern"] = LOOPBACK_URL
        for value in node.values():
            _relax_https(value)
    elif isinstance(node, list):
        for value in node:
            _relax_https(value)


def _pointer(path) -> str:
    return "/" + "/".join(str(p) for p in path) if path else "/"


def _query(path: str) -> dict[str, str]:
    from urllib.parse import parse_qsl
    return dict(parse_qsl(path.split("?", 1)[1], keep_blank_values=True)) if "?" in path else {}


def _coerce(value: str, param: dict[str, Any]) -> Any:
    """Turn a header, path or query string into the parameter's JSON type."""
    t = param.get("schema", {}).get("type")
    if t == "integer":
        try:
            return int(value)
        except ValueError:
            return value
    if t == "number":
        try:
            return float(value)
        except ValueError:
            return value
    return value
