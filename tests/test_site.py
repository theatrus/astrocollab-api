"""Check generated navigation and source parity without claiming browser coverage."""
import json
from html.parser import HTMLParser
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "_site"


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.ids = set()

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if "id" in attrs:
            self.ids.add(attrs["id"])
        if tag in ("a", "link", "script"):
            link = attrs.get("href", attrs.get("src"))
            if link:
                self.links.append(link)


class DocumentationSiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = Links()
        cls.page.feed((SITE / "index.html").read_text(encoding="utf-8"))
        cls.spec = json.loads((SITE / "openapi/astrocollab.json").read_text(encoding="utf-8"))

    def test_contract_has_no_generated_drift(self):
        source = yaml.safe_load((ROOT / "openapi/astrocollab.yaml").read_text(encoding="utf-8"))
        self.assertEqual(source, self.spec)

    def test_navigation_and_download_targets_exist(self):
        operations = {operation["operationId"] for item in self.spec["paths"].values() for operation in item.values()}
        for link in self.page.links:
            with self.subTest(link=link):
                if link.startswith("#operation/"):
                    self.assertIn(link.split("/")[1], operations)
                elif link.startswith("#guide/"):
                    parts = link.split("/")
                    self.assertIn("guide-" + parts[1], self.page.ids)
                    if len(parts) > 2:
                        self.assertIn(parts[2], self.page.ids)
                elif link.startswith("./"):
                    self.assertTrue((SITE / link[2:]).is_file())

    def test_example_downloads_match_validated_sources(self):
        manifest = json.loads((ROOT / "examples/manifest.json").read_text())
        for example in manifest:
            filename = example["file"]
            with self.subTest(file=filename):
                self.assertEqual((ROOT / "examples" / filename).read_bytes(), (SITE / "examples" / filename).read_bytes())

    def test_runtime_assets_are_self_contained(self):
        for asset in ("site/styles.css", "site/app.js"):
            text = (ROOT / asset).read_text(encoding="utf-8")
            self.assertNotIn("cdn.", text)
            self.assertNotIn("@import", text)
        self.assertIn('href="./openapi/astrocollab.yaml"', (SITE / "index.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
