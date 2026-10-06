"""Build the contract from the TypeSpec source and JSON examples.

Run `npm ci` in typespec/ first. The script compiles TypeSpec, then writes:

- openapi/astrocollab.yaml, with examples taken from examples/manifest.json.
  Objects stay open: a receiver keeps or ignores fields it does not know;
- schemas/*.schema.json, standalone JSON Schemas for every type;
- spec/api.md, a Markdown REST reference.

Pass --check to fail when any committed output differs from a fresh build.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import yaml

from contract_docs import json_schemas, rest_reference

ROOT = Path(__file__).resolve().parents[1]
TYPESPEC = ROOT / "typespec"
EMITTED = TYPESPEC / "tsp-output/openapi.yaml"
TARGET = ROOT / "openapi/astrocollab.yaml"
SCHEMAS = ROOT / "schemas"
REFERENCE = ROOT / "spec/api.md"
HEADER = "# Generated from typespec/ by tools/build_openapi.py. Do not edit.\n"
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
        if "operationId" not in entry or entry["direction"] == "query":
            continue
        op = operations[entry["operationId"]]
        value = json.loads((examples / entry["file"]).read_text(encoding="utf-8"))
        if entry["direction"] == "request":
            # JSON bodies use application/json, except merge patches.
            media = next(iter(op["requestBody"]["content"].values()))
        else:
            media = op["responses"][str(entry["status"])]["content"]["application/json"]
        media["examples"] = {"example": {"value": value}}


def build() -> dict[Path, str]:
    """Return every generated file and its text."""
    subprocess.run(["npx", "--no-install", "tsp", "compile", "."], cwd=TYPESPEC, check=True,
                   stdout=subprocess.DEVNULL)
    doc = yaml.safe_load(EMITTED.read_text(encoding="utf-8"))
    add_examples(doc)
    outputs = {TARGET: HEADER + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100)}
    outputs.update({SCHEMAS / name: text for name, text in json_schemas(doc).items()})
    outputs[REFERENCE] = rest_reference(doc, ROOT / "examples")
    return outputs


def main() -> None:
    outputs = build()
    stale_schemas = {p for p in SCHEMAS.glob("*.json")} - set(outputs)
    if "--check" in sys.argv:
        stale = [p for p, text in outputs.items() if not p.exists() or p.read_text(encoding="utf-8") != text]
        stale += sorted(stale_schemas)
        if stale:
            names = ", ".join(str(p.relative_to(ROOT)) for p in stale)
            raise SystemExit(f"Generated files are stale ({names}); run python tools/build_openapi.py")
        print("OpenAPI, JSON Schemas and REST reference match the TypeSpec source.")
        return
    SCHEMAS.mkdir(exist_ok=True)
    for path in stale_schemas:
        path.unlink()
    for path, text in outputs.items():
        path.write_text(text, encoding="utf-8")
    print(f"Wrote OpenAPI, {len(outputs) - 2} schema files and the REST reference.")


if __name__ == "__main__":
    main()
