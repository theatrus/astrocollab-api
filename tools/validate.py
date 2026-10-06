"""Validate the draft contract and payload fixtures, not a running service."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re

from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate
from referencing import Registry, Resource
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
    examples = ROOT / "examples"
    # setup/ holds what the capture script needed to set a night up (coordinator calls,
    # outside the protocol); extra/ holds its own manifest, merged into the main one.
    actual = {p.relative_to(examples).as_posix() for p in examples.rglob("*.json")
              if p.parts[len(examples.parts)] not in ("setup",)
              and p.name != "manifest.json"}
    assert len(registered) == len(manifest), "Duplicate fixture registration"
    assert actual == registered, f"Unregistered/missing fixtures: {actual ^ registered}"
    fixtures = {}
    coverage = {}
    for entry in manifest:
        value = json.loads((ROOT / "examples" / entry["file"]).read_text())
        if entry.get("schema"):
            assert_payload(entry["schema"], value, entry["file"])
        fixtures[entry["file"]] = (entry.get("schema"), value)
        if "operationId" in entry and entry["direction"] != "query":
            key = (entry["operationId"], entry["direction"], entry.get("status"))
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
            # Every operation states whether it needs an API key.
            assert "security" in operation or "security" in DOC, oid
            parameters = [
                DOC["components"]["parameters"][p["$ref"].split("/")[-1]]
                if "$ref" in p else p for p in operation.get("parameters", [])
            ]
            declared = {p["name"] for p in parameters if p["in"] == "path"}
            assert declared == set(re.findall(r"{([^}]+)}", path)), oid
            # Mutations are safe to retry by design; the protocol has no idempotency keys.
            assert not any(p["name"] == "Idempotency-Key" for p in parameters), oid
            payloads = []
            if "requestBody" in operation:
                content = (operation["requestBody"]["content"].get("application/json")
                           or operation["requestBody"]["content"].get("application/merge-patch+json"))
                if content:
                    payloads.append(("request", None, content))
            for status, response in operation["responses"].items():
                if not status.startswith("2"):
                    continue
                content = response.get("content", {}).get("application/json")
                if content:
                    payloads.append(("response", int(status), content))
            for direction, status, content in payloads:
                entry = coverage.get((oid, direction, status))
                assert entry, f"No example for {oid} {direction} {status or ''}"
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

    # The standalone JSON Schemas must accept every example without OpenAPI tooling.
    standalone = {}
    for file in (ROOT / "schemas").glob("*.schema.json"):
        schema = json.loads(file.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        standalone[schema["$id"]] = Resource.from_contents(schema)
    registry = Registry().with_resources(standalone.items())
    base = "https://astrocollabapi.com/schemas/"
    for entry in manifest:
        if not entry.get("schema"):
            continue
        schema = standalone[f"{base}{entry['schema']}.schema.json"].contents
        value = fixtures[entry["file"]][1]
        errors = list(Draft202012Validator(schema, registry=registry, format_checker=FORMATS).iter_errors(value))
        assert not errors, f"{entry['file']} fails standalone schema: {errors[0].message}"

    # Local Markdown link checks; external URLs are references, not fetched by CI.
    for file in ROOT.rglob("*.md"):
        if {".venv", "node_modules", "starfront"} & set(file.relative_to(ROOT).parts):
            continue
        for link in re.findall(r"\]\(([^)]+)\)", file.read_text(encoding="utf-8")):
            target = link.split("#")[0]
            if not target or "://" in target:
                continue
            assert (file.parent / target).exists(), f"Broken link: {file}: {target}"

    print(f"Validated OpenAPI, {len(operation_ids)} operations, {len(SCHEMAS)} schemas "
          f"(also as standalone JSON Schemas), "
          f"{len(manifest)} payloads and {len(negatives)} rejection fixtures.")
    print("Stateful authorization, concurrency and astronomy remain implementation conformance work.")


if __name__ == "__main__":
    main()
