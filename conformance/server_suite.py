"""Black-box conformance checks for an AstroCollab server.

    python -m conformance.server_suite --api-root URL --owner-key K --participant-key K2 \
        [--second-participant-key K3] [--allow-http-loopback]

Each key must belong to a different account and carry all scopes its role allows.
The suite creates projects and submissions on the server; use a test server.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import datetime as dt
import hashlib
import os
import sys
import time
from typing import Any, Callable
import uuid

from .contract import Contract
from .transport import Client, Response


class Fail(Exception):
    pass


class Skip(Exception):
    pass


@dataclass
class Result:
    name: str
    row: str
    outcome: str
    message: str = ""


def new_id() -> str:
    return str(uuid.uuid4())


def stamp(delta: dt.timedelta = dt.timedelta()) -> str:
    moment = dt.datetime.now(dt.timezone.utc).replace(microsecond=0) + delta
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def expect(response: Response, *statuses: int, code: str | None = None) -> Response:
    if response.status not in statuses:
        wanted = " or ".join(str(s) for s in statuses)
        raise Fail(f"{response.describe()}, expected {wanted}" + (f" {code}" if code else ""))
    if code is not None and response.code != code:
        raise Fail(f"{response.describe()}, expected code {code}")
    return response


CHECKS: list[tuple[str, str, str]] = []


def check(row: str):
    """Register a check. `row` names the spec/conformance.md row it covers."""
    def register(func: Callable[["Suite"], None]):
        CHECKS.append((func.__name__, row, func.__name__))
        return func
    return register


@dataclass
class Suite:
    client: Client
    owner: str
    alice: str
    bob: str | None = None
    pairing_code: str | None = None
    second_pairing_code: str | None = None
    state: dict[str, Any] = field(default_factory=dict)

    def get(self, name: str) -> Any:
        if name not in self.state:
            raise Skip(f"needs {name} from an earlier check")
        return self.state[name]

    def req(self, *args, **kwargs) -> Response:
        return self.client.request(*args, **kwargs)

    # Payload builders

    def requirements(self, title: str = "Conformance test project") -> dict[str, Any]:
        target, group, objective, terms = new_id(), new_id(), new_id(), new_id()
        return {
            "title": title,
            "description": "Created by the AstroCollab conformance suite.",
            "visibility": "public",
            "enrollment": "open",
            "state": "open",
            "targets": [{"id": target, "name": "Test field", "footprint": {
                "center": {"ra_degrees": 10.6847, "dec_degrees": 41.269},
                "width_degrees": 3.0, "height_degrees": 2.0, "position_angle_degrees": 0}}],
            "processing_groups": [{"id": group, "name": "Linear mono", "sampling_arcsec_per_pixel":
                                   {"min": 1, "max": 3}, "color_state": "mono", "registration": "unregistered",
                                   "linear": True, "required_calibration_steps": ["dark_subtract", "flat_correct"]}],
            "objectives": [{"id": objective, "target_id": target, "processing_group_id": group,
                            "bandpass": {"name": "H-alpha", "center_nm": 656.3, "width_nm": 3.0},
                            "purpose": "deep", "exposure": {"min_seconds": 300, "max_seconds": 600},
                            "goal": {"accepted_frames": 120, "accepted_integration_seconds": 36000},
                            "minimum_coverage_fraction": 0.5, "fresh_pixel_solve": True,
                            "quality_rules": [], "required_features": []}],
            "terms": {"id": terms, "version": 1, "url": "https://collab.example/terms/conformance/1",
                      "sha256": "b" * 64},
            "capture_deadline": stamp(dt.timedelta(days=60)),
            "submission_deadline": stamp(dt.timedelta(days=75)),
            "surplus_policy": "retain_and_attribute",
            "acceptance_policy_revision": 1,
            "required_features": [],
        }

    def equipment(self) -> dict[str, Any]:
        return {"name": "Conformance rig", "sensor_width_pixels": 6248, "sensor_height_pixels": 4176,
                "pixel_size_um": 3.76, "focal_length_mm": 400, "binning_x": 1, "binning_y": 1,
                "color_state": "mono", "filters": [{"id": new_id(), "bandpass":
                                                    {"name": "H-alpha", "center_nm": 656.3, "width_nm": 3.0}}],
                "rotation": "fixed", "confirmed_position_angle_degrees": 0}

    def register_equipment(self, key: str, participation: str) -> tuple[str, dict[str, Any]]:
        equipment_id, body = new_id(), self.equipment()
        r = expect(self.req("PUT", f"/participations/{participation}/equipment/{equipment_id}", key,
                            body, headers={"If-None-Match": "*"}), 200, 201)
        revision = (r.json or {}).get("equipment", {}).get("revision", 1)
        return equipment_id, {"body": body, "revision": revision, "etag": r.header("etag")}

    def artifact(self, data: bytes, capture: tuple[str, str] | None = None,
                 supersedes: str | None = None, space: str = "") -> dict[str, Any]:
        """Build a manifest entry. `space` selects a project's state keys, such as "ext_"."""
        req = self.get(space + "requirements")
        equipment_id, equipment = self.get(space + "alice_equipment")
        origin, capture_id = capture or (self.state.setdefault("origin", new_id()), new_id())
        digest = sha256(data)
        artifact = {
            "id": new_id(), "origin_id": origin, "capture_id": capture_id,
            "objective_ids": [req["objectives"][0]["id"]],
            "processing_group_id": req["processing_groups"][0]["id"],
            "equipment": {"equipment_id": equipment_id, "revision": equipment["revision"]},
            "captured_at": stamp(dt.timedelta(hours=-2)), "exposure_seconds": 300,
            "filter_id": equipment["body"]["filters"][0]["id"],
            "bandpass": {"name": "H-alpha", "center_nm": 656.3, "width_nm": 3.0},
            "format": "fits", "size_bytes": len(data), "sha256": digest,
            "calibration": {
                "raw_sha256": "c" * 64,
                "masters": [{"role": "dark", "sha256": "d" * 64}, {"role": "flat", "sha256": "e" * 64}],
                "steps": [{"operation": "dark_subtract", "software": "conformance", "version": "1",
                           "parameters": {}},
                          {"operation": "flat_correct", "software": "conformance", "version": "1",
                           "parameters": {}}],
                "pixel_unit": "adu", "linear": True, "color_state": "mono", "registration": "unregistered"},
            "measurements": [],
            "solve": {"source": "fresh_pixel_solve", "artifact_sha256": digest,
                      "center": {"ra_degrees": 10.6847, "dec_degrees": 41.269}, "method": "conformance",
                      "method_version": "1", "measured_at": stamp(dt.timedelta(hours=-1))},
        }
        if supersedes:
            artifact["supersedes_artifact_id"] = supersedes
        return artifact

    def submit(self, key: str, artifacts: list[dict[str, Any]], space: str = "",
               status: int | None = 201) -> Response:
        project = self.get(space + "project")
        body = {"id": new_id(), "participation_id": self.get(space + "alice_participation"),
                "project_revision": 1, "artifacts": artifacts}
        r = self.req("POST", f"/projects/{project}/submissions", key, body)
        return expect(r, status) if status else r

    def put_part(self, upload: dict[str, Any], number: int, data: bytes, digest: str | None = None,
                 key: str | None = None) -> Response:
        size = upload["part_size_bytes"]
        chunk = data[(number - 1) * size:number * size]
        return self.req("PUT", f"/uploads/{upload['id']}/parts/{number}", key or self.alice, body=chunk,
                        headers={"Content-Type": "application/octet-stream",
                                 "X-Part-SHA256": digest or sha256(chunk)})

    def upload_all(self, upload: dict[str, Any], data: bytes) -> None:
        for number in range(1, upload["part_count"] + 1):
            expect(self.put_part(upload, number, data), 200, 201)

    def wait_for_job(self, key: str, job_id: str, limit: float = 60) -> dict[str, Any]:
        deadline = time.monotonic() + limit
        while True:
            r = expect(self.req("GET", f"/jobs/{job_id}", key), 200)
            job = r.json
            if job["state"] in ("succeeded", "failed"):
                return job
            if time.monotonic() > deadline:
                raise Fail(f"job {job_id} still {job['state']} after {limit:.0f} seconds")
            time.sleep(min(job.get("poll_after_seconds", 1), 2))

    # Run

    def run(self) -> list[Result]:
        results = []
        for name, row, func_name in CHECKS:
            func = globals()[func_name]
            before = len(self.client.issues)
            try:
                func(self)
                issues = self.client.issues[before:]
                if issues:
                    raise Fail("contract: " + "; ".join(issues[:3]))
                results.append(Result(name, row, "PASS"))
            except Skip as skip:
                results.append(Result(name, row, "SKIP", str(skip)))
            except Fail as fail:
                results.append(Result(name, row, "FAIL", str(fail)))
            except Exception as error:  # A crash in one check must not stop the others.
                results.append(Result(name, row, "FAIL", f"{type(error).__name__}: {error}"))
        return results


