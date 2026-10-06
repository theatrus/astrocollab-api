"""The conformance tester: unit tests for its contract checks, then the whole suite run
against our reference server and against Starfront's server.

Each server's test projects are made with that server's own tools, which are outside
the protocol: our reference server's Python tools, and Starfront's coordinator route
with its admin token. The suite itself only uses the contract's routes.

Starfront is looked for at STARFRONT_DIR, or beside this repository as ../starfront. It
runs under STARFRONT_PYTHON, or its own .venv, or this Python; it needs FastAPI and
uvicorn. Without them the Starfront test is skipped, unless STARFRONT_REQUIRED=1, as in
CI, where it fails instead.
"""
from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from conformance.contract import Contract  # noqa: E402
from conformance import server_suite  # noqa: E402
from conformance.proxy import make_proxy  # noqa: E402

EXAMPLES = ROOT / "examples"

#: The projects every server is tested with: a mosaic wanting H and O, and one object
#: in L, R, G and B. Filter names are written as people write them, not as letters.
MOSAIC = {
    "name": "Conformance mosaic",
    "region": {"ra": 10.6847, "dec": 41.269, "width": 7.0, "height": 4.5, "rotation": 0.0},
    "kind": "mosaic",
    "goals": {"Ha": 10.0, "OIII": 12.0},
    "requirements": {"filters": {"Ha": 7.0, "OIII": 7.0}, "maxHfr": 3.5, "maxGuideRms": 1.5,
                     "minExposure": 120.0, "maxExposure": 600.0, "minFramesPerVisit": 10,
                     "minMoonSeparation": 30.0, "minAltitude": 30.0},
}
SINGLE = {
    "name": "Conformance single",
    "region": {"ra": 202.4696, "dec": 47.1952, "width": 0.25, "height": 0.2, "rotation": 0.0},
    "kind": "single",
    "goals": {"L": 5.0, "R": 2.0, "G": 2.0, "B": 2.0},
    "requirements": {"filters": {"L": None, "R": None, "G": None, "B": None},
                     "minExposure": 60.0, "maxExposure": 600.0},
}


def example(name: str) -> bytes:
    return (EXAMPLES / name).read_bytes()


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def run_suite(server: str, extra: list[str]) -> tuple[int, str]:
    lines: list[str] = []
    code = server_suite.main(["--server", server, *extra], out=lines.append)
    return code, "\n".join(lines)


# ---------------------------------------------------------------------------
# The contract layer
# ---------------------------------------------------------------------------

class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = Contract()
        cls.manifest = json.loads((EXAMPLES / "manifest.json").read_text())

    def path_for(self, operation_id: str) -> str:
        op = self.contract.by_id[operation_id]
        return op.template.replace("{project_id}", "000000000002").replace("{task_id}", "000000000004")

    def test_matching(self):
        self.assertEqual(self.contract.match("GET", "/api/v1/agent/task")[0].id, "tonight")
        op, params = self.contract.match("POST", "/api/v1/agent/task/000000000004")
        self.assertEqual((op.id, params), ("setTaskState", {"task_id": "000000000004"}))
        self.assertEqual(self.contract.match("POST", "/api/v1/agent/projects/abc/join")[0].id, "joinProject")
        self.assertIsNone(self.contract.match("DELETE", "/api/v1/agents"))

    def test_every_example_passes_both_publications(self):
        for entry in self.manifest:
            if entry.get("direction") == "query" or not entry.get("operationId"):
                continue
            path = self.path_for(entry["operationId"])
            op = self.contract.by_id[entry["operationId"]]
            with self.subTest(file=entry["file"]):
                if entry["direction"] == "request":
                    headers = {"Content-Type": "application/json", "Authorization": "Bearer t"}
                    issues = self.contract.check_request(op.method, path, headers, example(entry["file"]))
                else:
                    issues = self.contract.check_response(op.method, path, entry["status"],
                                                          {"Content-Type": "application/json"},
                                                          example(entry["file"]))
                self.assertEqual(issues, [])

    def test_the_standalone_schemas_are_used(self):
        self.assertIn("HelloRequest", self.contract.schema_ids)
        broken = json.loads(example("hello.request.json"))
        broken["profile"]["focalLength"] = "530 mm"
        issues = self.contract.validate("HelloRequest", broken)
        self.assertTrue(any(i.startswith("schemas/HelloRequest") for i in issues), issues)
        self.assertTrue(any(not i.startswith("schemas/") for i in issues), issues)

    def test_request_faults(self):
        issues = self.contract.check_request("GET", "/api/v1/agent/task?night=2026-10-05", {}, b"")
        self.assertIn("missing Authorization: Bearer header", issues)
        bad = self.contract.check_request("GET", "/api/v1/agent/task?moon=2", {"Authorization": "Bearer t"}, b"")
        self.assertTrue(any("moon" in i for i in bad), bad)
        body = json.dumps({"state": "maybe"}).encode()
        wrong = self.contract.check_request("POST", "/api/v1/agent/task/000000000004",
                                            {"Authorization": "Bearer t", "Content-Type": "application/json"}, body)
        self.assertTrue(any("request body" in i for i in wrong), wrong)
        self.assertEqual(self.contract.check_request("GET", "/api/v1/health", {}, b""), [])
        self.assertEqual(self.contract.check_request("GET", "/nowhere", {}, b""), ["unknown route GET /nowhere"])

    def test_undocumented_status_is_a_server_fault(self):
        issues = self.contract.check_response("POST", "/api/v1/agent/projects/000000000002/join", 418,
                                              {"Content-Type": "application/json"}, b'{"detail": "x"}')
        self.assertEqual(issues, ["status 418 is not in the contract for joinProject"])

    def test_filter_folding(self):
        for name, letter in (("Ha 3nm", "H"), ("OIII", "O"), ("SII (3 nm)", "S"), ("Lum", "L"),
                             ("luminance", "L"), ("Dual band", "Dual band")):
            self.assertEqual(server_suite.fold(name), letter)


