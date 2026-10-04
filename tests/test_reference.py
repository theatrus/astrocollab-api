"""Run the reference client against the reference server, then probe key rules.

The server checks every response against openapi/astrocollab.yaml, so a
contract break shows up as a 500 contract_violation.
"""
import hashlib
import json
from pathlib import Path
import sys
import threading
import unittest
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reference import client  # noqa: E402
from reference.server import serve  # noqa: E402

OWNER, ALICE = "owner-secret-for-tests", "alice-secret-for-tests"


class ReferenceServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = serve(keys={"owner": OWNER, "alice": ALICE}, part_size=1024,
                          check_responses=True)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.root = cls.httpd.api.base_url + "/v1"
        cls.progress = client.run(cls.root, OWNER, ALICE, log=lambda *args: None)
        cls.project_id = cls.progress["project_id"]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def call(self, method, path, key=None, body=None, headers=None, raw=None):
        headers = dict(headers or {})
        if key:
            headers["Authorization"] = f"Bearer {key}"
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.root + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request) as response:
                text = response.read()
                return response.status, response.headers, json.loads(text) if text else None
        except urllib.error.HTTPError as error:
            with error:
                text = error.read()
            return error.code, error.headers, json.loads(text) if text else None

    def post(self, path, key, body=None, idempotency_key=None, headers=None):
        headers = {"Idempotency-Key": idempotency_key or str(uuid.uuid4()), **(headers or {})}
        return self.call("POST", path, key, body, headers)

    def assertProblem(self, result, status, code):
        self.assertEqual((result[0], result[2]["code"]), (status, code), result[2])

    def new_project(self, key=OWNER):
        body = client.example("createProject.request.json", {
            "00000000-0000-4000-8000-000000000001": str(uuid.uuid4())})
        body["requirements"]["capture_deadline"] = "2099-01-01T00:00:00Z"
        body["requirements"]["submission_deadline"] = "2099-02-01T00:00:00Z"
        status, _, created = self.post("/projects", key, body)
        self.assertEqual(status, 201, created)
        return body, created

    def test_walkthrough_credits_one_frame(self):
        objective = self.progress["objectives"][0]
        self.assertEqual(objective["accepted_frames"], 1)
        self.assertEqual(objective["accepted_integration_seconds"], 300)
        self.assertEqual(self.progress["distinct_credited_captures"], 1)

    def test_unknown_key_is_rejected(self):
        self.assertProblem(self.call("GET", "/me/participations", "not-a-key"),
                           401, "invalid_credentials")

    def test_missing_key_is_rejected(self):
        self.assertProblem(self.call("GET", "/me/participations"), 401, "authentication_required")

    def test_role_limits_key_scopes(self):
        # Alice's key holds every scope, but a contributor cannot read the draft.
        self.assertProblem(self.call("GET", f"/projects/{self.project_id}/draft", ALICE),
                           403, "insufficient_scope")

    def test_draft_replace_needs_current_etag(self):
        body, created = self.new_project()
        path = f"/projects/{created['project']['id']}/draft"
        self.assertProblem(self.call("PUT", path, OWNER, body["requirements"]),
                           428, "precondition_required")
        self.assertProblem(self.call("PUT", path, OWNER, body["requirements"],
                                     {"If-Match": '"stale"'}), 412, "precondition_failed")
        _, headers, _ = self.call("GET", path, OWNER)
        status, _, draft = self.call("PUT", path, OWNER, body["requirements"],
                                     {"If-Match": headers["ETag"]})
        self.assertEqual((status, draft["draft_revision"]), (200, 2))

    def test_idempotent_replay_and_conflict(self):
        body = client.example("createProject.request.json", {
            "00000000-0000-4000-8000-000000000001": str(uuid.uuid4())})
        key = str(uuid.uuid4())
        first = self.post("/projects", OWNER, body, key)
        again = self.post("/projects", OWNER, body, key)
        self.assertEqual((first[0], again[0]), (201, 201))
        self.assertEqual(first[2], again[2])
        body["requirements"]["title"] = "Changed"
        self.assertProblem(self.post("/projects", OWNER, body, key), 409, "idempotency_conflict")

    def test_upload_part_rules(self):
        body, created = self.new_project()
        project_id = created["project"]["id"]
        self.post(f"/projects/{project_id}/publish", OWNER,
                  headers={"If-Match": self.call("GET", f"/projects/{project_id}/draft", OWNER)[1]["ETag"]})
        terms = body["requirements"]["terms"]
        _, _, part = self.post(f"/projects/{project_id}/participations", ALICE, {"accepted_terms": terms})
        ids = {"00000000-0000-4000-8000-000000000003": part["id"],
               "00000000-0000-4000-8000-000000000007": str(uuid.uuid4()),
               "00000000-0000-4000-8000-000000000011": str(uuid.uuid4()),
               "00000000-0000-4000-8000-000000000012": str(uuid.uuid4())}
        equipment = ids["00000000-0000-4000-8000-000000000007"]
        self.call("PUT", f"/participations/{part['id']}/equipment/{equipment}", ALICE,
                  client.example("registerEquipment.request.json", ids), {"If-None-Match": "*"})
        data = b"x" * 2500
        manifest = client.example("createSubmission.request.json", ids)
        manifest["artifacts"][0].update(size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        status, _, submission = self.post(f"/projects/{project_id}/submissions", ALICE, manifest)
        self.assertEqual(status, 201, submission)
        upload = submission["uploads"][0]
        self.assertEqual((upload["part_size_bytes"], upload["part_count"]), (1024, 3))

        def put(number, chunk, digest=None):
            return self.call("PUT", f"/uploads/{upload['id']}/parts/{number}", ALICE, raw=chunk,
                             headers={"X-Part-SHA256": digest or hashlib.sha256(chunk).hexdigest()})

        self.assertProblem(put(1, data[:1024], "0" * 64), 422, "digest_mismatch")
        first = put(1, data[:1024])
        self.assertEqual(first[0], 200)
        self.assertEqual(put(1, data[:1024])[2], first[2])  # Identical retry, same receipt.
        self.assertProblem(put(1, b"y" * 1024), 409, "part_conflict")
        self.assertProblem(put(3, data[2048:] + b"z"), 422, "part_size_mismatch")
        self.assertProblem(self.post(f"/submissions/{submission['id']}/finalize", ALICE),
                           409, "upload_incomplete")

    def test_external_delivery_is_credited_after_retrieval(self):
        body = client.example("createProject.request.json", {
            "00000000-0000-4000-8000-000000000001": str(uuid.uuid4())})
        reqs = body["requirements"]
        reqs.update(capture_deadline="2099-01-01T00:00:00Z", submission_deadline="2099-02-01T00:00:00Z",
                    external_delivery={"providers": ["https"],
                                       "instructions": "Share a link to the calibrated file."})
        _, _, created = self.post("/projects", OWNER, body)
        project_id = created["project"]["id"]
        draft = f"/projects/{project_id}/draft"
        self.post(f"/projects/{project_id}/publish", OWNER,
                  headers={"If-Match": self.call("GET", draft, OWNER)[1]["ETag"]})
        _, _, part = self.post(f"/projects/{project_id}/participations", ALICE,
                               {"accepted_terms": reqs["terms"]})
        ids = {"00000000-0000-4000-8000-000000000003": part["id"],
               "00000000-0000-4000-8000-000000000007": str(uuid.uuid4()),
               "00000000-0000-4000-8000-000000000010": str(uuid.uuid4()),
               "00000000-0000-4000-8000-000000000011": str(uuid.uuid4()),
               "00000000-0000-4000-8000-000000000012": str(uuid.uuid4())}
        equipment = ids["00000000-0000-4000-8000-000000000007"]
        self.call("PUT", f"/participations/{part['id']}/equipment/{equipment}", ALICE,
                  client.example("registerEquipment.request.json", ids), {"If-None-Match": "*"})
        manifest = client.example("createSubmission.request.json", ids)
        artifact = manifest["artifacts"][0]
        artifact.update(delivery="external", external={
            "provider": "dropbox", "url": "https://files.example/m31.fits",
            "shared_at": "2026-10-04T05:00:00Z"})
        path = f"/projects/{project_id}/submissions"
        self.assertProblem(self.post(path, ALICE, manifest), 422, "external_delivery_not_accepted")
        artifact["external"]["provider"] = "https"
        status, _, submission = self.post(path, ALICE, manifest)
        self.assertEqual((status, submission["uploads"], submission["artifacts"][0]["state"]),
                         (201, [], "awaiting_retrieval"))
        _, _, job = self.post(f"/submissions/{submission['id']}/finalize", ALICE)

        sub_path = f"/submissions/{submission['id']}"
        retrieval = f"{sub_path}/artifacts/{artifact['id']}/retrieval"
        verified = {"outcome": "verified", "sha256": artifact["sha256"],
                    "size_bytes": artifact["size_bytes"], "retrieved_at": "2026-10-05T00:00:00Z"}
        etag = self.call("GET", sub_path, OWNER)[1]["ETag"]
        self.assertProblem(self.post(retrieval, ALICE, verified, headers={"If-Match": etag}),
                           403, "insufficient_scope")
        wrong = {**verified, "sha256": "0" * 64}
        self.assertProblem(self.post(retrieval, OWNER, wrong, headers={"If-Match": etag}),
                           422, "digest_mismatch")
        status, _, result = self.post(retrieval, OWNER, verified, headers={"If-Match": etag})
        self.assertEqual((status, result["artifacts"][0]["state"], result["state"]),
                         (200, "accepted", "complete"))
        self.assertEqual(self.call("GET", f"/jobs/{job['id']}", ALICE)[2]["state"], "succeeded")
        progress = self.call("GET", f"/projects/{project_id}/progress", ALICE)[2]
        self.assertEqual(progress["objectives"][0]["accepted_frames"], 1)

    def test_pairing_issues_a_working_key_once(self):
        code = self.httpd.issue_pairing_code("carol")
        installation = str(uuid.uuid4())
        request = {"pairing_code": code, "installation_id": installation, "client_name": "Roof rig"}
        status, headers, paired = self.call("POST", "/pair", body=request)
        self.assertEqual(status, 201, paired)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(self.call("GET", "/me/participations", paired["api_key"])[0], 200)
        self.assertProblem(self.call("POST", "/pair", body=request), 401, "invalid_pairing_code")

        # Pairing the same installation again replaces its old key.
        request["pairing_code"] = self.httpd.issue_pairing_code("carol")
        _, _, again = self.call("POST", "/pair", body=request)
        self.assertProblem(self.call("GET", "/me/participations", paired["api_key"]),
                           401, "invalid_credentials")
        self.assertEqual(self.call("GET", "/me/participations", again["api_key"])[0], 200)

    def test_project_limited_key(self):
        code = self.httpd.issue_pairing_code("alice", project_ids=[str(uuid.uuid4())])
        _, _, paired = self.call("POST", "/pair", body={
            "pairing_code": code, "installation_id": str(uuid.uuid4()), "client_name": "Limited"})
        self.assertEqual(len(paired["project_ids"]), 1)
        # Alice belongs to the walkthrough project, but this key does not cover it.
        self.assertProblem(self.call("GET", f"/projects/{self.project_id}/activity", paired["api_key"]),
                           404, "not_found")


if __name__ == "__main__":
    unittest.main()