# Checks run in this order; later checks reuse state from earlier ones.

@check("Discovery")
def capabilities(s: Suite) -> None:
    r = expect(s.req("GET", "/capabilities"), 200)
    s.state["capabilities"] = r.json


@check("Credentials")
def missing_credential_is_401(s: Suite) -> None:
    expect(s.req("GET", "/me/participations"), 401)


@check("Credentials")
def bad_credential_is_401(s: Suite) -> None:
    expect(s.req("GET", "/me/participations", "not-a-real-credential-" + new_id()), 401)
    expect(s.req("GET", "/projects", "not-a-real-credential-" + new_id()), 401)


@check("Credentials")
def api_key_reads_account(s: Suite) -> None:
    expect(s.req("GET", "/me/participations", s.alice), 200)


@check("Scoped reads")
def public_routes_need_no_credential(s: Suite) -> None:
    expect(s.req("GET", "/projects"), 200)


def pair(s: Suite, code: str, installation: str) -> Response:
    body = {"pairing_code": code, "installation_id": installation, "client_name": "Conformance suite"}
    return s.req("POST", "/pair", json_body=body, idempotent=False)


@check("Credentials")
def pairing_issues_working_key(s: Suite) -> None:
    if not s.pairing_code:
        raise Skip("needs --pairing-code")
    installation = new_id()
    r = expect(pair(s, s.pairing_code, installation), 201)
    if "no-store" not in (r.header("cache-control") or ""):
        raise Fail("pairing response lacks Cache-Control: no-store")
    issued = r.json
    if issued.get("installation_id") != installation:
        raise Fail("pairing response names a different installation")
    expect(s.req("GET", "/me/participations", issued["api_key"]), 200)
    s.state["paired"] = (installation, issued["api_key"])


