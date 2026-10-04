"""Unit tests for the conformance tester, and a run against the reference server
when reference/ provides one."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import threading
import unittest

from conformance.contract import Contract
from conformance import server_suite
from conformance.proxy import make_proxy

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
JSON = {"Content-Type": "application/json", "Authorization": "Bearer test-key"}


def example(name: str) -> bytes:
    return (EXAMPLES / name).read_bytes()


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = Contract()
        cls.manifest = json.loads((EXAMPLES / "manifest.json").read_text())

    def test_matching_prefers_literal_routes(self):
        self.assertEqual(self.contract.match("GET", "/me/equipment")[0].id, "listEquipment")
        op, params = self.contract.match("PUT", "/uploads/u1/parts/3")
        self.assertEqual((op.id, params), ("putUploadPart", {"upload_id": "u1", "part_number": "3"}))
        self.assertIsNone(self.contract.match("PATCH", "/projects"))

    def test_example_responses_pass(self):
        for entry in self.manifest:
            if entry.get("direction") != "response":
                continue
            op = self.contract.by_id[entry["operationId"]]
            path = op.template.replace("{part_number}", "1")
            for name in ("project_id", "participation_id", "equipment_id", "submission_id", "upload_id",
                         "snapshot_id", "artifact_id"):
                path = path.replace("{" + name + "}", "00000000-0000-4000-8000-000000000001")
            path = path.replace("{revision}", "1")
            with self.subTest(file=entry["file"]):
                issues = self.contract.check_response(op.method, path, entry["status"],
                                                      {"Content-Type": "application/json",
                                                       "Cache-Control": "no-store"},
                                                      example(entry["file"]))
                self.assertEqual(issues, [])

    def test_example_request_passes(self):
        path = "/projects/00000000-0000-4000-8000-000000000001/submissions"
        issues = self.contract.check_request("POST", path, JSON, example("createSubmission.request.json"))
        self.assertEqual(issues, [])

    def test_pairing_needs_no_credential(self):
        body = example("pairClient.request.json")
        self.assertEqual(self.contract.check_request("POST", "/pair", {"Content-Type": "application/json"}, body), [])
        paired = example("pairClient.response.json") if (EXAMPLES / "pairClient.response.json").exists() else None
        if paired is not None:
            headers = {"Content-Type": "application/json"}
            self.assertIn("missing Cache-Control response header",
                          self.contract.check_response("POST", "/pair", 201, headers, paired))
            self.assertEqual(self.contract.check_response("POST", "/pair", 201,
                                                          dict(headers, **{"Cache-Control": "no-store"}), paired), [])

    def test_request_faults(self):
        path = "/projects/00000000-0000-4000-8000-000000000001/submissions"
        issues = self.contract.check_request("POST", path, {}, b"{}")
        self.assertIn("missing Authorization header", issues)
        self.assertTrue(any("'artifacts' is a required property" in i for i in issues))
        public = "/projects/00000000-0000-4000-8000-000000000001/progress"
        self.assertEqual(self.contract.check_request("GET", public, {}, b""), [])
        self.assertEqual(self.contract.check_request("GET", "/nowhere", {}, b""), ["unknown route GET /nowhere"])

    def test_equipment_put_needs_no_precondition(self):
        path = "/me/equipment/00000000-0000-4000-8000-000000000002"
        headers = {"Content-Type": "application/json", "Authorization": "Bearer k"}
        self.assertEqual(self.contract.check_request("PUT", path, headers,
                                                     example("registerEquipment.request.json")), [])

    def test_merge_patch_media_type(self):
        path = "/me/equipment/00000000-0000-4000-8000-000000000002"
        body = json.dumps({"focal_length_mm": 402.5}).encode()
        headers = {"Authorization": "Bearer k", "Content-Type": "application/merge-patch+json"}
        self.assertEqual(self.contract.check_request("PATCH", path, headers, body), [])
        headers["Content-Type"] = "application/json"
        self.assertTrue(self.contract.check_request("PATCH", path, headers, body))

    def test_passband_matching(self):
        ha = {"name": "H-alpha", "center_nm": 656.3, "width_nm": 7.0}
        self.assertTrue(server_suite.band_matches(dict(ha, width_nm=3.0), ha))
        self.assertFalse(server_suite.band_matches(dict(ha, width_nm=12.0), ha))
        self.assertFalse(server_suite.band_matches({"name": "OIII", "center_nm": 500.7, "width_nm": 3.0}, ha))
        self.assertTrue(server_suite.band_matches({"name": "Ha", "center_nm": 656.0}, ha))
        self.assertFalse(server_suite.band_matches(dict(ha, width_nm=1.0), ha))
        self.assertTrue(server_suite.band_matches(dict(ha, center_nm=660.0), {"name": "Ha", "center_nm": 656.3}))
        self.assertFalse(server_suite.band_matches(dict(ha, center_nm=662.0), {"name": "Ha", "center_nm": 656.3}))

    def test_standalone_schemas_agree(self):
        schemas = ROOT / "schemas"
        if not schemas.is_dir():
            self.skipTest("schemas/ is not generated")
        standalone = Contract(schema_dir=schemas)
        for entry in self.manifest:
            with self.subTest(file=entry["file"]):
                value = json.loads(example(entry["file"]))
                self.assertEqual(standalone.validate(entry["schema"], value), [])
                broken = dict(value, unknown_field=1) if isinstance(value, dict) else value
                self.assertEqual(bool(standalone.validate(entry["schema"], broken)),
                                 bool(self.contract.validate(entry["schema"], broken)))

    def test_field_of_view(self):
        rig = json.loads(example("registerEquipment.request.json"))
        width, height = server_suite.field_of_view(rig)
        self.assertAlmostEqual(width, 3.36, delta=0.01)
        self.assertAlmostEqual(height, 2.25, delta=0.01)

    def test_part_digest_and_length(self):
        path = "/uploads/00000000-0000-4000-8000-000000000001/parts/1"
        headers = {"Authorization": "Bearer k", "Content-Type": "application/octet-stream",
                   "Content-Length": "3", "X-Part-SHA256": "0" * 64}
        self.assertIn("X-Part-SHA256 does not match the part bytes",
                      self.contract.check_request("PUT", path, headers, b"abc"))

    def test_error_responses_need_problem_json(self):
        issues = self.contract.check_response("GET", "/capabilities", 500, {"Content-Type": "text/plain"}, b"oops")
        self.assertTrue(issues)
        problem = example("id-conflict.json")
        self.assertEqual(self.contract.check_response(
            "POST", "/submissions/00000000-0000-4000-8000-000000000001/finalize", 409,
            {"Content-Type": "application/problem+json"}, problem), [])
        self.assertIn("problem status 409 differs from HTTP 400", self.contract.check_response(
            "GET", "/capabilities", 400, {"Content-Type": "application/problem+json"}, problem))

    def test_loopback_urls(self):
        value = json.loads(example("getCapabilities.response.json"))
        headers = {"Content-Type": "application/json"}
        value["api_root"] = "http://127.0.0.1:8080/v1"
        self.assertEqual(self.contract.check_response("GET", "/capabilities", 200, headers,
                                                      json.dumps(value).encode()), [])
        value["api_root"] = "http://collab.example/v1"
        self.assertTrue(self.contract.check_response("GET", "/capabilities", 200, headers,
                                                     json.dumps(value).encode()))
        terms = {"id": "00000000-0000-4000-8000-000000000019", "version": 1,
                 "url": "http://127.0.0.1:8080/terms/1", "sha256": "b" * 64}
        self.assertTrue(self.contract.validate("Terms", terms))
        self.assertEqual(Contract(allow_http_loopback=True).validate("Terms", terms), [])


def reference_serve():
    try:
        from reference.server import serve
    except ImportError:
        return None
    return serve


KEYS = {"alice": "alice-test-key", "bob": "bob-test-key"}


def classify(api_root: str, project_ids: list[str]) -> dict[str, str]:
    """Pick sample projects for each suite flag by reading their requirements."""
    import urllib.request
    picked = {}
    for project_id in project_ids:
        request = urllib.request.Request(f"{api_root}/projects/{project_id}",
                                         headers={"Authorization": f"Bearer {KEYS['alice']}"})
        with urllib.request.urlopen(request) as response:
            requirements = json.load(response)["requirements"]
        if requirements.get("deliverable") == "stacked_masters":
            picked.setdefault("--masters-project-id", project_id)
        elif requirements.get("external_delivery"):
            picked.setdefault("--external-project-id", project_id)
        else:
            picked.setdefault("--project-id", project_id)
    return picked


@unittest.skipIf(reference_serve() is None, "reference/server.py is not available")
class ReferenceServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = reference_serve()(host="127.0.0.1", port=0, keys=KEYS)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.server.server_address[:2]
        cls.api_root = f"http://{host}:{port}/v1"
        cls.projects = classify(cls.api_root, list(getattr(cls.server, "sample_project_ids", [])))

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_server_suite_passes(self):
        if "--project-id" not in self.projects:
            self.skipTest("reference server has no sample project that takes calibrated subs")
        args = ["--api-root", self.api_root, "--participant-key", KEYS["alice"],
                "--second-participant-key", KEYS["bob"], "--allow-http-loopback"]
        for flag, project_id in self.projects.items():
            args += [flag, project_id]
        issue = getattr(self.server, "issue_pairing_code", None)
        if issue is not None:
            args += ["--pairing-code", issue("bob"), "--second-pairing-code", issue("bob")]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status = server_suite.main(args)
        self.assertEqual(status, 0, out.getvalue())

    def test_reference_client_has_no_faults(self):
        try:
            import reference.client  # noqa: F401
        except ImportError:
            self.skipTest("reference/client.py is not available")
        proxy, recorder = make_proxy(("127.0.0.1", 0), self.api_root, Contract(allow_http_loopback=True))
        thread = threading.Thread(target=proxy.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = proxy.server_address[:2]
            run = subprocess.run([sys.executable, "-m", "reference.client", "--api-root", f"http://{host}:{port}/v1",
                                  "--key", KEYS["alice"]],
                                 cwd=ROOT, capture_output=True, text=True, timeout=300)
        finally:
            proxy.shutdown()
            proxy.server_close()
        report = recorder.report()
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertGreater(report["requests"], 0)
        self.assertEqual(report["client_faults"], [], json.dumps(report, indent=2))
        self.assertEqual(report["server_faults"], [], json.dumps(report, indent=2))


if __name__ == "__main__":
    unittest.main()
