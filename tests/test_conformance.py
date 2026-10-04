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
JSON = {"Content-Type": "application/json", "Authorization": "Bearer test-key",
        "Idempotency-Key": "00000000-0000-4000-8000-000000000090"}


def example(name: str) -> bytes:
    return (EXAMPLES / name).read_bytes()


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = Contract()
        cls.manifest = json.loads((EXAMPLES / "manifest.json").read_text())

    def test_matching_prefers_literal_routes(self):
        self.assertEqual(self.contract.match("GET", "/me/participations")[0].id, "listMyParticipations")
        op, params = self.contract.match("PUT", "/uploads/u1/parts/3")
        self.assertEqual((op.id, params), ("putUploadPart", {"upload_id": "u1", "part_number": "3"}))
        self.assertIsNone(self.contract.match("PATCH", "/projects"))

    def test_example_responses_pass(self):
        for entry in self.manifest:
            if entry.get("direction") != "response":
                continue
            op = self.contract.by_id[entry["operationId"]]
            path = op.template.replace("{part_number}", "1")
            for name in ("project_id", "participation_id", "equipment_id", "intent_id", "writer_id",
                         "submission_id", "upload_id", "snapshot_id", "job_id"):
                path = path.replace("{" + name + "}", "00000000-0000-4000-8000-000000000001")
            path = path.replace("{revision}", "1")
            with self.subTest(file=entry["file"]):
                issues = self.contract.check_response(op.method, path, entry["status"],
                                                      {"Content-Type": "application/json",
                                                       "Cache-Control": "no-store"},
                                                      example(entry["file"]))
                self.assertEqual(issues, [])

    def test_example_request_passes(self):
        issues = self.contract.check_request("POST", "/projects", JSON, example("createProject.request.json"))
        self.assertEqual(issues, [])

    def test_pairing_is_public_and_not_idempotent(self):
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
        issues = self.contract.check_request("POST", "/projects", {}, b"{}")
        self.assertIn("missing Authorization header", issues)
        self.assertIn("missing Idempotency-Key header", issues)
        self.assertTrue(any("'requirements' is a required property" in i for i in issues))
        self.assertEqual(self.contract.check_request("GET", "/nowhere", {}, b""), ["unknown route GET /nowhere"])

    def test_create_or_replace_needs_one_precondition(self):
        path = "/participations/00000000-0000-4000-8000-000000000001/equipment/00000000-0000-4000-8000-000000000002"
        body = example("registerEquipment.request.json")
        headers = {"Content-Type": "application/json", "Authorization": "Bearer k"}
        self.assertIn("send exactly one of If-Match or If-None-Match: *",
                      self.contract.check_request("PUT", path, headers, body))
        self.assertEqual(self.contract.check_request("PUT", path, dict(headers, **{"If-None-Match": "*"}), body), [])

    def test_part_digest_and_length(self):
        path = "/uploads/00000000-0000-4000-8000-000000000001/parts/1"
        headers = {"Authorization": "Bearer k", "Content-Type": "application/octet-stream",
                   "Content-Length": "3", "X-Part-SHA256": "0" * 64}
        self.assertIn("X-Part-SHA256 does not match the part bytes",
                      self.contract.check_request("PUT", path, headers, b"abc"))

    def test_error_responses_need_problem_json(self):
        issues = self.contract.check_response("GET", "/capabilities", 500, {"Content-Type": "text/plain"}, b"oops")
        self.assertTrue(issues)
        problem = example("terms-consent-required.json")
        self.assertEqual(self.contract.check_response(
            "POST", "/submissions/00000000-0000-4000-8000-000000000001/finalize", 409,
            {"Content-Type": "application/problem+json"}, problem), [])
        self.assertIn("problem status 409 differs from HTTP 400", self.contract.check_response(
            "GET", "/capabilities", 400, {"Content-Type": "application/problem+json"}, problem))

    def test_loopback_urls_need_the_option(self):
        value = json.loads(example("getCapabilities.response.json"))
        value["api_root"] = "http://127.0.0.1:8080/v1"
        body = json.dumps(value).encode()
        headers = {"Content-Type": "application/json"}
        self.assertTrue(self.contract.check_response("GET", "/capabilities", 200, headers, body))
        relaxed = Contract(allow_http_loopback=True)
        self.assertEqual(relaxed.check_response("GET", "/capabilities", 200, headers, body), [])
        value["api_root"] = "http://collab.example/v1"
        self.assertTrue(relaxed.check_response("GET", "/capabilities", 200, headers, json.dumps(value).encode()))


def reference_serve():
    try:
        from reference.server import serve
    except ImportError:
        return None
    return serve


KEYS = {"owner": "owner-test-key", "alice": "alice-test-key", "bob": "bob-test-key"}


@unittest.skipIf(reference_serve() is None, "reference/server.py is not available")
class ReferenceServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = reference_serve()(host="127.0.0.1", port=0, keys=KEYS)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.server.server_address[:2]
        cls.api_root = f"http://{host}:{port}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_server_suite_passes(self):
        args = ["--api-root", self.api_root, "--owner-key", KEYS["owner"], "--participant-key", KEYS["alice"],
                "--second-participant-key", KEYS["bob"], "--allow-http-loopback"]
        issue = getattr(getattr(self.server, "api", None), "issue_pairing_code", None)
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
                                  "--owner-key", KEYS["owner"], "--participant-key", KEYS["alice"]],
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
