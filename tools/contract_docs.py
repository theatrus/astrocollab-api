"""Generate standalone JSON Schemas and a Markdown REST reference from the OpenAPI contract.

Both outputs work without OpenAPI tooling: the schemas are plain JSON Schema 2020-12
files that reference each other by relative path, and the reference is Markdown.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

SCHEMA_BASE = "https://theatrus.github.io/astrocollab-api/schemas/"
METHODS = ("get", "put", "patch", "post", "delete")
TAG_TITLES = {
    "capabilities": "Discovery",
    "pairing": "Pairing",
    "projects": "Projects",
    "rigs": "Rigs and assignments",
    "submissions": "Submissions and uploads",
}
STATUS_TEXT = {
    "200": "OK", "201": "Created", "202": "Accepted", "409": "Conflict",
    "429": "Too many requests", "default": "Error",
}
INLINE_EXAMPLE_LINES = 30
# Error codes each route can return, beyond 401, 429 and 5xx. See spec/codes.md.
ROUTE_CODES = {
    "pairClient": ["invalid_pairing_code", "invalid_request"],
    "listMyProjects": ["invalid_cursor", "invalid_limit"],
    "getProject": ["not_found"],
    "getProgress": ["not_found"],
    "registerEquipment": ["invalid_request", "invalid_range"],
    "updateEquipment": ["not_found", "invalid_request", "invalid_range"],
    "getEquipment": ["not_found"],
    "listEquipment": ["invalid_cursor", "invalid_limit"],
    "checkIn": ["not_found", "invalid_reference", "membership_inactive"],
    "createSubmission": [
        "membership_inactive", "id_conflict", "submission_deadline_passed", "payload_too_large",
        "too_many_artifacts", "artifact_too_large", "invalid_request", "invalid_reference",
        "invalid_revision", "duplicate_id", "invalid_supersede", "capture_deadline_passed",
        "deliverable_mismatch", "too_few_subs", "invalid_stack", "drizzle_not_allowed",
        "external_delivery_not_accepted",
    ],
    "putUploadPart": [
        "not_found", "part_conflict", "upload_expired", "upload_finalized", "invalid_part_number",
        "part_size_mismatch", "digest_mismatch",
    ],
    "getUpload": ["not_found"],
    "finalizeSubmission": [
        "not_found", "upload_incomplete", "terms_consent_required", "submission_deadline_passed",
    ],
    "getSubmission": ["not_found"],
}
# The order a client calls them in.
OP_ORDER = [
    "getCapabilities", "pairClient", "listMyProjects", "getProject", "getProgress",
    "registerEquipment", "updateEquipment", "getEquipment", "listEquipment", "checkIn",
    "createSubmission", "putUploadPart", "getUpload", "finalizeSubmission", "getSubmission",
]


def _schema_file(name: str) -> str:
    return f"{name}.schema.json"


def _relink(node: object) -> object:
    """Point component references at sibling schema files."""
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str) and value.startswith("#/components/schemas/"):
                out[key] = _schema_file(value.rsplit("/", 1)[1])
            else:
                out[key] = _relink(value)
        return out
    if isinstance(node, list):
        return [_relink(value) for value in node]
    return node


def json_schemas(doc: dict) -> dict[str, str]:
    """Return {file name: JSON text} for every component schema, plus an index."""
    files = {}
    names = sorted(doc["components"]["schemas"])
    for name in names:
        schema = _relink(copy.deepcopy(doc["components"]["schemas"][name]))
        head = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": SCHEMA_BASE + _schema_file(name),
            "title": name,
        }
        files[_schema_file(name)] = json.dumps({**head, **schema}, indent=2, ensure_ascii=False) + "\n"
    index = {
        "description": "AstroCollab JSON Schemas, generated from openapi/astrocollab.yaml.",
        "version": doc["info"]["version"],
        "schemas": {name: _schema_file(name) for name in names},
    }
    files["index.json"] = json.dumps(index, indent=2) + "\n"
    return files


def _ref_name(schema: dict) -> str | None:
    ref = schema.get("$ref", "")
    return ref.rsplit("/", 1)[1] if ref.startswith("#/components/schemas/") else None


def _schema_link(schema: dict) -> str:
    name = _ref_name(schema)
    if name:
        return f"[`{name}`](../schemas/{_schema_file(name)})"
    kind = schema.get("type", "value")
    return f"`{schema.get('format', kind)}`" if kind == "string" else f"`{kind}`"


def _key_rule(op: dict, doc: dict) -> str:
    security = op.get("security", doc.get("security", []))
    open_ = any(not entry for entry in security)
    keyed = any(entry for entry in security)
    if keyed and open_:
        return "Optional. Public projects can be read without a key."
    return "Required." if keyed else "None."


def _param_rows(op: dict, doc: dict) -> list[str]:
    rows = []
    for param in op.get("parameters", []):
        if "$ref" in param:
            param = doc["components"]["parameters"][param["$ref"].rsplit("/", 1)[1]]
        schema = param.get("schema", {})
        detail = param.get("description", "")
        rule = []
        for key in ("pattern", "minimum", "maximum", "default"):
            if key in schema:
                rule.append(f"{key} `{schema[key]}`")
        need = "required" if param.get("required") else "optional"
        rows.append(f"| `{param['name']}` | {param['in']} | {_schema_link(schema)}, {need} | "
                    f"{'; '.join(filter(None, [detail, ', '.join(rule)]))} |")
    return rows


def _example(path: Path, title: str) -> list[str]:
    text = path.read_text(encoding="utf-8")
    link = f"[{title}](../examples/{path.name})"
    if text.count("\n") > INLINE_EXAMPLE_LINES:
        return [f"Example: {link}.", ""]
    return [f"{title} ([file](../examples/{path.name})):", "", "```json", text.rstrip(), "```", ""]


def rest_reference(doc: dict, examples: Path) -> str:
    manifest = json.loads((examples / "manifest.json").read_text(encoding="utf-8"))
    by_op = {}
    for entry in manifest:
        if "operationId" in entry:
            by_op.setdefault(entry["operationId"], []).append(entry)
    server = doc["servers"][0]["url"]
    lines = [
        "# REST reference",
        "",
        "<!-- Generated from openapi/astrocollab.yaml by tools/build_openapi.py. Do not edit. -->",
        "",
        f"Version {doc['info']['version']}. Paths are relative to the API root from "
        f"`GET /capabilities`, for example `{server}`.",
        "",
        "Send `Authorization: Bearer <api key>` where a key is required. Bodies are JSON",
        "unless stated. Errors use `application/problem+json`; act on `code`. Every type",
        "links to a standalone [JSON Schema](../schemas/index.json), so you can validate",
        "payloads without OpenAPI tools. The [protocol](protocol.md) gives the rules",
        "behind each route, and [codes](codes.md) lists every error code and reason.",
        "",
        "| Method | Path | Purpose |",
        "| --- | --- | --- |",
    ]
    ops = [(path, method, op) for path, item in doc["paths"].items()
           for method, op in item.items() if method in METHODS]
    ops.sort(key=lambda o: OP_ORDER.index(o[2]["operationId"]) if o[2]["operationId"] in OP_ORDER else 99)
    for path, method, op in ops:
        lines.append(f"| `{method.upper()}` | [`{path}`](#{op['operationId'].lower()}) | {op['summary']} |")
    lines.append("")
    current = None
    for path, method, op in ops:
        tag = op["tags"][0]
        if tag != current:
            current = tag
            lines += [f"## {TAG_TITLES.get(tag, tag)}", ""]
        lines += [f'<a id="{op["operationId"].lower()}"></a>', "", f"### {op['summary']}", "",
                  f"`{method.upper()} {path}`", "", f"API key: {_key_rule(op, doc)}", ""]
        if op.get("description"):
            lines += [" ".join(op["description"].split()), ""]
        params = _param_rows(op, doc)
        if params:
            lines += ["| Name | In | Type | Notes |", "| --- | --- | --- | --- |", *params, ""]
        body = op.get("requestBody")
        if body:
            media_type, media = next(iter(body["content"].items()))
            lines += [f"Request body (`{media_type}`): {_schema_link(media['schema'])}", ""]
        lines += ["| Status | Body | Meaning |", "| --- | --- | --- |"]
        for status, response in op["responses"].items():
            content = response.get("content", {})
            body_text = ", ".join(f"{_schema_link(m['schema'])} (`{t}`)" for t, m in content.items()) or "None"
            meaning = " ".join(response.get("description", STATUS_TEXT.get(status, "")).split())
            lines.append(f"| `{status}` | {body_text} | {meaning} |")
        lines.append("")
        codes = ROUTE_CODES.get(op["operationId"])
        if codes:
            lines += ["Error codes: " + ", ".join(f"[`{c}`](codes.md)" for c in codes) + ".", ""]
        for entry in by_op.get(op["operationId"], []):
            title = "Request" if entry["direction"] == "request" else f"Response `{entry['status']}`"
            lines += _example(examples / entry["file"], title)
    return "\n".join(lines).rstrip() + "\n"