@check("Credentials")
def pairing_code_is_single_use(s: Suite) -> None:
    if not s.pairing_code:
        raise Skip("needs --pairing-code")
    s.get("paired")
    expect(pair(s, s.pairing_code, new_id()), 401, code="invalid_pairing_code")
    expect(pair(s, "not-a-real-code-" + new_id()[:8], new_id()), 401, code="invalid_pairing_code")


@check("Credentials")
def repairing_revokes_previous_key(s: Suite) -> None:
    if not s.second_pairing_code:
        raise Skip("needs --second-pairing-code")
    installation, first_key = s.get("paired")
    second = expect(pair(s, s.second_pairing_code, installation), 201).json
    expect(s.req("GET", "/me/participations", second["api_key"]), 200)
    expect(s.req("GET", "/me/participations", first_key), 401)


@check("Publication")
def create_project(s: Suite) -> None:
    requirements = s.requirements()
    body = {"id": new_id(), "requirements": requirements}
    r = expect(s.req("POST", "/projects", s.owner, body), 201)
    created = r.json
    if created["project"]["id"] != body["id"]:
        raise Fail("server did not keep the client-assigned project ID")
    if created["project"]["current_revision"] is not None:
        raise Fail("unpublished project must have current_revision null")
    if created["owner_participation"]["role"] != "owner":
        raise Fail("creator must hold an owner participation")
    s.state["project"], s.state["requirements"] = body["id"], requirements


@check("Publication")
def publish_needs_draft_etag(s: Suite) -> None:
    project = s.get("project")
    draft = expect(s.req("GET", f"/projects/{project}/draft", s.owner), 200)
    etag = draft.header("etag")
    if not etag:
        raise Fail("draft read returned no ETag")
    expect(s.req("POST", f"/projects/{project}/publish", s.owner), 428)
    expect(s.req("POST", f"/projects/{project}/publish", s.owner, headers={"If-Match": '"stale-etag"'}), 412)
    r = expect(s.req("POST", f"/projects/{project}/publish", s.owner, headers={"If-Match": etag}), 201)
    if r.json["revision"] != 1:
        raise Fail(f"first publication returned revision {r.json['revision']}, expected 1")
    current = expect(s.req("GET", f"/projects/{project}"), 200).json
    if current["current_revision"] != 1:
        raise Fail("project does not report the published revision")
    s.state["published"] = True


@check("Publication")
def draft_is_maintainer_only(s: Suite) -> None:
    project = s.get("project")
    s.get("published")
    expect(s.req("GET", f"/projects/{project}/draft", s.alice), 403, 404)


