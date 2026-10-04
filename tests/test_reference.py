"""Run the reference client against the reference server, then probe its rules.

The server checks every response against openapi/astrocollab.yaml, so a
contract break shows up as a 500 contract_violation.
"""
import hashlib
import json
from pathlib import Path
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reference import client  # noqa: E402
from reference.server import serve  # noqa: E402

ALICE, BOB = "alice-secret-for-tests", "bob-secret-for-tests"
SURVEY, MASTERS, SHARED = 0, 1, 2  # Indexes into sample_project_ids.


class Http:
    """Small HTTP helpers shared by the test classes."""

    def start(self, **options):
        self.httpd = serve(keys={"alice": ALICE, "bob": BOB}, check_responses=True, **options)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.api = self.httpd.api
        self.root = self.api.base_url + "/v1"

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def call(self, method, path, key=None, body=None, headers=None, raw=None):
        headers = dict(headers or {})
        if key:
            headers["Authorization"] = f"Bearer {key}"
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
            headers.setdefault("Content-Type", "application/json")
        request = urllib.request.Request(self.root + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request) as response:
                text = response.read()
                return response.status, response.headers, json.loads(text) if text else None
        except urllib.error.HTTPError as error:
            with error:
                text = error.read()
            return error.code, error.headers, json.loads(text) if text else None

    def post(self, path, key, body=None):
        return self.call("POST", path, key, body)

    def assertProblem(self, result, status, code):
        self.assertEqual((result[0], result[2]["code"]), (status, code), result[2])

    def project(self, index):
        project_id = self.httpd.sample_project_ids[index]
        return project_id, self.call("GET", f"/projects/{project_id}")[2]["requirements"]

    def put_rig(self, key, config):
        equipment_id = str(uuid.uuid4())
        status, _, body = self.call("PUT", f"/me/equipment/{equipment_id}", key, config)
        self.assertEqual(status, 200, body)
        return equipment_id

    def check_in(self, config, key=ALICE, project_index=SURVEY):
        request = {"equipment_id": self.put_rig(key, config),
                   "project_ids": [self.httpd.sample_project_ids[project_index]],
                   "observed_at": "2026-10-04T04:00:00Z"}
        status, _, result = self.post("/me/checkins", key, request)
        self.assertEqual(status, 200, result)
        return result

    @staticmethod
    def filters(count=3):
        return [str(uuid.uuid4()) for _ in client.FILTERS[:count]]

    def submit(self, key, project_id, manifest, data):
        """Create, upload, finalize and wait. Returns the artifact result."""
        status, _, created = self.post(f"/projects/{project_id}/submissions", key, manifest)
        self.assertEqual(status, 201, created)
        for upload in created["uploads"]:
            size = upload["part_size_bytes"]
            for number in range(1, upload["part_count"] + 1):
                chunk = data[(number - 1) * size:number * size]
                self.call("PUT", f"/uploads/{upload['id']}/parts/{number}", key, raw=chunk,
                          headers={"X-Part-SHA256": hashlib.sha256(chunk).hexdigest()})
        status, _, finalized = self.post(f"/submissions/{created['id']}/finalize", key)
        self.assertEqual(status, 202, finalized)
        for _ in range(100):
            result = self.call("GET", f"/submissions/{created['id']}", key)[2]
            if result["state"] == "complete":
                return result["artifacts"][0]
            time.sleep(0.05)
        self.fail("assessment did not finish")


