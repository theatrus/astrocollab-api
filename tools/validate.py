"""Validate the draft contract and payload fixtures, not a running service."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re

from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate
import yaml

ROOT = Path(__file__).resolve().parents[1]
DOC = yaml.safe_load((ROOT / "openapi/astrocollab.yaml").read_text(encoding="utf-8"))
SCHEMAS = DOC["components"]["schemas"]
FORMATS = FormatChecker()


def validator(name: str) -> Draft202012Validator:
    # Local component references resolve against this document; no network reads.
    return Draft202012Validator(
        {"$ref": f"#/components/schemas/{name}", "components": DOC["components"]},
        format_checker=FORMATS,
    )


def assert_payload(name: str, value: object, label: str) -> None:
    errors = list(validator(name).iter_errors(value))
    if errors:
        details = "\n".join(f"  {list(e.absolute_path)}: {e.message}" for e in errors)
        raise AssertionError(f"{label} does not match {name}:\n{details}")


def check_refs(value: object) -> None:
    if isinstance(value, dict):
        if "$ref" in value:
            ref = value["$ref"]
            assert ref.startswith("#/"), f"Nonlocal reference: {ref}"
            node = DOC
            for part in ref[2:].split("/"):
                node = node[part.replace("~1", "/").replace("~0", "~")]
        for item in value.values():
            check_refs(item)
    elif isinstance(value, list):
        for item in value:
            check_refs(item)


def main() -> None:
    validate(DOC)
    check_refs(DOC)
    for schema in SCHEMAS.values():
        Draft202012Validator.check_schema(schema)

    manifest = json.loads((ROOT / "examples/manifest.json").read_text())
    registered = {entry["file"] for entry in manifest}
    actual = {p.name for p in (ROOT / "examples").glob("*.json")} - {"manifest.json"}
    assert len(registered) == len(manifest), "Duplicate fixture registration"
    assert actual == registered, f"Unregistered/missing fixtures: {actual ^ registered}"
    fixtures = {}
    coverage = {}
    for entry in manifest:
        value = json.loads((ROOT / "examples" / entry["file"]).read_text())
        assert_payload(entry["schema"], value, entry["file"])
        fixtures[entry["file"]] = (entry["schema"], value)
        if "operationId" in entry:
            key = (entry["operationId"], entry["direction"])
            assert key not in coverage, f"Duplicate operation fixture: {key}"
            coverage[key] = entry

    operation_ids = set()
    methods = {"get", "put", "post", "patch", "delete"}
    for path, path_item in DOC["paths"].items():
        for method, operation in path_item.items():
            if method not in methods:
                continue
            oid = operation["operationId"]
            assert oid not in operation_ids, f"Duplicate operation ID: {oid}"
            operation_ids.add(oid)
            assert operation["x-token-context"] in {"public", "public_or_project", "public_or_account", "account_or_project", "account", "project"}
            assert "x-required-scopes" in operation
            parameters = [
                DOC["components"]["parameters"][p["$ref"].split("/")[-1]]
                if "$ref" in p else p for p in operation.get("parameters", [])
            ]
            declared = {p["name"] for p in parameters if p["in"] == "path"}
            assert declared == set(re.findall(r"{([^}]+)}", path)), oid
            # Pairing codes are single use; a replayed pairing response would leak a key.
            if method == "post" and oid != "pairClient":
                assert any(p["name"] == "Idempotency-Key" and p["required"] for p in parameters), oid
            payloads = []
            if "requestBody" in operation:
                content = operation["requestBody"]["content"].get("application/json")
                if content:
                    payloads.append(("request", content))
            for status, response in operation["responses"].items():
                if not status.startswith("2"):
                    continue
                content = response.get("content", {}).get("application/json")
                if content:
                    payloads.append(("response", content))
            for direction, content in payloads:
                entry = coverage[(oid, direction)]
                schema_name = content["schema"]["$ref"].split("/")[-1]
                assert entry["schema"] == schema_name, (oid, direction)
                value = fixtures[entry["file"]][1]
                assert content["examples"]["example"]["value"] == value, f"Inline example drift: {oid}"
                assert_payload(schema_name, value, oid)

    negatives = json.loads((ROOT / "tests/invalid-payloads.json").read_text())
    for case in negatives:
        schema, original = fixtures[case["base"]]
        value = copy.deepcopy(original)
        for mutation in case["mutations"]:
            parts = mutation["path"]
            node = value
            for part in parts[:-1]:
                node = node[part]
            if mutation["op"] == "remove":
                del node[parts[-1]]
            else:
                assert mutation["op"] == "set"
                node[parts[-1]] = mutation["value"]
        errors = list(validator(schema).iter_errors(value))
        assert errors, f"Invalid fixture unexpectedly accepted: {case['name']}"
        assert any(e.validator == case["expected_validator"] for e in errors), (
            case["name"], [(e.validator, e.message) for e in errors]
        )

    # Local Markdown link checks; external URLs are references, not fetched by CI.
    for file in ROOT.rglob("*.md"):
        if ".venv" in file.parts or "node_modules" in file.parts:
            continue
        for link in re.findall(r"\]\(([^)]+)\)", file.read_text(encoding="utf-8")):
            target = link.split("#")[0]
            if not target or "://" in target:
                continue
            assert (file.parent / target).exists(), f"Broken link: {file}: {target}"

    print(f"Validated OpenAPI, {len(operation_ids)} operations, {len(SCHEMAS)} schemas, "
          f"{len(manifest)} payloads and {len(negatives)} rejection fixtures.")
    print("Stateful authorization, concurrency and astronomy remain implementation conformance work.")


if __name__ == "__main__":
    main()