@check("Enrollment")
def open_enrollment_is_active(s: Suite) -> None:
    project = s.get("project")
    s.get("published")
    terms = s.get("requirements")["terms"]
    r = expect(s.req("POST", f"/projects/{project}/participations", s.alice, {"accepted_terms": terms}), 200, 201)
    if r.json["state"] != "active":
        raise Fail(f"open enrollment returned state {r.json['state']}")
    s.state["alice_participation"] = r.json["id"]
    if s.bob:
        r = expect(s.req("POST", f"/projects/{project}/participations", s.bob, {"accepted_terms": terms}),
                   200, 201)
        s.state["bob_participation"] = r.json["id"]


@check("Enrollment")
def duplicate_join_keeps_identity(s: Suite) -> None:
    project, first = s.get("project"), s.get("alice_participation")
    terms = s.get("requirements")["terms"]
    r = expect(s.req("POST", f"/projects/{project}/participations", s.alice, {"accepted_terms": terms}), 200, 201)
    if r.json["id"] != first:
        raise Fail("a second join created a new participation")


@check("Retry")
def idempotent_replay(s: Suite) -> None:
    key = new_id()
    body = {"id": new_id(), "requirements": s.requirements("Idempotency test")}
    headers = {"Idempotency-Key": key}
    first = expect(s.req("POST", "/projects", s.owner, body, headers=headers), 201)
    s.state["other_project"] = body["id"]
    again = expect(s.req("POST", "/projects", s.owner, body, headers=headers), 201)
    if again.json != first.json:
        raise Fail("replay with the same key and body returned a different body")
    changed = dict(body, requirements=dict(body["requirements"], title="Changed title"))
    expect(s.req("POST", "/projects", s.owner, changed, headers=headers), 409, code="idempotency_conflict")


@check("Credentials")
def project_route_needs_participation(s: Suite) -> None:
    other = s.get("other_project")
    s.get("alice_participation")
    expect(s.req("GET", f"/projects/{other}/activity", s.alice), 403, 404)


@check("Credentials")
def contributor_cannot_use_maintainer_scopes(s: Suite) -> None:
    project = s.get("project")
    s.get("alice_participation")
    expect(s.req("GET", f"/projects/{project}/participations", s.alice), 403, 404)
    expect(s.req("POST", f"/projects/{project}/publish", s.alice, headers={"If-Match": '"any"'}), 403, 404)


@check("Abuse")
def unknown_request_fields_rejected(s: Suite) -> None:
    project = s.get("project")
    r = s.req("POST", "/sync/snapshots", s.alice, {"project_ids": [project], "exclusive_target_lock": True})
    expect(r, 400, 422)


@check("Credentials")
def missing_resource_is_404(s: Suite) -> None:
    expect(s.req("GET", f"/jobs/{new_id()}", s.alice), 403, 404)


@check("Retry")
def create_or_replace_preconditions(s: Suite) -> None:
    participation = s.get("alice_participation")
    equipment_id, body = new_id(), s.equipment()
    path = f"/participations/{participation}/equipment/{equipment_id}"
    expect(s.req("PUT", path, s.alice, body, headers={"If-Match": '"x"', "If-None-Match": "*"}), 400)
    expect(s.req("PUT", path, s.alice, body), 400, 428)
    expect(s.req("PUT", path, s.alice, body, headers={"If-Match": '"missing"'}), 412)
    created = expect(s.req("PUT", path, s.alice, body, headers={"If-None-Match": "*"}), 200, 201)
    expect(s.req("PUT", path, s.alice, body, headers={"If-None-Match": "*"}), 412)
    expect(s.req("PUT", path, s.alice, body, headers={"If-Match": '"stale-etag"'}), 412)
    etag = created.header("etag")
    if not etag:
        raise Fail("equipment write returned no ETag")
    current = expect(s.req("GET", path, s.alice), 200)
    expect(s.req("PUT", path, s.alice, dict(body, name="Renamed rig"),
                 headers={"If-Match": current.header("etag") or etag}), 200)
    revision = (created.json or {}).get("equipment", {}).get("revision", 1)
    s.state["alice_equipment"] = (equipment_id, {"body": body, "revision": revision})


@check("Scoped reads")
def private_offers_hidden_from_others(s: Suite) -> None:
    if not s.bob:
        raise Skip("needs --second-participant-key")
    participation = s.get("alice_participation")
    equipment_id, _ = s.get("alice_equipment")
    expect(s.req("GET", f"/participations/{participation}/equipment/{equipment_id}", s.bob), 403, 404)