class ReferenceServerTests(Http, unittest.TestCase):
    """The walkthrough, keys, rigs, uploads and deliverables, on one server."""

    @classmethod
    def setUpClass(cls):
        cls.start(cls, part_size=1024)
        cls.progress = client.run(cls.root, ALICE, log=lambda *args: None)

    @classmethod
    def tearDownClass(cls):
        cls.stop(cls)

    def manifest(self, project_id, data, **changes):
        """A one-sub manifest for the survey's first objective, from the example."""
        reqs = self.call("GET", f"/projects/{project_id}")[2]["requirements"]
        objective = reqs["objectives"][0]
        config = client.rig("Upload rig", 400, self.filters(1))
        equipment_id = self.put_rig(ALICE, config)
        manifest = client.example("createSubmission.request.json")
        artifact = manifest["artifacts"][0]
        digest = hashlib.sha256(data).hexdigest()
        artifact.update(id=str(uuid.uuid4()), capture_id=str(uuid.uuid4()),
                        objective_ids=[objective["id"]],
                        processing_group_id=objective["processing_group_id"],
                        equipment={"equipment_id": equipment_id, "revision": 1},
                        filter_id=config["filters"][0]["id"], bandpasses=config["filters"][0]["bandpasses"],
                        exposure_seconds=objective["exposure"]["min_seconds"],
                        size_bytes=len(data), sha256=digest, **changes)
        artifact["solve"]["artifact_sha256"] = digest
        for field in ("assignment_id", "panel_id"):
            artifact.pop(field, None)
        manifest.update(id=str(uuid.uuid4()), project_revision=1)
        return manifest

    def test_walkthrough_credits_one_frame(self):
        self.assertEqual((self.progress["accepted_frames"], self.progress["accepted_integration_seconds"]),
                         (1, 300))

    def test_unknown_key_is_rejected(self):
        self.assertProblem(self.call("GET", "/me/projects", "not-a-key"), 401, "invalid_credentials")

    def test_missing_key_is_rejected(self):
        self.assertProblem(self.call("GET", "/me/projects"), 401, "authentication_required")

    def test_my_projects_lists_the_samples(self):
        items = self.call("GET", "/me/projects", ALICE)[2]["items"]
        self.assertEqual({p["title"] for p in items},
                         {"Sample sky survey", "Sample masters", "Sample shared files"})
        self.assertEqual({p["membership"] for p in items}, {"active"})

    def test_rig_put_patch_and_list(self):
        path = f"/me/equipment/{uuid.uuid4()}"
        config = client.rig("Test rig", 400, self.filters())
        first = self.call("PUT", path, ALICE, config)[2]
        again = self.call("PUT", path, ALICE, config)[2]
        self.assertEqual((first["revision"], again["revision"]), (1, 1))
        patch = {"Content-Type": "application/merge-patch+json"}
        patched = self.call("PATCH", path, ALICE, {"focal_length_mm": 402.5}, patch)[2]
        self.assertEqual(patched["revision"], 2)
        self.assertEqual(patched["configuration"]["focal_length_mm"], 402.5)
        self.assertEqual(patched["configuration"]["filters"], config["filters"])
        listed = self.call("GET", "/me/equipment", ALICE)[2]["items"]
        self.assertIn(first["id"], [e["id"] for e in listed])

    def test_upload_part_rules(self):
        project_id = self.httpd.sample_project_ids[SURVEY]
        data = b"x" * 2500
        manifest = self.manifest(project_id, data)
        status, _, submission = self.post(f"/projects/{project_id}/submissions", ALICE, manifest)
        self.assertEqual(status, 201, submission)
        self.assertEqual(self.post(f"/projects/{project_id}/submissions", ALICE, manifest)[0], 200)
        manifest["artifacts"][0]["exposure_seconds"] += 1
        self.assertProblem(self.post(f"/projects/{project_id}/submissions", ALICE, manifest),
                           409, "id_conflict")
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
        self.assertProblem(self.call("GET", f"/submissions/{submission['id']}", BOB), 404, "not_found")

    def test_external_delivery_is_credited_after_retrieval(self):
        reqs = self.call("GET", f"/projects/{self.httpd.sample_project_ids[SURVEY]}")[2]["requirements"]
        reqs = {**reqs, "external_delivery": {"providers": ["https"],
                                              "instructions": "Share a link to the calibrated file."}}
        project_id = self.api.create_project(reqs)
        self.api.join("alice", project_id)
        manifest = self.manifest(project_id, b"e" * 100)
        artifact = manifest["artifacts"][0]
        artifact.update(delivery="external", external={
            "provider": "dropbox", "url": "https://files.example/share", "path": "m31/frame-1.fits",
            "shared_at": "2026-10-04T05:00:00Z"})
        path = f"/projects/{project_id}/submissions"
        self.assertProblem(self.post(path, ALICE, manifest), 422, "external_delivery_not_accepted")
        artifact["external"]["provider"] = "https"
        status, _, submission = self.post(path, ALICE, manifest)
        self.assertEqual((status, submission["uploads"], submission["artifacts"][0]["state"]),
                         (201, [], "awaiting_retrieval"))
        self.assertEqual(submission["manifest"]["artifacts"][0]["external"]["path"], "m31/frame-1.fits")
        with self.assertRaises(Exception):  # Not finalized yet.
            self.api.record_retrieval(artifact["id"], "verified", artifact["sha256"], artifact["size_bytes"])
        self.assertEqual(self.post(f"/submissions/{submission['id']}/finalize", ALICE)[0], 202)
        with self.assertRaises(Exception):
            self.api.record_retrieval(artifact["id"], "verified", "0" * 64, artifact["size_bytes"])
        self.api.record_retrieval(artifact["id"], "verified", artifact["sha256"], artifact["size_bytes"])
        result = self.call("GET", f"/submissions/{submission['id']}", ALICE)[2]
        self.assertEqual((result["state"], result["artifacts"][0]["state"],
                          result["artifacts"][0]["credited_frames"]), ("complete", "accepted", 1))

    def test_manual_assessment_tool(self):
        project_id = self.httpd.sample_project_ids[SURVEY]
        data = b"r" * 900
        result = self.submit(ALICE, project_id, self.manifest(project_id, data), data)
        self.assertEqual(result["state"], "accepted")
        self.api.assess_artifact(result["artifact_id"], "rejected", ["satellite_trail"])
        sub = next(s for s in self.api.submissions.values()
                   if s["manifest"]["artifacts"][0]["id"] == result["artifact_id"])
        latest = self.call("GET", f"/submissions/{sub['id']}", ALICE)[2]["artifacts"][0]
        self.assertEqual((latest["state"], latest["reason_codes"]), ("rejected", ["satellite_trail"]))
        self.assertNotIn("credited_frames", latest)

    def test_stacked_masters(self):
        project_id, reqs = self.project(MASTERS)
        objective = reqs["objectives"][0]
        config = client.rig("Masters rig", 400, self.filters())
        equipment_id = self.put_rig(ALICE, config)
        exposure = objective["exposure"]["min_seconds"]
        data = b"m" * 1500

        def master(subs, drizzle=False):
            manifest = client.example("stacked-master-submission.json")
            artifact = manifest["artifacts"][0]
            stack = artifact["stack"]
            stack.update(sub_count=len(subs), integration_seconds=len(subs) * exposure, subs=subs,
                         first_captured_at=min(s["captured_at"] for s in subs),
                         last_captured_at=max(s["captured_at"] for s in subs))
            if drizzle:
                stack["drizzle_scale"] = 2
            digest = hashlib.sha256(data).hexdigest()
            artifact.update(id=str(uuid.uuid4()), objective_ids=[objective["id"]],
                            processing_group_id=objective["processing_group_id"],
                            equipment={"equipment_id": equipment_id, "revision": 1},
                            filter_id=config["filters"][0]["id"], bandpasses=config["filters"][0]["bandpasses"],
                            exposure_seconds=exposure, size_bytes=len(data), sha256=digest)
            artifact["solve"]["artifact_sha256"] = digest
            for field in ("assignment_id", "panel_id"):
                artifact.pop(field, None)
            manifest.update(id=str(uuid.uuid4()), project_revision=1)
            return manifest

        origin = str(uuid.uuid4())
        subs = [{"origin_id": origin, "capture_id": str(uuid.uuid4()),
                 "captured_at": f"2026-10-04T04:{n:02d}:00Z", "sha256": f"{n:064x}"} for n in range(20)]
        path = f"/projects/{project_id}/submissions"
        self.assertProblem(self.post(path, ALICE, master(subs[:5])), 422, "too_few_subs")
        self.assertProblem(self.post(path, ALICE, master(subs, drizzle=True)), 422, "drizzle_not_allowed")
        self.assertProblem(self.post(path, ALICE, self.manifest(project_id, data)),
                           422, "deliverable_mismatch")
        first = self.submit(ALICE, project_id, master(subs[:10]), data)
        self.assertEqual((first["state"], first["credited_frames"]), ("accepted", 10))
        again = self.submit(ALICE, project_id, master(subs[9:19]), data)  # Shares one sub.
        self.assertEqual((again["state"], again["reason_codes"]), ("rejected", ["duplicate_capture"]))
        progress = self.call("GET", f"/projects/{project_id}/progress")[2]
        credited = next(o for o in progress["objectives"] if o["objective_id"] == objective["id"])
        self.assertEqual((credited["accepted_frames"], credited["accepted_integration_seconds"]),
                         (10, 10 * exposure))

    def test_pairing_issues_a_working_key_once(self):
        code = self.httpd.issue_pairing_code("carol")
        installation = str(uuid.uuid4())
        request = {"pairing_code": code, "installation_id": installation, "client_name": "Roof rig"}
        status, headers, paired = self.call("POST", "/pair", body=request)
        self.assertEqual(status, 201, paired)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(self.call("GET", "/me/projects", paired["api_key"])[0], 200)
        self.assertProblem(self.call("POST", "/pair", body=request), 401, "invalid_pairing_code")
        request["pairing_code"] = self.httpd.issue_pairing_code("carol")  # Pairing again replaces the key.
        _, _, again = self.call("POST", "/pair", body=request)
        self.assertProblem(self.call("GET", "/me/projects", paired["api_key"]), 401, "invalid_credentials")
        self.assertEqual(self.call("GET", "/me/projects", again["api_key"])[0], 200)

    def test_pairing_for_a_rig_returns_its_id(self):
        rig_id = str(uuid.uuid4())
        code = self.httpd.issue_pairing_code("carol", equipment_id=rig_id)
        _, _, paired = self.call("POST", "/pair", body={
            "pairing_code": code, "installation_id": str(uuid.uuid4()), "client_name": "Rig"})
        self.assertEqual(paired["equipment_id"], rig_id)

    def test_project_limited_key(self):
        code = self.httpd.issue_pairing_code("alice", project_ids=[self.httpd.sample_project_ids[MASTERS]])
        _, _, paired = self.call("POST", "/pair", body={
            "pairing_code": code, "installation_id": str(uuid.uuid4()), "client_name": "Limited"})
        titles = [p["title"] for p in self.call("GET", "/me/projects", paired["api_key"])[2]["items"]]
        self.assertEqual(titles, ["Sample masters"])
        survey = self.httpd.sample_project_ids[SURVEY]
        self.assertProblem(self.post(f"/projects/{survey}/submissions", paired["api_key"],
                                     self.manifest(survey, b"z")), 404, "not_found")


