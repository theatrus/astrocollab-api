"""Build a self-contained static documentation site from the canonical spec."""
from __future__ import annotations

import html
import json
from pathlib import Path
import re
import shutil

import markdown
import yaml

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "_site"
GUIDES = {
    "walkthrough": "spec/walkthrough.md",
    "protocol": "spec/protocol.md",
    "conformance": "spec/conformance.md",
    "integration": "integrations/psf-guard.md",
    "changes": "spec/changes.md",
}


def build() -> None:
    DEST.mkdir(exist_ok=True)
    for name in ("styles.css", "app.js", "favicon.svg"):
        shutil.copyfile(ROOT / "site" / name, DEST / name)
    for folder in ("openapi", "examples", "tests"):
        target = DEST / folder
        target.mkdir(exist_ok=True)
        for source in (ROOT / folder).glob("*"):
            if source.is_file() and source.suffix in (".yaml", ".json"):
                shutil.copyfile(source, target / source.name)
    spec = yaml.safe_load((ROOT / "openapi/astrocollab.yaml").read_text(encoding="utf-8"))
    (DEST / "openapi/astrocollab.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    templates = []
    for slug, filename in GUIDES.items():
        source = ROOT / filename
        content = markdown.markdown(source.read_text(encoding="utf-8"), extensions=["tables", "fenced_code", "toc"])

        def rewrite(match: re.Match[str]) -> str:
            link = html.unescape(match[1])
            if re.match(r"^(https?:|mailto:)", link):
                return match[0]
            target, _, fragment = link.partition("#")
            absolute = (source.parent / target).resolve() if target else source
            for guide_slug, guide_path in GUIDES.items():
                if absolute == (ROOT / guide_path).resolve():
                    href = f"#guide/{guide_slug}" + (f"/{fragment}" if fragment else "")
                    return f'href="{html.escape(href, quote=True)}"'
            relative = absolute.relative_to(ROOT).as_posix()
            if absolute.is_file() and absolute.suffix in (".json", ".yaml"):
                return f'href="./{relative}"'
            return f'href="https://github.com/theatrus/astrocollab-api/tree/main/{relative}"'

        content = re.sub(r'href="([^"]+)"', rewrite, content)
        templates.append(f'<template id="guide-{slug}"><a class="source-link" href="https://github.com/theatrus/astrocollab-api/blob/main/{filename}">VIEW SOURCE ON GITHUB ↗</a>{content}</template>')
    index = (ROOT / "site/index.html").read_text(encoding="utf-8")
    operations = sum(len([m for m in item if m in {"get", "post", "put", "patch", "delete"}]) for item in spec["paths"].values())
    examples = json.loads((ROOT / "examples/manifest.json").read_text())
    replacements = {"__VERSION__": html.escape(spec["info"]["version"]), "__OPERATIONS__": str(operations), "__EXAMPLES__": str(len(examples)), "__GUIDE_TEMPLATES__": "\n".join(templates)}
    for key, value in replacements.items():
        index = index.replace(key, value)
    assert not re.search(r"__[A-Z_]+__", index), "Unreplaced build placeholder"
    (DEST / "index.html").write_text(index, encoding="utf-8")
    (DEST / ".nojekyll").write_text("", encoding="utf-8")
    print(f"Built documentation: {operations} operations, {len(examples)} examples, {len(GUIDES)} guides -> _site/")


if __name__ == "__main__":
    build()
