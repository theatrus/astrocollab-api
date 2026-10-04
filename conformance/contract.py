"""Check HTTP requests and responses against the AstroCollab OpenAPI contract.

The contract only describes message shape. Stateful rules (ETags, retries,
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
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012
import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "openapi/astrocollab.yaml"
DEFAULT_CATALOG = ROOT / "spec/codes.md"
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

    security: list[dict[str, Any]] | None = None

    @property
    def auth_required(self) -> bool:
        """True unless the operation's security list allows no credential."""
        return {} not in (self.security or [])

    def param(self, location: str, name: str) -> dict[str, Any] | None:
        for p in self.parameters:
            if p["in"] == location and p["name"].lower() == name.lower():
                return p
        return None


def _headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    return {k.lower(): v for k, v in (headers or {}).items()}


class Contract:
    def __init__(self, path: Path | str = DEFAULT_CONTRACT, allow_http_loopback: bool = False,
                 schema_dir: Path | str | None = None, catalog: Path | str | None = DEFAULT_CATALOG):
        """Load the OpenAPI contract. With `schema_dir`, validate named bodies against
        the standalone JSON Schemas there (such as schemas/) instead."""
        self.doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        self.components = copy.deepcopy(self.doc["components"])
        if allow_http_loopback:
            _relax_https(self.components)
        self.format_checker = FormatChecker()
        self.catalog = load_catalog(catalog) if catalog and Path(catalog).exists() else None
        self.registry = None
        self.schema_ids: dict[str, str] = {}
        if schema_dir is not None:
            resources = []
            for file in sorted(Path(schema_dir).glob("*.schema.json")):
                schema = json.loads(file.read_text(encoding="utf-8"))
                if allow_http_loopback:
                    _relax_https(schema)
                self.schema_ids[file.name.removesuffix(".schema.json")] = schema["$id"]
                resources.append((schema["$id"], Resource.from_contents(schema, default_specification=DRAFT202012)))
            self.registry = Registry().with_resources(resources)
        self.operations: list[Operation] = []
        for template, item in self.doc["paths"].items():
            shared = item.get("parameters", [])
            for method, spec in item.items():
                if method not in METHODS:
                    continue
                params = [self._resolve(p) for p in shared + spec.get("parameters", [])]
                pattern = "^" + re.sub(r"\\\{([^}]+)\\\}", r"(?P<\1>[^/]+)", re.escape(template)) + "$"
                self.operations.append(Operation(spec["operationId"], method.upper(), template,
                                                 re.compile(pattern), spec, params,
                                                 spec.get("security", self.doc.get("security", []))))
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
        name = self.schema_name(schema)
        if self.registry is not None and name in self.schema_ids:
            validator = Draft202012Validator({"$ref": self.schema_ids[name]}, registry=self.registry,
                                             format_checker=self.format_checker)
        else:
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
        if op.auth_required and not auth:
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
        media = content.get(ctype)
        if media is None:
            declared = " or ".join(content)
            issues.append(f"body must use Content-Type {declared}, got {ctype or 'none'}")
            media = next(iter(content.values()))
        try:
            value = json.loads(body)
        except ValueError:
            return issues + ["body is not valid JSON"]
        schema = media["schema"]
        issues += [f"body {m}" for m in self.validate(schema, value)]
        return issues

    # Catalog of codes (warnings only: servers MAY add codes)

    def catalog_warnings(self, status: int, body: bytes | None) -> list[str]:
        """Name problem codes, wait reasons and rejection reasons missing from spec/codes.md."""
        if self.catalog is None or not body:
            return []
        try:
            value = json.loads(body)
        except ValueError:
            return []
        found: list[tuple[str, str]] = []

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                if node.get("action") == "wait":
                    found.extend(("wait", c) for c in node.get("reason_codes", []))
                if node.get("state") == "rejected" and "artifact_id" in node:
                    found.extend(("rejection", c) for c in node.get("reason_codes", []))
                for child in node.values():
                    walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        if status >= 400 and isinstance(value, dict) and isinstance(value.get("code"), str):
            found.append(("error", value["code"]))
        else:
            walk(value)
        names = {"error": "error code", "wait": "wait reason", "rejection": "rejection reason"}
        return [f"{names[kind]} {code} is not in spec/codes.md" for kind, code in found
                if code not in self.catalog[kind]]

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


def load_catalog(path: Path | str = DEFAULT_CATALOG) -> dict[str, set[str]]:
    """Read spec/codes.md: error codes, check-in wait reasons and file rejection
    reasons, from the backticked names in each table's first column (the second
    column when the first holds an HTTP status)."""
    catalog: dict[str, set[str]] = {"error": set(), "wait": set(), "rejection": set()}
    section = None
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            heading = line.lower()
            section = ("error" if "error" in heading else "wait" if "wait" in heading
                       else "rejection" if "rejection" in heading else None)
        elif section and line.startswith("| `"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            cell = cells[1] if re.fullmatch(r"`\d{3}`", cells[0]) and len(cells) > 1 else cells[0]
            catalog[section].update(re.findall(r"`([a-z][a-z0-9_]*)`", cell))
    return catalog


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