# ---------------------------------------------------------------------------
# Our reference server
# ---------------------------------------------------------------------------

def reference_serve():
    """The reference server's `serve`, once it speaks 0.2; None until then."""
    try:
        from reference.server import serve
    except Exception:
        return None
    import inspect
    if "sample" not in inspect.signature(serve).parameters:
        return None
    return serve


@unittest.skipIf(reference_serve() is None, "reference/server.py does not serve 0.2 yet")
class ReferenceServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        serve = reference_serve()
        cls.httpd = serve(host="127.0.0.1", port=0, sample=True)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.httpd.server_address[:2]
        cls.server = f"http://{host}:{port}"
        tools = cls.httpd.tools
        cls.mosaic = tools.create_project(MOSAIC["name"], MOSAIC["region"], MOSAIC["kind"],
                                          MOSAIC["goals"], MOSAIC["requirements"])
        cls.single = tools.create_project(SINGLE["name"], SINGLE["region"], SINGLE["kind"],
                                          SINGLE["goals"], SINGLE["requirements"])
        cls.tools = tools

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_suite_passes_with_sign_in(self):
        person = self.tools.sign_in("Conformance person")
        code = self.tools.issue_pairing_code("Conformance person")
        # Tokens and codes may start with "-", so they go after "=".
        status, output = run_suite(self.server, [f"--person-token={person}", f"--pairing-code={code}",
                                                 "--project-id", self.mosaic,
                                                 "--single-project-id", self.single])
        self.assertEqual(status, 0, output)
        self.assertNotIn("SKIP  pairing", output)

    def test_suite_passes_with_pairing_only(self):
        codes = [self.tools.issue_pairing_code("Conformance pairer") for _ in range(2)]
        status, output = run_suite(self.server, [f"--pairing-code={codes[0]}", f"--pairing-code={codes[1]}",
                                                 "--project-id", self.mosaic])
        self.assertEqual(status, 0, output)

    def test_example_client_through_the_proxy(self):
        client = ROOT / "reference" / "client.py"
        if not client.exists():
            self.skipTest("reference/client.py is missing")
        helped = subprocess.run([sys.executable, "-m", "reference.client", "--help"], cwd=ROOT,
                                capture_output=True, text=True, timeout=60)
        flags = helped.stdout
        proxy, recorder = make_proxy(("127.0.0.1", 0), self.server, Contract())
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        try:
            through = f"http://127.0.0.1:{proxy.server_address[1]}"
            server_flag = next((f for f in ("--server", "--url", "--api-root") if f in flags), None)
            if server_flag is None:
                self.skipTest("cannot tell how to point reference.client at a server")
            argv = [sys.executable, "-m", "reference.client", server_flag, through]
            if "--pairing-code" in flags:
                argv += [f"--pairing-code={self.tools.issue_pairing_code('Proxy client')}"]
            elif "--person-token" in flags:
                argv += [f"--person-token={self.tools.sign_in('Proxy client')}"]
            run = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=120)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        finally:
            proxy.shutdown()
            proxy.server_close()
        report = recorder.report()
        self.assertGreater(report["requests"], 3)
        self.assertEqual(report["client_faults"], [], report)
        self.assertEqual(report["server_faults"], [], report)