@check("Status")
def status_sequence_rules(s: Suite) -> None:
    participation, objective = s.get("alice_participation"), s.get("requirements")["objectives"][0]["id"]
    path = f"/participations/{participation}/status/{new_id()}"

    def status(sequence: int, phase: str) -> dict[str, Any]:
        return {"sequence": sequence, "observed_at": stamp(), "expires_at": stamp(dt.timedelta(hours=1)),
                "phase": phase, "objective_id": objective, "captured_reported_frames": 0,
                "visibility": "private"}

    first_body = status(1, "acquiring")
    first = expect(s.req("PUT", path, s.alice, first_body), 200)
    if first.json["applied"] is not True:
        raise Fail("first status was not applied")
    retry = expect(s.req("PUT", path, s.alice, first_body), 200)
    if retry.json != first.json:
        raise Fail("identical retry returned a different receipt")
    expect(s.req("PUT", path, s.alice, dict(first_body, phase="waiting")), 409)
    expect(s.req("PUT", path, s.alice, status(2, "calibrating")), 200)
    lower = expect(s.req("PUT", path, s.alice, status(1, "idle")), 200)
    if lower.json["applied"] is not False:
        raise Fail("lower sequence must return applied: false")


@check("No locks")
def overlapping_intents_both_accepted(s: Suite) -> None:
    if not s.bob:
        raise Skip("needs --second-participant-key")
    req = s.get("requirements")
    for name, key in (("alice", s.alice), ("bob", s.bob)):
        participation = s.get(f"{name}_participation")
        equipment_id, equipment = s.register_equipment(key, participation)
        intent = {"project_revision": 1, "panels": [{
            "id": new_id(), "target_id": req["targets"][0]["id"], "objective_ids": [req["objectives"][0]["id"]],
            "footprint": req["targets"][0]["footprint"], "overlap_fraction": 0.15,
            "equipment": {"equipment_id": equipment_id, "revision": equipment["revision"]},
            "filter_id": equipment["body"]["filters"][0]["id"], "exposure_seconds": 300,
            "suggested_frames": 10}],
            "estimated_rig_seconds": 3600, "expires_at": stamp(dt.timedelta(days=1)), "state": "active"}
        expect(s.req("PUT", f"/participations/{participation}/intents/{new_id()}", key, intent,
                     headers={"If-None-Match": "*"}), 200, 201)


@check("Snapshot")
def snapshot_then_changes(s: Suite) -> None:
    project = s.get("project")
    s.get("alice_participation")
    page = expect(s.req("POST", "/sync/snapshots", s.alice, {"project_ids": [project]}), 201).json
    pages = 1
    while page["next_page_cursor"] is not None:
        if "changes_cursor" in page:
            raise Fail("a page before the last returned changes_cursor")
        page = expect(s.req("GET", f"/sync/snapshots/{page['snapshot_id']}?cursor={page['next_page_cursor']}",
                            s.alice), 200).json
        pages += 1
        if pages > 1000:
            raise Fail("snapshot did not end after 1000 pages")
    cursor = page["changes_cursor"]
    changes = expect(s.req("GET", f"/changes?cursor={cursor}", s.alice), 200).json
    s.state["changes_cursor"] = changes["next_cursor"]


@check("Snapshot")
def invalid_cursor_rejected(s: Suite) -> None:
    expect(s.req("GET", "/changes?cursor=not-a-real-cursor", s.alice), 400, 410)


@check("Multipart")
def upload_parts_out_of_order(s: Suite) -> None:
    limits = s.get("capabilities")["limits"]
    s.get("alice_equipment")
    size = min(2 * limits["max_chunk_bytes"] + 1024, limits["max_artifact_bytes"])
    data = os.urandom(size)
    submission = s.submit(s.alice, [s.artifact(data)]).json
    upload = submission["uploads"][0]
    s.state["upload"] = (submission, upload, data)
    if upload["part_count"] < 2:
        s.upload_all(upload, data)
        raise Skip("server chose a single part; order cannot vary")
    receipts = {}
    for number in range(upload["part_count"], 0, -1):
        receipts[number] = expect(s.put_part(upload, number, data), 200, 201).json
    session = expect(s.req("GET", f"/uploads/{upload['id']}", s.alice), 200).json
    received = sorted(p["part_number"] for p in session["received_parts"])
    if received != list(range(1, upload["part_count"] + 1)):
        raise Fail(f"server lists parts {received} after all were sent")
    s.state["receipt_1"] = receipts[1]


@check("Multipart")
def identical_part_retry(s: Suite) -> None:
    _, upload, data = s.get("upload")
    first = s.state.get("receipt_1")
    again = expect(s.put_part(upload, 1, data), 200, 201).json
    if first is not None and again != first:
        raise Fail("an identical retry returned a different receipt")


