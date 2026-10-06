"""Check requests and responses against the AstroCollab 0.2 contract.

Every body is checked twice: against the OpenAPI document and against the
standalone JSON Schemas in schemas/, so a fault in either publication shows up.
The contract only describes shape. The rules that need state (dealing, judging,
duplicates) live in the server suite.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Mapping
from urllib.parse import parse_qsl

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012
import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "openapi/astrocollab.yaml"
DEFAULT_SCHEMAS = ROOT / "schemas"
METHODS = ("get", "put", "post", "delete", "patch")
REF_PREFIX = "#/components/schemas/"


@dataclass
class Operation:
    id: str
    method: str
    template: str
    regex: re.Pattern[str]
    spec: dict[str, Any]
    parameters: list[dict[str, Any]]
    security: list[dict[str, Any]]

    @property
    def needs_token(self) -> bool:
        """True unless the operation's security list allows no credential."""
        return {} not in self.security


def _lower(headers: Mapping[str, str] | None) -> dict[str, str]:
    return {k.lower(): v for k, v in (headers or {}).items()}


class Contract:
    def __init__(self, path: Path | str = DEFAULT_CONTRACT,
                 schema_dir: Path | str | None = DEFAULT_SCHEMAS):
        self.doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        self.components = copy.deepcopy(self.doc["components"])
        self.formats = FormatChecker()
        self.registry: Registry | None = None
        self.schema_ids: dict[str, str] = {}
        if schema_dir is not None and Path(schema_dir).is_dir():
            resources = []
            for file in sorted(Path(schema_dir).glob("*.schema.json")):
                schema = json.loads(file.read_text(encoding="utf-8"))
                self.schema_ids[file.name.removesuffix(".schema.json")] = schema["$id"]
                resources.append((schema["$id"], Resource.from_contents(
                    schema, default_specification=DRAFT202012)))
            self.registry = Registry().with_resources(resources)
        self.operations: list[Operation] = []
        for template, item in self.doc["paths"].items():
            shared = item.get("parameters", [])
            for method, spec in item.items():
                if method not in METHODS:
                    continue
                params = [self._resolve(p) for p in shared + spec.get("parameters", [])]
                pattern = "^" + re.sub(r"\\\{([^}]+)\\\}", r"(?P<\1>[^/]+)", re.escape(template)) + "$"
                self.operations.append(Operation(
                    spec["operationId"], method.upper(), template, re.compile(pattern), spec, params,
                    spec.get("security", self.doc.get("security", []))))
        # Literal segments win over templated ones.
        self.operations.sort(key=lambda op: -len(re.sub(r"\{[^}]+\}", "", op.template)))
        self.by_id = {op.id: op for op in self.operations}

    def _resolve(self, node: dict[str, Any]) -> dict[str, Any]:
        while "$ref" in node:
            target: Any = self.doc
            for part in node["$ref"][2:].split("/"):
                target = target[part]
            node = target
        return node

    # Matching and validation ------------------------------------------------

    def match(self, method: str, path: str) -> tuple[Operation, dict[str, str]] | None:
        """Match a path relative to the server's address, such as `/api/v1/agent/task`."""
        path = path.split("?", 1)[0]
        for op in self.operations:
            if op.method == method.upper():
                found = op.regex.match(path)
                if found:
                    return op, found.groupdict()
        return None

    @staticmethod
    def schema_name(schema: dict[str, Any]) -> str | None:
        ref = schema.get("$ref", "")
        return ref[len(REF_PREFIX):] if ref.startswith(REF_PREFIX) else None

    def validate(self, schema: dict[str, Any] | str, value: Any) -> list[str]:
        """Check a value against a contract schema, in both publications."""
        if isinstance(schema, str):
            schema = {"$ref": REF_PREFIX + schema}
        wrapped = {"allOf": [schema], "components": self.components}
        problems = [_describe(error) for error in
                    Draft202012Validator(wrapped, format_checker=self.formats).iter_errors(value)]
        name = self.schema_name(schema)
        if self.registry is not None and name in self.schema_ids:
            standalone = {"$ref": self.schema_ids[name]}
            validator = Draft202012Validator(standalone, registry=self.registry,
                                             format_checker=self.formats)
            problems += [f"schemas/{name}.schema.json: {_describe(error)}"
                         for error in validator.iter_errors(value)]
        elif self.registry is not None and name is not None:
            problems.append(f"schemas/{name}.schema.json is missing")
        return problems

    # Requests ---------------------------------------------------------------

    def check_request(self, method: str, path: str, headers: Mapping[str, str] | None,
                      body: bytes | None) -> list[str]:
        """Problems with a request a client sent."""
        found = self.match(method, path)
        if found is None:
            return [f"unknown route {method.upper()} {path.split('?')[0]}"]
        op, _ = found
        h = _lower(headers)
        issues: list[str] = []
        auth = h.get("authorization", "")
        if op.needs_token and not auth.lower().startswith("bearer "):
            issues.append("missing Authorization: Bearer header")
        query = dict(parse_qsl(path.split("?", 1)[1])) if "?" in path else {}
        for param in op.parameters:
            if param["in"] != "query":
                continue
            name = param["name"]
            if name not in query:
                if param.get("required"):
                    issues.append(f"missing query parameter {name}")
                continue
            value: Any = query[name]
            kind = param.get("schema", {}).get("type")
            if kind in ("number", "integer"):
                try:
                    value = float(value) if kind == "number" else int(value)
                except ValueError:
                    issues.append(f"query parameter {name} is not a {kind}")
                    continue
            issues += [f"query parameter {name}: {p}" for p in self.validate(param.get("schema", {}), value)]
        content = (op.spec.get("requestBody") or {}).get("content", {})
        media = content.get("application/json")
        if media is None:
            return issues
        if not body:
            if (op.spec.get("requestBody") or {}).get("required"):
                issues.append("missing request body")
            return issues
        if "json" not in h.get("content-type", ""):
            issues.append("request body without Content-Type: application/json")
        try:
            value = json.loads(body)
        except ValueError:
            return issues + ["request body is not JSON"]
        issues += [f"request body: {p}" for p in self.validate(media["schema"], value)]
        return issues

    # Responses --------------------------------------------------------------

    def check_response(self, method: str, path: str, status: int,
                       headers: Mapping[str, str] | None, body: bytes | None) -> list[str]:
        """Problems with a response a server sent."""
        found = self.match(method, path)
        if found is None:
            return []
        op, _ = found
        responses = op.spec.get("responses", {})
        spec = responses.get(str(status)) or responses.get("default")
        if spec is None:
            return [f"status {status} is not in the contract for {op.id}"]
        content = spec.get("content", {})
        media = content.get("application/json")
        if media is None:
            return []
        if not body:
            return ["empty body where the contract has one"]
        if "json" not in _lower(headers).get("content-type", ""):
            return ["body is not served as JSON"]
        try:
            value = json.loads(body)
        except ValueError:
            return ["body is not JSON"]
        return [f"body: {p}" for p in self.validate(media["schema"], value)]


def _describe(error: Any) -> str:
    where = "/".join(str(part) for part in error.absolute_path)
    return f"{where or '(root)'}: {error.message}"[:400]
