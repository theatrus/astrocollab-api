"""Generate standalone JSON Schemas and a Markdown REST reference from the OpenAPI contract.

Both outputs work without OpenAPI tooling: the schemas are plain JSON Schema 2020-12
files that reference each other by relative path, and the reference is Markdown.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

SCHEMA_BASE = "https://astrocollabapi.com/schemas/"
METHODS = ("get", "put", "patch", "post", "delete")
TAG_TITLES = {
    "discovery": "Discovery",
    "signin": "Signing in",
    "telescopes": "Telescopes and their tokens",
    "telescope": "The telescope's night",
}
STATUS_TEXT = {"200": "OK", "401": "Unauthorized", "409": "Conflict", "422": "Invalid"}
INLINE_EXAMPLE_LINES = 30
#: Routes that take a person's token from sign-in. Every other private route takes a
#: telescope's agent token.
PERSON_ROUTES = {"authMe", "authLogout", "enrolTelescope", "listTelescopes"}
# The order a program calls them in.
OP_ORDER = [
    "health", "authStatus", "authLogin", "authPoll", "authMe", "authLogout",
    "enrolTelescope", "listTelescopes", "pairTelescope",
    "hello", "openProjects", "projectDepth", "joinProject", "tonight", "setTaskState", "report", "presence",
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


def _token_rule(op: dict, doc: dict) -> str:
    security = op.get("security", doc.get("security", []))
    if not any(entry for entry in security):
        return "None."
    if op["operationId"] in PERSON_ROUTES:
        return "A person's token, from signing in."
    return "The telescope's agent token."


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


def _example(examples: Path, name: str, title: str) -> list[str]:
    text = (examples / name).read_text(encoding="utf-8")
    link = f"[{title}](../examples/{name})"
    if text.count("\n") > INLINE_EXAMPLE_LINES:
        return [f"Example: {link}.", ""]
    return [f"{title} ([file](../examples/{name})):", "", "```json", text.rstrip(), "```", ""]


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
        f"Version {doc['info']['version']}. Paths are relative to the server's address, "
        f"for example `{server}`.",
        "",
        "Send `Authorization: Bearer <token>` where a token is needed: a telescope's agent",
        "token on telescope routes, a person's token on account routes. Bodies are JSON.",
        "Errors are `{\"detail\": ...}`: words for the operator on most errors, a list of",
        "bad fields on `422`. Objects are open: keep or ignore fields you do not know. Every",
        "type links to a standalone [JSON Schema](../schemas/index.json), so you can",
        "validate payloads without OpenAPI tools. The [protocol](protocol.md) gives the",
        "rules behind each route.",
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
                  f"`{method.upper()} {path}`", "", f"Token: {_token_rule(op, doc)}", ""]
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
        for entry in by_op.get(op["operationId"], []):
            title = {"request": "Request", "query": "Query"}.get(
                entry["direction"], f"Response `{entry.get('status')}`")
            lines += _example(examples, entry["file"], title)
    return "\n".join(lines).rstrip() + "\n"