@check("Multipart")
def changed_part_conflicts(s: Suite) -> None:
    _, upload, data = s.get("upload")
    size = min(upload["part_size_bytes"], len(data))
    other = bytes(b ^ 0xFF for b in data[:size])
    r = s.req("PUT", f"/uploads/{upload['id']}/parts/1", s.alice, body=other,
              headers={"Content-Type": "application/octet-stream", "X-Part-SHA256": sha256(other)})
    expect(r, 409, code="part_conflict")


@check("Multipart")
def bad_part_digest_rejected(s: Suite) -> None:
    s.get("alice_equipment")
    data = os.urandom(4096)
    submission = s.submit(s.alice, [s.artifact(data)]).json
    upload = submission["uploads"][0]
    s.state["incomplete"] = submission
    expect(s.put_part(upload, 1, data, digest="0" * 64), 422, code="digest_mismatch")
    session = expect(s.req("GET", f"/uploads/{upload['id']}", s.alice), 200).json
    if session["received_parts"]:
        raise Fail("server recorded a part whose digest did not match")


@check("Multipart")
def finalize_incomplete_upload(s: Suite) -> None:
    submission = s.get("incomplete")
    expect(s.req("POST", f"/submissions/{submission['id']}/finalize", s.alice), 409, code="upload_incomplete")


@check("Finalize races")
def finalize_retry_returns_same_job(s: Suite) -> None:
    submission, upload, data = s.get("upload")
    session = expect(s.req("GET", f"/uploads/{upload['id']}", s.alice), 200).json
    have = {p["part_number"] for p in session["received_parts"]}
    for number in range(1, upload["part_count"] + 1):
        if number not in have:
            expect(s.put_part(upload, number, data), 200, 201)
    first = expect(s.req("POST", f"/submissions/{submission['id']}/finalize", s.alice), 202).json
    again = expect(s.req("POST", f"/submissions/{submission['id']}/finalize", s.alice), 202).json
    if first["id"] != again["id"]:
        raise Fail("a second finalize created a new job")
    job = s.wait_for_job(s.alice, first["id"])
    if job["state"] != "succeeded":
        raise Fail(f"assessment job ended {job['state']}")
    s.state["assessed"] = job["result"]


@check("Scoped reads")
def submissions_hidden_from_others(s: Suite) -> None:
    if not s.bob:
        raise Skip("needs --second-participant-key")
    submission, _, _ = s.get("upload")
    expect(s.req("GET", f"/submissions/{submission['id']}", s.bob), 403, 404)


@check("Recalibration")
def progress_counts_capture_once(s: Suite) -> None:
    result = s.get("assessed")
    project = s.get("project")
    artifact = result["artifacts"][0]
    if artifact["state"] != "accepted":
        raise Skip(f"server assessed the test frame as {artifact['state']}; cannot check credit")
    manifest = result["manifest"]["artifacts"][0]
    before = expect(s.req("GET", f"/projects/{project}/progress", s.alice), 200).json
    if before["distinct_credited_captures"] != 1:
        raise Fail(f"one accepted capture shows as {before['distinct_credited_captures']}")
    data = os.urandom(2048)
    replacement = s.artifact(data, capture=(manifest["origin_id"], manifest["capture_id"]),
                             supersedes=manifest["id"])
    submission = s.submit(s.alice, [replacement]).json
    s.upload_all(submission["uploads"][0], data)
    job = expect(s.req("POST", f"/submissions/{submission['id']}/finalize", s.alice), 202).json
    done = s.wait_for_job(s.alice, job["id"])
    if done["state"] != "succeeded" or done["result"]["artifacts"][0]["state"] != "accepted":
        raise Skip("server did not accept the recalibrated frame; cannot check replacement")
    after = expect(s.req("GET", f"/projects/{project}/progress", s.alice), 200).json
    if after["distinct_credited_captures"] != 1:
        raise Fail(f"recalibrating one capture gave {after['distinct_credited_captures']} credited captures")
    frames = after["objectives"][0]["accepted_frames"]
    if frames != 1:
        raise Fail(f"recalibrating one capture gave {frames} accepted frames")


@check("Snapshot")
def changes_report_assessment(s: Suite) -> None:
    cursor = s.get("changes_cursor")
    s.get("assessed")
    seen, sequences = set(), []
    for _ in range(100):
        page = expect(s.req("GET", f"/changes?cursor={cursor}", s.alice), 200).json
        for item in page["items"]:
            sequences.append(item["sequence"])
            if item["operation"] == "upsert":
                seen.add(item["entry"]["kind"])
        if not page["items"]:
            break
        cursor = page["next_cursor"]
    if sequences != sorted(sequences) or len(set(sequences)) != len(sequences):
        raise Fail("change sequences are not strictly increasing")
    if not seen & {"assessment", "progress"}:
        raise Fail("change feed shows no assessment or progress after an assessment")