class AssignmentTests(Http, unittest.TestCase):
    """Assignments from check-ins. Each test gets a fresh server, so panels start empty."""

    def setUp(self):
        self.start()

    def tearDown(self):
        self.stop()

    def test_rigs_get_different_framing(self):
        wide = self.check_in(client.rig("Wide", 400, self.filters()))
        long = self.check_in(client.rig("Long", 2000, self.filters()))
        self.assertEqual(wide["action"], "image")
        panel = wide["assignment"]["panels"][0]
        self.assertEqual(len(wide["assignment"]["panels"]), 1)
        self.assertEqual(panel["target_name"], "North America and Pelican nebulae")
        self.assertEqual(panel["layout"], {"columns": 1, "rows": 1, "column": 1, "row": 1})
        self.assertAlmostEqual(panel["footprint"]["width_degrees"], 3.36, places=2)
        long_panel = long["assignment"]["panels"][0]
        self.assertNotEqual(long_panel["target_id"], panel["target_id"])
        self.assertGreater(long_panel["layout"]["columns"] * long_panel["layout"]["rows"], 1)
        self.assertLessEqual(long_panel["footprint"]["width_degrees"], 0.68)  # Fits the 2000 mm field.

    def test_identical_long_rigs_get_different_panels(self):
        first = self.check_in(client.rig("Long A", 2000, self.filters()))["assignment"]
        second = self.check_in(client.rig("Long B", 2000, self.filters()), BOB)["assignment"]
        self.assertEqual(first["panels"][0]["target_id"], second["panels"][0]["target_id"])
        self.assertFalse({p["id"] for p in first["panels"]} & {p["id"] for p in second["panels"]})
        self.assertEqual(len(first["panels"]), 1)  # A panel needs the full goal: one is a night's work.

    def test_smaller_field_gets_its_own_layout(self):
        big = self.check_in(client.rig("2000 mm", 2000, self.filters()))["assignment"]["panels"][0]
        small = client.rig("2400 mm", 2400, self.filters())
        small_panel = self.check_in(small)["assignment"]["panels"][0]
        self.assertEqual(small_panel["target_id"], big["target_id"])
        self.assertLess(small_panel["footprint"]["width_degrees"], big["footprint"]["width_degrees"])
        wider = self.check_in(client.rig("1600 mm", 1600, self.filters()))["assignment"]["panels"][0]
        self.assertEqual(wider["footprint"]["width_degrees"], big["footprint"]["width_degrees"])

    def test_short_rig_takes_a_small_target_whole(self):
        config = client.rig("Small pixels", 600, self.filters(1))
        config.update(pixel_size_um=1.5, filters=[{"id": str(uuid.uuid4()), "bandpasses": [{
            "name": "OIII", "center_nm": 500.7, "width_nm": 3.0}]}])
        assignment = self.check_in(config)["assignment"]
        panel = assignment["panels"][0]
        self.assertEqual(panel["layout"], {"columns": 1, "rows": 1, "column": 1, "row": 1})
        self.assertIn("NGC 7662", panel["target_name"])
        self.assertTrue(any("small in this field" in n for n in assignment["notes"]))

    def test_narrowband_does_not_serve_luminance(self):
        config = client.rig("Long, H-alpha only", 2000, self.filters(1))
        panel = self.check_in(config)["assignment"]["panels"][0]
        self.assertIn("M42", panel["target_name"])  # Not M51's luminance goal.

    def test_dual_band_color_rig_serves_two_objectives(self):
        config = client.rig("Color rig", 400, [])
        config.update(color_state="cfa", filters=[{
            "id": str(uuid.uuid4()), "name": "Dual band", "kind": "dual_narrowband",
            "bandpasses": [{"name": "H-alpha", "center_nm": 656.3, "width_nm": 7},
                           {"name": "OIII", "center_nm": 500.7, "width_nm": 7}]}])
        assignment = self.check_in(config)["assignment"]
        _, reqs = self.project(SURVEY)
        bands = {o["id"]: o["bandpasses"][0]["name"] for o in reqs["objectives"]}
        self.assertEqual(sorted(bands[oid] for oid in assignment["panels"][0]["objective_ids"]),
                         ["H-alpha", "OIII"])

    def test_check_in_retry_and_continue(self):
        equipment_id = self.put_rig(ALICE, client.rig("Long", 2000, self.filters()))
        body = {"equipment_id": equipment_id, "observed_at": "2026-10-04T04:00:00Z"}
        first = self.post("/me/checkins", ALICE, body)[2]["assignment"]
        self.assertEqual(self.post("/me/checkins", ALICE, body)[2]["assignment"]["id"], first["id"])
        panel = first["panels"][0]
        report = {**body, "assignment_id": first["id"], "observed_at": "2026-10-04T05:00:00Z",
                  "unsubmitted_captures": [{"panel_id": panel["id"], "frames": 3,
                                            "integration_seconds": 3 * panel["exposure_seconds"]}]}
        self.assertEqual(self.post("/me/checkins", ALICE, report)[2]["action"], "continue")
        self.post("/me/checkins", ALICE, report)  # A total, not a delta: repeating changes nothing.
        progress = self.call("GET", f"/projects/{first['project_id']}/progress")[2]
        objective = next(o for o in progress["objectives"] if o["objective_id"] in panel["objective_ids"])
        self.assertEqual((objective["reported_frames"], objective["assigned_frames"]),
                         (3, panel["suggested_frames"]))
        # Reported frames are on their way, so the next rig's need for this panel shrinks.
        self.assertEqual(self.api.reported_frames(panel["id"]), 3)

    def test_reported_frames_steer_other_rigs(self):
        # 800 mm with 2×2 binning: 1.94″/px over a 1.7°×1.1° field, so the Heart
        # Nebula (2°×2°) needs a mosaic in the masters project.
        config = client.rig("800 mm binned", 800, self.filters(1))
        config.update(binning_x=2, binning_y=2)
        first_rig = self.put_rig(ALICE, config)
        project_id = self.httpd.sample_project_ids[MASTERS]
        ask = {"project_ids": [project_id], "observed_at": "2026-10-04T04:00:00Z"}
        first = self.post("/me/checkins", ALICE, {**ask, "equipment_id": first_rig})[2]["assignment"]
        panel = first["panels"][0]
        self.assertGreater(panel["layout"]["columns"] * panel["layout"]["rows"], 1)
        report = {**ask, "equipment_id": first_rig, "assignment_id": first["id"],
                  "observed_at": "2026-10-04T06:00:00Z",
                  "unsubmitted_captures": [{"panel_id": panel["id"], "frames": 15,
                                            "integration_seconds": 15 * panel["exposure_seconds"]}]}
        self.post("/me/checkins", ALICE, report)
        self.post("/me/checkins", ALICE, report)  # A total: repeating changes nothing.
        progress = self.call("GET", f"/projects/{project_id}/progress")[2]
        heart = next(o for o in progress["objectives"] if o["objective_id"] in panel["objective_ids"])
        self.assertEqual(heart["reported_frames"], 15)
        second = self.post("/me/checkins", BOB, {**ask, "equipment_id": self.put_rig(BOB, config)})[2]
        self.assertNotEqual(second["assignment"]["panels"][0]["id"], panel["id"])
        self.post("/me/checkins", ALICE, {**report, "unsubmitted_captures": []})  # All submitted.
        progress = self.call("GET", f"/projects/{project_id}/progress")[2]
        self.assertEqual(sum(o["reported_frames"] for o in progress["objectives"]), 0)

    def test_incomplete_rig_waits(self):
        result = self.check_in({"name": "Just a name"})
        self.assertEqual((result["action"], result["reason_codes"]), ("wait", ["rig_incomplete"]))

    def test_low_target_is_skipped_for_site(self):
        config = client.rig("Long", 2000, self.filters())
        config["site"] = {"latitude_degrees": 70, "longitude_degrees": 20, "precision_km": 100,
                          "timezone": "Europe/Oslo"}
        name = self.check_in(config)["assignment"]["panels"][0]["target_name"]
        self.assertNotIn("M42", name)  # Dec −5° peaks at 15° from latitude 70°.

    def test_no_matching_filter_waits(self):
        config = client.rig("Near infrared only", 400, self.filters(1))
        config["filters"][0]["bandpasses"] = [{"name": "Near IR", "center_nm": 850, "width_nm": 100}]
        result = self.check_in(config)
        self.assertEqual((result["action"], result["reason_codes"]), ("wait", ["no_matching_filter"]))
        self.assertNotIn("assignment", result)

    def test_sampling_out_of_range_waits(self):
        result = self.check_in(client.rig("135 mm lens", 135, self.filters()))
        self.assertEqual((result["action"], result["reason_codes"]), ("wait", ["sampling_out_of_range"]))

    def test_panel_completes_and_coverage_counts(self):
        """Frames count toward the panel they name; off-panel frames are rejected."""
        _, reqs = self.project(SURVEY)
        objective = next(o for o in reqs["objectives"] if o["bandpasses"][0]["name"] == "OIII"
                         and o["target_id"] == reqs["targets"][5]["id"])  # NGC 7662, 120 frames.
        self.api.publish(self.httpd.sample_project_ids[SURVEY], {**reqs, "objectives": [
            {**objective, "goal": {"accepted_frames": 2}}]})
        config = client.rig("Small pixels", 600, self.filters(1))
        config.update(pixel_size_um=1.5, filters=[{"id": str(uuid.uuid4()), "bandpasses": [{
            "name": "OIII", "center_nm": 500.7, "width_nm": 3.0}]}])
        equipment_id = self.put_rig(ALICE, config)
        project_id = self.httpd.sample_project_ids[SURVEY]
        self.api.join("alice", project_id)  # Consent to the new revision's terms.
        result = self.post("/me/checkins", ALICE, {"equipment_id": equipment_id,
                                                    "observed_at": "2026-10-04T04:00:00Z"})[2]
        panel = result["assignment"]["panels"][0]
        self.assertEqual(panel["suggested_frames"], 3)  # Two frames, assuming 80% pass.

        def frame(center):
            data = uuid.uuid4().bytes * 10
            manifest = client.example("createSubmission.request.json")
            artifact = manifest["artifacts"][0]
            digest = hashlib.sha256(data).hexdigest()
            artifact.update(id=str(uuid.uuid4()), capture_id=str(uuid.uuid4()),
                            objective_ids=panel["objective_ids"], processing_group_id=objective["processing_group_id"],
                            equipment={"equipment_id": equipment_id, "revision": 1},
                            filter_id=config["filters"][0]["id"], bandpasses=config["filters"][0]["bandpasses"],
                            exposure_seconds=panel["exposure_seconds"], size_bytes=len(data), sha256=digest,
                            assignment_id=result["assignment"]["id"], panel_id=panel["id"])
            artifact["solve"].update(artifact_sha256=digest, center=center)
            manifest.update(id=str(uuid.uuid4()), project_revision=2)
            return self.submit(ALICE, project_id, manifest, data)

        center = panel["footprint"]["center"]
        off = {**center, "dec_degrees": center["dec_degrees"] + panel["footprint"]["height_degrees"] * 0.5}
        self.assertEqual(frame(off)["reason_codes"], ["coverage_too_low"])
        self.assertEqual(frame(center)["state"], "accepted")
        self.assertEqual(frame(center)["state"], "accepted")
        progress = self.call("GET", f"/projects/{project_id}/progress")[2]
        self.assertTrue(progress["objectives"][0]["complete"])
        surplus = frame(center)  # The panel already has the full goal.
        self.assertEqual(surplus["state"], "accepted")
        self.assertNotIn("credited_frames", surplus)


if __name__ == "__main__":
    unittest.main()
