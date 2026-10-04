"""Build openapi/astrocollab.yaml from the TypeSpec source and JSON examples.

Run `npm ci` in typespec/ first. The script compiles TypeSpec, then:

- closes every object schema (servers reject unknown fields);
- adds request and response examples from examples/manifest.json, so each
  example lives in one file.

Pass --check to fail when the committed contract differs from a fresh build.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
TYPESPEC = ROOT / "typespec"
EMITTED = TYPESPEC / "tsp-output/openapi.yaml"
TARGET = ROOT / "openapi/astrocollab.yaml"
HEADER = "# Generated from typespec/ by tools/build_openapi.py. Do not edit.\n"
PROBLEM_EXAMPLES = {
    "default": "invalid-request.json",
    "409": "idempotency-conflict.json",
    "412": "precondition-failed.json",
    "428": "precondition-required.json",
    "429": "rate-limited.json",
}
# Operations whose typical conflict differs from the default.
CONFLICT_EXAMPLES = {"putUploadPart": "part-conflict.json", "finalizeSubmission": "terms-consent-required.json"}


def close_objects(node: object) -> None:
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            node.setdefault("additionalProperties", False)
        for value in node.values():
            close_objects(value)
    elif isinstance(node, list):
        for value in node:
            close_objects(value)


def add_examples(doc: dict) -> None:
    examples = ROOT / "examples"
    manifest = json.loads((examples / "manifest.json").read_text(encoding="utf-8"))
    operations = {
        op["operationId"]: op
        for item in doc["paths"].values()
        for method, op in item.items()
        if method in ("get", "put", "post", "delete", "patch")
    }
    for entry in manifest:
        if "operationId" not in entry:
            continue
        op = operations[entry["operationId"]]
        value = json.loads((examples / entry["file"]).read_text(encoding="utf-8"))
        if entry["direction"] == "request":
            media = op["requestBody"]["content"]["application/json"]
        else:
            media = op["responses"][str(entry["status"])]["content"]["application/json"]
        media["examples"] = {"example": {"value": value}}
    for oid, op in operations.items():
        for status, response in op["responses"].items():
            media = response.get("content", {}).get("application/problem+json")
            if media is None:
                continue
            name = PROBLEM_EXAMPLES.get(status, PROBLEM_EXAMPLES["default"])
            if status == "409":
                name = CONFLICT_EXAMPLES.get(oid, name)
            media["example"] = json.loads((examples / name).read_text(encoding="utf-8"))


def build() -> str:
    subprocess.run(["npx", "--no-install", "tsp", "compile", "."], cwd=TYPESPEC, check=True,
                   stdout=subprocess.DEVNULL)
    doc = yaml.safe_load(EMITTED.read_text(encoding="utf-8"))
    close_objects(doc["components"]["schemas"])
    for item in doc["paths"].values():
        close_objects(item)
    add_examples(doc)
    return HEADER + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100)


def main() -> None:
    text = build()
    if "--check" in sys.argv:
        if TARGET.read_text(encoding="utf-8") != text:
            raise SystemExit("openapi/astrocollab.yaml is stale; run python tools/build_openapi.py")
        print("OpenAPI contract matches the TypeSpec source.")
        return
    TARGET.write_text(text, encoding="utf-8")
    print(f"Wrote {TARGET.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