# External delivery: files shared through a service such as Google Drive.

def external(s: Suite, artifact: dict[str, Any]) -> dict[str, Any]:
    url = f"https://drive.example/file/{new_id()}"
    s.state.setdefault("external_urls", []).append(url)
    return dict(artifact, delivery="external",
                external={"provider": "google_drive", "url": url, "shared_at": stamp(dt.timedelta(minutes=-5))})


def record_retrieval(s: Suite, key: str, submission_id: str, artifact_id: str,
                     body: dict[str, Any]) -> Response:
    etag = expect(s.req("GET", f"/submissions/{submission_id}", s.owner), 200).header("etag")
    if not etag:
        raise Fail("submission read returned no ETag")
    return s.req("POST", f"/submissions/{submission_id}/artifacts/{artifact_id}/retrieval", key, body,
                 headers={"If-Match": etag})


def artifact_state(submission: dict[str, Any], artifact_id: str) -> str:
    return next(a["state"] for a in submission["artifacts"] if a["artifact_id"] == artifact_id)


@check("External delivery")
def external_needs_project_opt_in(s: Suite) -> None:
    s.get("alice_equipment")
    r = s.submit(s.alice, [external(s, s.artifact(b"x" * 1024))], status=None)
    expect(r, 422, code="external_delivery_not_accepted")


@check("External delivery")
def external_project_setup(s: Suite) -> None:
    requirements = s.requirements("External delivery test")
    requirements["external_delivery"] = {"providers": ["google_drive", "https"],
                                         "instructions": "Share files with a link anyone can open."}
    project = new_id()
    expect(s.req("POST", "/projects", s.owner, {"id": project, "requirements": requirements}), 201)
    etag = expect(s.req("GET", f"/projects/{project}/draft", s.owner), 200).header("etag") or '"missing"'
    expect(s.req("POST", f"/projects/{project}/publish", s.owner, headers={"If-Match": etag}), 201)
    for name, key in (("alice", s.alice), ("bob", s.bob)):
        if key:
            r = expect(s.req("POST", f"/projects/{project}/participations", key,
                             {"accepted_terms": requirements["terms"]}), 200, 201)
            s.state[f"ext_{name}_participation"] = r.json["id"]
    s.state["ext_project"], s.state["ext_requirements"] = project, requirements
    s.state["ext_alice_equipment"] = s.register_equipment(s.alice, s.state["ext_alice_participation"])


@check("External delivery")
def external_artifact_awaits_retrieval(s: Suite) -> None:
    s.get("ext_project")
    data = os.urandom(4096)
    artifact = external(s, s.artifact(data, space="ext_"))
    submission = s.submit(s.alice, [artifact], space="ext_").json
    if any(u["artifact_id"] == artifact["id"] for u in submission["uploads"]):
        raise Fail("server opened an upload session for an external artifact")
    if artifact_state(submission, artifact["id"]) != "awaiting_retrieval":
        raise Fail(f"external artifact starts as {artifact_state(submission, artifact['id'])}")
    expect(s.req("POST", f"/submissions/{submission['id']}/finalize", s.alice), 202)
    s.state["ext_submission"] = (submission["id"], artifact, data)


@check("External delivery")
def contributor_cannot_record_retrieval(s: Suite) -> None:
    submission_id, artifact, data = s.get("ext_submission")
    body = {"outcome": "verified", "sha256": sha256(data), "size_bytes": len(data), "retrieved_at": stamp()}
    etag = expect(s.req("GET", f"/submissions/{submission_id}", s.alice), 200).header("etag") or '"any"'
    r = s.req("POST", f"/submissions/{submission_id}/artifacts/{artifact['id']}/retrieval", s.alice, body,
              headers={"If-Match": etag})
    expect(r, 403, 404)


@check("External delivery")
def retrieval_checks_hash(s: Suite) -> None:
    submission_id, artifact, data = s.get("ext_submission")
    body = {"outcome": "verified", "sha256": "0" * 64, "size_bytes": len(data), "retrieved_at": stamp()}
    expect(record_retrieval(s, s.owner, submission_id, artifact["id"], body), 422, code="digest_mismatch")
    state = artifact_state(expect(s.req("GET", f"/submissions/{submission_id}", s.alice), 200).json,
                           artifact["id"])
    if state != "awaiting_retrieval":
        raise Fail(f"a rejected retrieval record changed the artifact to {state}")