# ---------------------------------------------------------------------------
# Starfront's server
# ---------------------------------------------------------------------------

def starfront_dir() -> Path | None:
    found = Path(os.environ.get("STARFRONT_DIR") or ROOT.parent / "starfront")
    return found if (found / "server" / "run.py").exists() else None


def starfront_python(folder: Path) -> str | None:
    """A Python that can run Starfront's server, or None."""
    choices = [os.environ.get("STARFRONT_PYTHON"), str(folder / ".venv" / "bin" / "python"),
               str(folder / ".venv" / "Scripts" / "python.exe"), sys.executable]
    for choice in choices:
        if not choice or not Path(choice).exists():
            continue
        probe = subprocess.run([choice, "-c", "import fastapi, uvicorn"], capture_output=True)
        if probe.returncode == 0:
            return choice
    return None


def call(server: str, method: str, path: str, token: str | None = None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(server + path, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def starfront_required() -> bool:
    return os.environ.get("STARFRONT_REQUIRED") == "1"


@contextlib.contextmanager
def starfront_server():
    folder = starfront_dir()
    python = starfront_python(folder) if folder else None
    if folder is None or python is None:
        reason = ("Starfront is not checked out (STARFRONT_DIR or ../starfront)" if folder is None
                  else "no Python here can run Starfront's server (it needs fastapi and uvicorn)")
        if starfront_required():
            raise AssertionError(f"STARFRONT_REQUIRED=1, but {reason}")
        raise unittest.SkipTest(reason)
    data = tempfile.mkdtemp(prefix="starfront-conformance-")
    token = secrets.token_urlsafe(24)
    port = free_port()
    env = dict(os.environ, ASTROCOLLAB_DATA=data, ASTROCOLLAB_ADMIN_TOKEN=token,
               PYTHONUNBUFFERED="1")
    # The server's own log goes to a file: a pipe nobody reads would fill and stall it.
    log_path = Path(data) / "server.log"
    log = open(log_path, "w", encoding="utf-8")
    process = subprocess.Popen([python, "server/run.py", "--port", str(port)], cwd=folder, env=env,
                               stdout=log, stderr=subprocess.STDOUT, text=True)
    server = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 60
        while True:
            try:
                call(server, "GET", "/api/v1/health")
                break
            except OSError:
                if process.poll() is not None or time.time() > deadline:
                    log.flush()
                    raise RuntimeError("Starfront's server did not start:\n"
                                       + log_path.read_text(encoding="utf-8"))
                time.sleep(0.2)
        yield server, token
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        log.close()
        shutil.rmtree(data, ignore_errors=True)


#: Checks Starfront is known to fail, each with what the spec says. The test passes
#: while Starfront fails exactly these: a gap Starfront has closed fails it too, so the
#: list cannot go stale.
STARFRONT_GAPS = {
    # The spec lets a rig join with some of a project's filters and be dealt only those;
    # Starfront refuses such a rig with 409 ("no H").
    "a_rig_with_some_filters_joins_and_is_dealt_only_those",
}


class StarfrontServerTests(unittest.TestCase):
    def test_suite_passes(self):
        with starfront_server() as (server, admin):
            # Starfront's coordinator route, outside the protocol: the harness's job.
            mosaic = call(server, "POST", "/api/v1/projects", admin, MOSAIC)["project"]["id"]
            single = call(server, "POST", "/api/v1/projects", admin, SINGLE)["project"]["id"]
            status, output = run_suite(server, [f"--person-token={admin}", "--project-id", mosaic,
                                                "--single-project-id", single])
        failed = {line.split()[1] for line in output.splitlines() if line.startswith("FAIL")}
        self.assertLessEqual(failed, STARFRONT_GAPS, output)
        fixed = STARFRONT_GAPS - failed
        self.assertEqual(fixed, set(), "Starfront now passes these; take them out of STARFRONT_GAPS")


if __name__ == "__main__":
    unittest.main()