@check("External delivery")
def retrieval_mismatch_rejects_artifact(s: Suite) -> None:
    s.get("ext_project")
    data = os.urandom(2048)
    artifact = external(s, s.artifact(data, space="ext_"))
    submission = s.submit(s.alice, [artifact], space="ext_").json
    expect(s.req("POST", f"/submissions/{submission['id']}/finalize", s.alice), 202)
    body = {"outcome": "digest_mismatch", "sha256": "1" * 64, "size_bytes": len(data), "retrieved_at": stamp()}
    result = expect(record_retrieval(s, s.owner, submission["id"], artifact["id"], body), 200).json
    if artifact_state(result, artifact["id"]) != "rejected":
        raise Fail(f"digest_mismatch left the artifact {artifact_state(result, artifact['id'])}")


@check("External delivery")
def verified_retrieval_leads_to_credit(s: Suite) -> None:
    submission_id, artifact, data = s.get("ext_submission")
    project = s.get("ext_project")
    before = expect(s.req("GET", f"/projects/{project}/progress", s.alice), 200).json
    if before["distinct_credited_captures"] != 0:
        raise Fail("external artifact earned credit before retrieval")
    body = {"outcome": "verified", "sha256": sha256(data), "size_bytes": len(data), "retrieved_at": stamp()}
    expect(record_retrieval(s, s.owner, submission_id, artifact["id"], body), 200)
    deadline = time.monotonic() + 60
    while True:
        submission = expect(s.req("GET", f"/submissions/{submission_id}", s.alice), 200).json
        state = artifact_state(submission, artifact["id"])
        if state in ("accepted", "rejected"):
            break
        if time.monotonic() > deadline:
            raise Fail(f"artifact still {state} 60 seconds after verified retrieval")
        time.sleep(1)
    if state != "accepted":
        raise Skip("server rejected the verified test frame; cannot check credit")
    after = expect(s.req("GET", f"/projects/{project}/progress", s.alice), 200).json
    if after["distinct_credited_captures"] != 1:
        raise Fail(f"one verified external capture shows as {after['distinct_credited_captures']}")


@check("External delivery")
def external_links_stay_private(s: Suite) -> None:
    submission_id, _, _ = s.get("ext_submission")
    project = s.get("ext_project")
    urls = s.get("external_urls")
    seen = []
    if s.bob:
        expect(s.req("GET", f"/submissions/{submission_id}", s.bob), 403, 404)
        page = expect(s.req("POST", "/sync/snapshots", s.bob, {"project_ids": [project]}), 201)
        seen.append(page.body)
        seen.append(expect(s.req("GET", f"/projects/{project}/activity", s.bob), 200).body)
    seen.append(expect(s.req("GET", f"/projects/{project}"), 200).body)
    seen.append(expect(s.req("GET", f"/projects/{project}/progress"), 200).body)
    for body in seen:
        if any(url.encode() in body for url in urls):
            raise Fail("a shared-file link appears in another participant's or a public view")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run AstroCollab server conformance checks.")
    parser.add_argument("--api-root", required=True, help="API root, such as https://collab.example/v1")
    parser.add_argument("--owner-key", required=True, help="API key for the account that creates projects")
    parser.add_argument("--participant-key", required=True, help="API key for a second account")
    parser.add_argument("--second-participant-key", help="API key for a third account; enables privacy checks")
    parser.add_argument("--pairing-code", help="unused pairing code for any test account; enables pairing checks")
    parser.add_argument("--second-pairing-code",
                        help="a second code for the same account; checks that re-pairing revokes the old key")
    parser.add_argument("--allow-http-loopback", action="store_true",
                        help="accept http:// URLs on localhost, for local test servers")
    parser.add_argument("--contract", help="path to an OpenAPI contract file")
    args = parser.parse_args(argv)
    contract = Contract(args.contract, args.allow_http_loopback) if args.contract else \
        Contract(allow_http_loopback=args.allow_http_loopback)
    suite = Suite(Client(args.api_root, contract), args.owner_key, args.participant_key,
                  args.second_participant_key, args.pairing_code, args.second_pairing_code)
    results = suite.run()
    for result in results:
        line = f"{result.outcome} {result.name} [{result.row}]"
        print(line + (f": {result.message}" if result.message else ""))
    counts = {o: sum(r.outcome == o for r in results) for o in ("PASS", "FAIL", "SKIP")}
    print(f"\n{counts['PASS']} passed, {counts['FAIL']} failed, {counts['SKIP']} skipped.")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
