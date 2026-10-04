"""Black-box conformance checks for an AstroCollab server.

    python -m conformance.server_suite --api-root URL --participant-key K --project-id P \
        [--second-participant-key K2] [--masters-project-id M] [--external-project-id E] \
        [--pairing-code C] [--second-pairing-code C2] [--allow-http-loopback]

The API serves contributors only, so the suite cannot create projects. Prepare
them on the server first, with the participant accounts as active members. The
suite registers rigs and submits synthetic frames; use a test server.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import datetime as dt
import hashlib
import math
import os
import sys
import time
from typing import Any, Callable
import uuid

from .contract import DEFAULT_CONTRACT, Contract
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


CHECKS: list[tuple[str, str]] = []


def check(row: str):
    """Register a check. `row` names the spec/conformance.md row it covers."""
    def register(func: Callable[["Suite"], None]):
        CHECKS.append((func.__name__, row))
        return func
    return register


# Rig geometry and passbands

def field_of_view(rig: dict[str, Any]) -> tuple[float, float]:
    """Width and height of the rig's field in degrees. Sensor sizes count unbinned
    pixels, so binning does not change the field."""
    def axis(pixels: int) -> float:
        size_mm = pixels * rig["pixel_size_um"] / 1000
        return math.degrees(2 * math.atan(size_mm / (2 * rig["focal_length_mm"])))
    return axis(rig["sensor_width_pixels"]), axis(rig["sensor_height_pixels"])


def sampling(rig: dict[str, Any]) -> float:
    """Arcseconds per binned pixel."""
    return 206.265 * rig["pixel_size_um"] * rig.get("binning_x", 1) / rig["focal_length_mm"]


def band_matches(band: dict[str, Any], accepted: dict[str, Any]) -> bool:
    """Protocol §6: the center lies inside the accepted band and, when both widths
    are known, the width is between ¼ and 1× the accepted width. Without an
    accepted width, centers must agree within 5 nm."""
    if "width_nm" not in accepted:
        return abs(band["center_nm"] - accepted["center_nm"]) <= 5
    if abs(band["center_nm"] - accepted["center_nm"]) > accepted["width_nm"] / 2:
        return False
    return "width_nm" not in band or accepted["width_nm"] / 4 <= band["width_nm"] <= accepted["width_nm"]


def filter_for(accepted: dict[str, Any]) -> dict[str, Any]:
    """A filter with one passband that matches the accepted band."""
    band = dict(accepted)
    if "width_nm" in band:
        band["width_nm"] = band["width_nm"] / 2
    return {"id": new_id(), "name": f"{accepted['name']} test filter", "kind": "narrowband",
            "bandpasses": [band]}


@dataclass
class Project:
    """A prepared project, read from GET /projects/{id}."""
    id: str
    revision: int
    requirements: dict[str, Any]

    @property
    def objective(self) -> dict[str, Any]:
        return self.requirements["objectives"][0]

    @property
    def group(self) -> dict[str, Any]:
        groups = {g["id"]: g for g in self.requirements["processing_groups"]}
        return groups[self.objective["processing_group_id"]]

    @property
    def target(self) -> dict[str, Any]:
        targets = {t["id"]: t for t in self.requirements["targets"]}
        return targets[self.objective["target_id"]]

    def rig(self, sampling_arcsec: float | None = None, name: str = "Conformance rig") -> dict[str, Any]:
        """A complete rig that suits this project's first objective."""
        limits = self.group["sampling_arcsec_per_pixel"]
        wanted = sampling_arcsec or (limits["min"] + limits["max"]) / 2
        return {"name": name, "sensor_width_pixels": 6248, "sensor_height_pixels": 4176,
                "pixel_size_um": 3.76,
                # Round down, so sampling never falls just below the minimum.
                "focal_length_mm": math.floor(206.265 * 3.76 / wanted * 10) / 10,
                "binning_x": 1, "binning_y": 1, "color_state": self.group["color_state"],
                "filters": [filter_for(self.objective["bandpasses"][0])],
                "rotation": "fixed", "confirmed_position_angle_degrees": 0}


@dataclass
class Suite:
    client: Client
    alice: str
    project_id: str
    bob: str | None = None
    masters_project_id: str | None = None
    external_project_id: str | None = None
    pairing_code: str | None = None
    second_pairing_code: str | None = None
    state: dict[str, Any] = field(default_factory=dict)
    projects: dict[str, Project] = field(default_factory=dict)

    def get(self, name: str) -> Any:
        if name not in self.state:
            raise Skip(f"needs {name} from an earlier check")
        return self.state[name]

    def req(self, *args, **kwargs) -> Response:
        return self.client.request(*args, **kwargs)

    def project(self, project_id: str | None = None) -> Project:
        project_id = project_id or self.project_id
        if project_id not in self.projects:
            value = expect(self.req("GET", f"/projects/{project_id}", self.alice), 200).json
            self.projects[project_id] = Project(value["id"], value["revision"], value["requirements"])
        return self.projects[project_id]

    def register_rig(self, key: str, body: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        rig_id = new_id()
        r = expect(self.req("PUT", f"/me/equipment/{rig_id}", key, body), 200, 201)
        return rig_id, {"body": body, "revision": r.json["revision"]}

    def rig_for(self, project: Project) -> tuple[str, dict[str, Any]]:
        """Register one rig per project for submissions, and reuse it."""
        name = f"rig:{project.id}"
        if name not in self.state:
            self.state[name] = self.register_rig(self.alice, project.rig())
        return self.state[name]

    # Manifests

    def artifact(self, project: Project, data: bytes, capture: tuple[str, str] | None = None,
                 supersedes: str | None = None) -> dict[str, Any]:
        """A calibrated sub that meets the project's first objective."""
        rig_id, rig = self.rig_for(project)
        origin, capture_id = capture or (self.state.setdefault("origin", new_id()), new_id())
        digest = sha256(data)
        artifact = {
            "id": new_id(), "kind": "calibrated_sub", "origin_id": origin, "capture_id": capture_id,
            "objective_ids": [project.objective["id"]], "processing_group_id": project.group["id"],
            "equipment": {"equipment_id": rig_id, "revision": rig["revision"]},
            "captured_at": stamp(dt.timedelta(hours=-2)),
            "exposure_seconds": project.objective["exposure"]["min_seconds"],
            "filter_id": rig["body"]["filters"][0]["id"],
            "bandpasses": rig["body"]["filters"][0]["bandpasses"],
            "format": "fits", "size_bytes": len(data), "sha256": digest,
            "calibration": {
                "raw_sha256": sha256(b"raw" + data),
                "calibration_frames": [{"role": "dark", "sha256": "d" * 64}, {"role": "flat", "sha256": "e" * 64}],
                "steps": [{"operation": step, "software": "conformance", "version": "1", "parameters": {}}
                          for step in project.group["required_calibration_steps"] or ["dark_subtract"]],
                "pixel_unit": "adu", "linear": project.group["linear"], "color_state": project.group["color_state"],
                "registration": project.group["registration"]},
            "measurements": [],
            "solve": {"source": "fresh_pixel_solve", "artifact_sha256": digest,
                      "center": project.target["footprint"]["center"], "method": "conformance",
                      "method_version": "1", "measured_at": stamp(dt.timedelta(hours=-1))},
        }
        if supersedes:
            artifact["supersedes_artifact_id"] = supersedes
        return artifact

    def master(self, project: Project, data: bytes, subs: list[dict[str, Any]]) -> dict[str, Any]:
        """A stacked master of the given sub records."""
        artifact = self.artifact(project, data)
        for name in ("origin_id", "capture_id", "captured_at"):
            del artifact[name]
        del artifact["calibration"]["raw_sha256"]
        artifact["kind"] = "stacked_master"
        artifact["calibration"]["registration"] = "registered"
        times = sorted(sub["captured_at"] for sub in subs)
        artifact["stack"] = {
            "sub_count": len(subs), "integration_seconds": artifact["exposure_seconds"] * len(subs),
            "first_captured_at": times[0], "last_captured_at": times[-1], "subs": subs,
            "registration": "star alignment, Lanczos-3", "normalization": "additive with scaling",
            "rejection": "winsorized sigma clip, 3.0/3.0", "weighting": "PSF signal weight",
            "software": "conformance", "version": "1"}
        return artifact

    def stacked_subs(self, count: int) -> list[dict[str, Any]]:
        origin = self.state.setdefault("origin", new_id())
        return [{"origin_id": origin, "capture_id": new_id(),
                 "captured_at": stamp(dt.timedelta(hours=-3, minutes=5 * i)), "sha256": sha256(os.urandom(8))}
                for i in range(count)]

    def submit(self, key: str, project: Project, artifacts: list[dict[str, Any]],
               status: int | None = 201) -> Response:
        body = {"id": new_id(), "project_revision": project.revision, "artifacts": artifacts}
        r = self.req("POST", f"/projects/{project.id}/submissions", key, body)
        return expect(r, status) if status else r

    def put_part(self, upload: dict[str, Any], number: int, data: bytes, digest: str | None = None) -> Response:
        size = upload["part_size_bytes"]
        chunk = data[(number - 1) * size:number * size]
        return self.req("PUT", f"/uploads/{upload['id']}/parts/{number}", self.alice, body=chunk,
                        headers={"Content-Type": "application/octet-stream",
                                 "X-Part-SHA256": digest or sha256(chunk)})

    def upload_all(self, upload: dict[str, Any], data: bytes) -> None:
        for number in range(1, upload["part_count"] + 1):
            expect(self.put_part(upload, number, data), 200)

    def finalize(self, submission_id: str) -> dict[str, Any]:
        return expect(self.req("POST", f"/submissions/{submission_id}/finalize", self.alice), 202).json

    def wait_until_assessed(self, submission_id: str, limit: float = 60) -> dict[str, Any]:
        """Poll the submission until every artifact has a final result."""
        deadline = time.monotonic() + limit
        while True:
            submission = expect(self.req("GET", f"/submissions/{submission_id}", self.alice), 200).json
            if all(a["state"] in ("accepted", "rejected", "superseded") for a in submission["artifacts"]):
                return submission
            if time.monotonic() > deadline:
                states = ", ".join(a["state"] for a in submission["artifacts"])
                raise Fail(f"submission still {states} after {limit:.0f} seconds")
            time.sleep(1)

    def send(self, project: Project, artifact: dict[str, Any], data: bytes) -> dict[str, Any]:
        """Submit, upload, finalize and wait. Return the assessed submission."""
        submission = self.submit(self.alice, project, [artifact]).json
        for upload in submission["uploads"]:
            self.upload_all(upload, data)
        self.finalize(submission["id"])
        return self.wait_until_assessed(submission["id"])

    def progress(self, project: Project) -> dict[str, Any]:
        return expect(self.req("GET", f"/projects/{project.id}/progress", self.alice), 200).json

    def accepted_frames(self, project: Project) -> int:
        return self.progress(project)["accepted_frames"]

    # Run

    def run(self) -> list[Result]:
        results = []
        for name, row in CHECKS:
            before = len(self.client.issues)
            try:
                globals()[name](self)
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

@check("Credentials")
def capabilities(s: Suite) -> None:
    s.state["capabilities"] = expect(s.req("GET", "/capabilities"), 200).json


@check("Credentials")
def prepared_project_is_open(s: Suite) -> None:
    mine = expect(s.req("GET", "/me/projects", s.alice), 200).json["items"]
    membership = {p["project_id"]: p["membership"] for p in mine}
    for project_id in filter(None, (s.project_id, s.masters_project_id, s.external_project_id)):
        if membership.get(project_id) != "active":
            raise Fail(f"participant key is not an active member of project {project_id}")
    project = s.project()
    if project.requirements.get("deliverable", "calibrated_subs") != "calibrated_subs":
        raise Fail("--project-id must take calibrated subs")
    s.state["ready"] = True


@check("Credentials")
def missing_credential_is_401(s: Suite) -> None:
    expect(s.req("GET", "/me/projects"), 401)


@check("Credentials")
def bad_credential_is_401(s: Suite) -> None:
    expect(s.req("GET", "/me/projects", "not-a-real-credential-" + new_id()), 401)
    expect(s.req("GET", f"/projects/{s.project_id}", "not-a-real-credential-" + new_id()), 401)


@check("Credentials")
def other_projects_are_closed(s: Suite) -> None:
    s.get("ready")
    project = s.project()
    other = Project(new_id(), project.revision, project.requirements)
    r = s.submit(s.alice, other, [s.artifact(project, os.urandom(1024))], status=None)
    expect(r, 403, 404)


@check("Privacy")
def public_routes_need_no_credential(s: Suite) -> None:
    expect(s.req("GET", "/capabilities"), 200)
    project = s.project()
    if project.requirements.get("visibility") == "public":
        expect(s.req("GET", f"/projects/{project.id}"), 200)
        expect(s.req("GET", f"/projects/{project.id}/progress"), 200)


# Pairing

def pair(s: Suite, code: str, installation: str) -> Response:
    body = {"pairing_code": code, "installation_id": installation, "client_name": "Conformance suite"}
    return s.req("POST", "/pair", json_body=body)


@check("Pairing")
def pairing_issues_working_key(s: Suite) -> None:
    if not s.pairing_code:
        raise Skip("needs --pairing-code")
    installation = new_id()
    r = expect(pair(s, s.pairing_code, installation), 201)
    if "no-store" not in (r.header("cache-control") or ""):
        raise Fail("pairing response lacks Cache-Control: no-store")
    if r.json.get("installation_id") != installation:
        raise Fail("pairing response names a different installation")
    expect(s.req("GET", "/me/projects", r.json["api_key"]), 200)
    s.state["paired"] = (installation, r.json["api_key"])


@check("Pairing")
def pairing_code_is_single_use(s: Suite) -> None:
    if not s.pairing_code:
        raise Skip("needs --pairing-code")
    s.get("paired")
    expect(pair(s, s.pairing_code, new_id()), 401, code="invalid_pairing_code")
    expect(pair(s, "not-a-real-code-" + new_id()[:8], new_id()), 401, code="invalid_pairing_code")


@check("Pairing")
def repairing_revokes_previous_key(s: Suite) -> None:
    if not s.second_pairing_code:
        raise Skip("needs --second-pairing-code")
    installation, first_key = s.get("paired")
    second = expect(pair(s, s.second_pairing_code, installation), 201).json
    expect(s.req("GET", "/me/projects", second["api_key"]), 200)
    expect(s.req("GET", "/me/projects", first_key), 401)


# Rigs

@check("Abuse")
def unknown_request_fields_rejected(s: Suite) -> None:
    body = dict(s.project().rig(), exclusive_target_lock=True)
    expect(s.req("PUT", f"/me/equipment/{new_id()}", s.alice, body), 400, 422)


@check("Rigs")
def repeated_put_keeps_revision(s: Suite) -> None:
    rig_id, body = new_id(), s.project().rig(name="Revision test rig")
    path = f"/me/equipment/{rig_id}"
    first = expect(s.req("PUT", path, s.alice, body), 200, 201).json
    again = expect(s.req("PUT", path, s.alice, body), 200).json
    if again["revision"] != first["revision"]:
        raise Fail("repeating an identical equipment PUT created a new revision")
    renamed = expect(s.req("PUT", path, s.alice, dict(body, name="Renamed rig")), 200).json
    if renamed["revision"] <= first["revision"]:
        raise Fail("changing equipment did not create a new revision")
    listed = expect(s.req("GET", "/me/equipment", s.alice), 200).json
    if rig_id not in {item["id"] for item in listed["items"]}:
        raise Fail("GET /me/equipment does not list the registered rig")
    s.state["alice_rig_id"] = rig_id


@check("Rigs")
def patch_changes_one_field(s: Suite) -> None:
    rig_id, body = new_id(), s.project().rig(name="Patch test rig")
    path = f"/me/equipment/{rig_id}"
    first = expect(s.req("PUT", path, s.alice, body), 200, 201).json
    patched = expect(s.req("PATCH", path, s.alice, {"focal_length_mm": body["focal_length_mm"] + 2.5},
                           headers={"Content-Type": "application/merge-patch+json"}), 200).json
    expected = dict(body, focal_length_mm=body["focal_length_mm"] + 2.5)
    changed = sorted(k for k in expected if patched["configuration"].get(k) != expected[k])
    if changed:
        raise Fail(f"PATCH of focal_length_mm also changed or dropped: {', '.join(changed)}")
    if patched["revision"] <= first["revision"]:
        raise Fail("PATCH that changed the rig did not create a new revision")


@check("Rigs")
def incomplete_rig_waits(s: Suite) -> None:
    rig_id = new_id()
    expect(s.req("PUT", f"/me/equipment/{rig_id}", s.alice, {"name": "Rig with only a name"}), 200, 201)
    result = expect(s.req("POST", "/me/checkins", s.alice, {"equipment_id": rig_id, "observed_at": stamp()}),
                    200).json
    if result["action"] != "wait" or "rig_incomplete" not in result["reason_codes"]:
        raise Fail(f"incomplete rig got {result['action']} {result['reason_codes']}, "
                   "expected wait with rig_incomplete")


@check("Privacy")
def rigs_hidden_from_others(s: Suite) -> None:
    if not s.bob:
        raise Skip("needs --second-participant-key")
    rig_id = s.get("alice_rig_id")
    expect(s.req("GET", f"/me/equipment/{rig_id}", s.bob), 403, 404)
    if rig_id in {item["id"] for item in expect(s.req("GET", "/me/equipment", s.bob), 200).json["items"]}:
        raise Fail("another account's rig appears in GET /me/equipment")


# Asking for work

def ask_for_work(s: Suite, key: str, rig_id: str, project_ids: list[str] | None = None) -> dict[str, Any] | None:
    """Check in with one rig. Return the assignment, or None when the server says wait."""
    body: dict[str, Any] = {"equipment_id": rig_id, "observed_at": stamp()}
    if project_ids:
        body["project_ids"] = project_ids
    result = expect(s.req("POST", "/me/checkins", key, body), 200).json
    if result["action"] == "image":
        return result["assignment"]
    if result["action"] == "wait":
        return None
    raise Fail(f"first check-in for a new rig returned {result['action']}")


def plan_problems(plan: dict[str, Any], rig_id: str, rig: dict[str, Any], req: dict[str, Any]) -> list[str]:
    problems = []
    width, height = field_of_view(rig)
    objectives = {o["id"]: o for o in req["objectives"]}
    groups = {g["id"]: g for g in req["processing_groups"]}
    targets = {t["id"] for t in req["targets"]}
    filters = {f["id"]: f for f in rig["filters"]}
    for panel in plan["panels"]:
        name = f"panel {panel['id'][:8]}"
        fp = panel["footprint"]
        w, h = fp["width_degrees"] / 1.02, fp["height_degrees"] / 1.02
        if not ((w <= width and h <= height) or (w <= height and h <= width)):
            problems.append(f"{name} is {fp['width_degrees']:.2f}x{fp['height_degrees']:.2f} degrees; "
                            f"the rig sees {width:.2f}x{height:.2f}")
        if panel["equipment"]["equipment_id"] != rig_id:
            problems.append(f"{name} names another rig")
        rig_filter = filters.get(panel["filter_id"])
        if rig_filter is None:
            problems.append(f"{name} uses a filter the rig does not have")
        if panel["target_id"] not in targets:
            problems.append(f"{name} names an unknown target")
        for objective_id in panel["objective_ids"]:
            objective = objectives.get(objective_id)
            if objective is None:
                problems.append(f"{name} names an unknown objective")
                continue
            if rig_filter and not any(band_matches(band, accepted) for band in rig_filter["bandpasses"]
                                      for accepted in objective["bandpasses"]):
                problems.append(f"{name} lists an objective its filter cannot serve")
            low, high = objective["exposure"]["min_seconds"], objective["exposure"]["max_seconds"]
            if not low <= panel["exposure_seconds"] <= high:
                problems.append(f"{name} exposes {panel['exposure_seconds']} s, outside {low}-{high} s")
            limits = groups[objective["processing_group_id"]]["sampling_arcsec_per_pixel"]
            if not limits["min"] <= sampling(rig) <= limits["max"]:
                problems.append(f"{name} plans a rig whose sampling {sampling(rig):.2f}\"/px the objective "
                                "does not accept")
    return problems


@check("Asking for work")
def check_in_with_only_equipment(s: Suite) -> None:
    s.get("ready")
    rig_id, rig = s.register_rig(s.alice, s.project().rig(name="Check-in test rig"))
    plan = ask_for_work(s, s.alice, rig_id)
    if plan is None:
        return
    problems = plan_problems(plan, rig_id, rig["body"], s.project(plan["project_id"]).requirements)
    if problems:
        raise Fail("; ".join(problems[:3]))


@check("Sharing out the picture")
def identical_rigs_get_different_panels(s: Suite) -> None:
    project = s.project()
    limits = project.group["sampling_arcsec_per_pixel"]
    narrow = project.rig(sampling_arcsec=limits["min"], name="Long rig")
    width, height = field_of_view(narrow)
    target = project.target["footprint"]
    if target["width_degrees"] <= max(width, height) and target["height_degrees"] <= max(width, height):
        raise Skip("the project's first target fits the narrowest rig the objective allows")
    places = []
    for number, key in enumerate((s.alice, s.bob or s.alice), 1):
        rig_id, rig = s.register_rig(key, dict(narrow, name=f"Long rig {number}",
                                               filters=[filter_for(project.objective["bandpasses"][0])]))
        plan = ask_for_work(s, key, rig_id, [project.id])
        if plan is None:
            raise Skip("server said wait; no plans to compare")
        problems = plan_problems(plan, rig_id, rig["body"], project.requirements)
        if problems:
            raise Fail(f"rig {number}: " + "; ".join(problems[:3]))
        panel = plan["panels"][0]
        layout = panel.get("layout") or {}
        center = panel["footprint"]["center"]
        places.append(((layout.get("column"), layout.get("row")),
                       (round(center["ra_degrees"], 4), round(center["dec_degrees"], 4))))
    if places[0] == places[1]:
        raise Fail("two identical rigs got the same panel of a target larger than their field")


@check("Reporting progress")
def unsubmitted_captures_replace_last_report(s: Suite) -> None:
    project = s.project()
    rig_id, rig = s.register_rig(s.alice, project.rig(name="Reporting rig"))
    plan = ask_for_work(s, s.alice, rig_id, [project.id])
    if plan is None:
        raise Skip("server said wait; no panel to report against")
    panel = plan["panels"][0]
    objective_ids = panel["objective_ids"]

    def snapshot() -> tuple[dict[str, int], tuple[Any, Any]]:
        progress = s.progress(project)
        reported = {o["objective_id"]: o["reported_frames"] for o in progress["objectives"]
                    if o["objective_id"] in objective_ids}
        return reported, (progress["accepted_frames"], progress["accepted_integration_seconds"])

    def report(captures: list[dict[str, Any]]) -> None:
        body = {"equipment_id": rig_id, "observed_at": stamp(), "project_ids": [project.id],
                "assignment_id": plan["id"], "unsubmitted_captures": captures}
        expect(s.req("POST", "/me/checkins", s.alice, body), 200)

    frames = 5
    captures = [{"panel_id": panel["id"], "frames": frames,
                 "integration_seconds": frames * panel["exposure_seconds"], "last_captured_at": stamp()}]
    base, accepted = snapshot()
    report(captures)
    raised, accepted_after = snapshot()
    for objective_id in objective_ids:
        if raised[objective_id] - base[objective_id] != frames:
            raise Fail(f"reporting {frames} frames moved reported_frames by "
                       f"{raised[objective_id] - base[objective_id]}")
    report(captures)
    if snapshot()[0] != raised:
        raise Fail("repeating the same report changed reported_frames")
    report([])
    cleared, accepted_cleared = snapshot()
    if cleared != base:
        raise Fail(f"an empty report left reported_frames at {cleared}, expected {base}")
    if accepted_after != accepted or accepted_cleared != accepted:
        raise Fail("reported frames changed accepted frames or integration")


# Submissions and uploads

@check("Retry")
def repeated_submission_returns_existing(s: Suite) -> None:
    project = s.project()
    body = {"id": new_id(), "project_revision": project.revision,
            "artifacts": [s.artifact(project, os.urandom(1024))]}
    path = f"/projects/{project.id}/submissions"
    first = expect(s.req("POST", path, s.alice, body), 201).json
    again = expect(s.req("POST", path, s.alice, body), 200).json
    if again["id"] != first["id"] or again["manifest"] != first["manifest"] or \
            [u["id"] for u in again["uploads"]] != [u["id"] for u in first["uploads"]]:
        raise Fail("repeating a submission with the same id and body did not return the same submission")
    changed = dict(body, artifacts=[s.artifact(project, os.urandom(1024))])
    expect(s.req("POST", path, s.alice, changed), 409, code="id_conflict")


@check("Multipart")
def upload_parts_out_of_order(s: Suite) -> None:
    limits = s.get("capabilities")["limits"]
    project = s.project()
    data = os.urandom(min(2 * limits["max_chunk_bytes"] + 1024, limits["max_artifact_bytes"]))
    submission = s.submit(s.alice, project, [s.artifact(project, data)]).json
    upload = submission["uploads"][0]
    s.state["upload"] = (submission, upload, data)
    if upload["part_count"] < 2:
        s.upload_all(upload, data)
        raise Skip("server chose a single part; order cannot vary")
    receipts = {}
    for number in range(upload["part_count"], 0, -1):
        receipts[number] = expect(s.put_part(upload, number, data), 200).json
    session = expect(s.req("GET", f"/uploads/{upload['id']}", s.alice), 200).json
    received = sorted(p["part_number"] for p in session["received_parts"])
    if received != list(range(1, upload["part_count"] + 1)):
        raise Fail(f"server lists parts {received} after all were sent")
    s.state["receipt_1"] = receipts[1]


@check("Multipart")
def identical_part_retry(s: Suite) -> None:
    _, upload, data = s.get("upload")
    first = s.state.get("receipt_1")
    again = expect(s.put_part(upload, 1, data), 200).json
    if first is not None and again != first:
        raise Fail("an identical retry returned a different receipt")


@check("Multipart")
def changed_part_conflicts(s: Suite) -> None:
    _, upload, data = s.get("upload")
    other = bytes(b ^ 0xFF for b in data[:min(upload["part_size_bytes"], len(data))])
    r = s.req("PUT", f"/uploads/{upload['id']}/parts/1", s.alice, body=other,
              headers={"Content-Type": "application/octet-stream", "X-Part-SHA256": sha256(other)})
    expect(r, 409, code="part_conflict")


@check("Multipart")
def bad_part_digest_rejected(s: Suite) -> None:
    project = s.project()
    data = os.urandom(4096)
    submission = s.submit(s.alice, project, [s.artifact(project, data)]).json
    upload = submission["uploads"][0]
    s.state["incomplete"] = submission
    expect(s.put_part(upload, 1, data, digest="0" * 64), 422, code="digest_mismatch")
    if expect(s.req("GET", f"/uploads/{upload['id']}", s.alice), 200).json["received_parts"]:
        raise Fail("server recorded a part whose digest did not match")


@check("Multipart")
def finalize_incomplete_upload(s: Suite) -> None:
    submission = s.get("incomplete")
    expect(s.req("POST", f"/submissions/{submission['id']}/finalize", s.alice), 409, code="upload_incomplete")


@check("Finalize races")
def finalize_twice_returns_same_submission(s: Suite) -> None:
    submission, upload, data = s.get("upload")
    session = expect(s.req("GET", f"/uploads/{upload['id']}", s.alice), 200).json
    have = {p["part_number"] for p in session["received_parts"]}
    for number in range(1, upload["part_count"] + 1):
        if number not in have:
            expect(s.put_part(upload, number, data), 200)
    first = s.finalize(submission["id"])
    again = s.finalize(submission["id"])
    if first["id"] != again["id"] or first["id"] != submission["id"]:
        raise Fail("finalize returned a different submission")
    s.state["assessed"] = s.wait_until_assessed(submission["id"])


@check("Privacy")
def submissions_hidden_from_others(s: Suite) -> None:
    if not s.bob:
        raise Skip("needs --second-participant-key")
    submission, _, _ = s.get("upload")
    expect(s.req("GET", f"/submissions/{submission['id']}", s.bob), 403, 404)


@check("Recalibration")
def recalibration_counts_capture_once(s: Suite) -> None:
    result = s.get("assessed")
    project = s.project()
    if result["artifacts"][0]["state"] != "accepted":
        raise Skip(f"server assessed the test frame as {result['artifacts'][0]['state']}; cannot check credit")
    manifest = result["manifest"]["artifacts"][0]
    before = s.progress(project)
    data = os.urandom(2048)
    replacement = s.artifact(project, data, capture=(manifest["origin_id"], manifest["capture_id"]),
                             supersedes=manifest["id"])
    done = s.send(project, replacement, data)
    if done["artifacts"][0]["state"] != "accepted":
        raise Skip("server did not accept the recalibrated frame; cannot check replacement")
    after = s.progress(project)
    for name in ("accepted_frames", "accepted_integration_seconds"):
        if after[name] != before[name]:
            raise Fail(f"recalibrating one capture changed {name} from {before[name]} to {after[name]}")


# Stacked masters

def masters_project(s: Suite) -> Project:
    if not s.masters_project_id:
        raise Skip("needs --masters-project-id")
    project = s.project(s.masters_project_id)
    if project.requirements.get("deliverable") != "stacked_masters":
        raise Fail("--masters-project-id must take stacked masters")
    return project


@check("Stacked masters")
def deliverable_kind_must_match(s: Suite) -> None:
    project = masters_project(s)
    sub = s.artifact(project, os.urandom(1024))
    expect(s.submit(s.alice, project, [sub], status=None), 422, code="deliverable_mismatch")
    subs_project = s.project()
    master = s.master(subs_project, os.urandom(1024), s.stacked_subs(3))
    expect(s.submit(s.alice, subs_project, [master], status=None), 422, code="deliverable_mismatch")


@check("Stacked masters")
def master_below_min_sub_count_gets_no_credit(s: Suite) -> None:
    project = masters_project(s)
    minimum = project.requirements["master_rules"]["min_sub_count"]
    if minimum < 2:
        raise Skip("min_sub_count is 1; no master can be too small")
    before = s.accepted_frames(project)
    data = os.urandom(2048)
    r = s.submit(s.alice, project, [s.master(project, data, s.stacked_subs(minimum - 1))], status=None)
    if r.status == 422:
        expect(r, 422, code="too_few_subs")
        return
    expect(r, 201)
    for upload in r.json["uploads"]:
        s.upload_all(upload, data)
    s.finalize(r.json["id"])
    done = s.wait_until_assessed(r.json["id"])
    if done["artifacts"][0]["state"] != "rejected":
        raise Fail(f"a master of {minimum - 1} subs with min_sub_count {minimum} was "
                   f"{done['artifacts'][0]['state']}")
    if s.accepted_frames(project) != before:
        raise Fail("a master below min_sub_count earned credit")


@check("Stacked masters")
def accepted_master_credits_its_subs(s: Suite) -> None:
    project = masters_project(s)
    subs = s.stacked_subs(project.requirements["master_rules"]["min_sub_count"] + 1)
    before = s.accepted_frames(project)
    data = os.urandom(2048)
    done = s.send(project, s.master(project, data, subs), data)
    if done["artifacts"][0]["state"] != "accepted":
        raise Skip(f"server assessed the test master as {done['artifacts'][0]['state']}; cannot check credit")
    gained = s.accepted_frames(project) - before
    if gained != len(subs):
        raise Fail(f"a master of {len(subs)} subs added {gained} accepted frames")
    s.state["credited_subs"] = subs


@check("Stacked masters")
def master_reusing_credited_sub_earns_nothing(s: Suite) -> None:
    project = masters_project(s)
    credited = s.get("credited_subs")
    subs = [credited[0]] + s.stacked_subs(project.requirements["master_rules"]["min_sub_count"])
    before = s.accepted_frames(project)
    data = os.urandom(2048)
    r = s.submit(s.alice, project, [s.master(project, data, subs)], status=None)
    if r.status in (409, 422):
        return
    expect(r, 201)
    for upload in r.json["uploads"]:
        s.upload_all(upload, data)
    s.finalize(r.json["id"])
    s.wait_until_assessed(r.json["id"])
    if s.accepted_frames(project) != before:
        raise Fail("a master that reuses an already credited sub earned new credit")


# External delivery: files shared through a service such as Google Drive.
# Recording retrieval is a project-team task outside the API, so the checks stop
# at awaiting_retrieval.

def external(s: Suite, project: Project, artifact: dict[str, Any]) -> dict[str, Any]:
    providers = (project.requirements.get("external_delivery") or {}).get("providers") or ["https"]
    url = f"https://drive.example/file/{new_id()}"
    s.state.setdefault("external_urls", []).append(url)
    return dict(artifact, delivery="external",
                external={"provider": providers[0], "url": url, "shared_at": stamp(dt.timedelta(minutes=-5))})


@check("External delivery")
def external_needs_project_opt_in(s: Suite) -> None:
    project = s.project()
    if project.requirements.get("external_delivery"):
        raise Skip("--project-id accepts external delivery; nothing to reject")
    r = s.submit(s.alice, project, [external(s, project, s.artifact(project, b"x" * 1024))], status=None)
    expect(r, 422, code="external_delivery_not_accepted")


@check("External delivery")
def external_artifact_awaits_retrieval(s: Suite) -> None:
    if not s.external_project_id:
        raise Skip("needs --external-project-id")
    project = s.project(s.external_project_id)
    if not project.requirements.get("external_delivery"):
        raise Fail("--external-project-id does not accept external delivery")
    before = s.progress(project)
    data = os.urandom(4096)
    artifact = external(s, project, s.artifact(project, data))
    submission = s.submit(s.alice, project, [artifact]).json
    if any(u["artifact_id"] == artifact["id"] for u in submission["uploads"]):
        raise Fail("server opened an upload session for an external artifact")
    state = next(a["state"] for a in submission["artifacts"] if a["artifact_id"] == artifact["id"])
    if state != "awaiting_retrieval":
        raise Fail(f"external artifact starts as {state}")
    after = s.finalize(submission["id"])
    state = next(a["state"] for a in after["artifacts"] if a["artifact_id"] == artifact["id"])
    if state != "awaiting_retrieval":
        raise Fail(f"finalize moved an unretrieved external artifact to {state}")
    if s.progress(project)["accepted_frames"] != before["accepted_frames"]:
        raise Fail("an external artifact earned credit before retrieval")
    s.state["ext_submission"] = (project, submission["id"])


@check("External delivery")
def external_links_stay_private(s: Suite) -> None:
    project, submission_id = s.get("ext_submission")
    urls = s.get("external_urls")
    seen = [expect(s.req("GET", f"/projects/{project.id}"), 200, 401, 403).body,
            expect(s.req("GET", f"/projects/{project.id}/progress"), 200, 401, 403).body]
    if s.bob:
        expect(s.req("GET", f"/submissions/{submission_id}", s.bob), 403, 404)
        seen.append(expect(s.req("GET", f"/projects/{project.id}", s.bob), 200).body)
        seen.append(expect(s.req("GET", f"/projects/{project.id}/progress", s.bob), 200).body)
    for body in seen:
        if any(url.encode() in body for url in urls):
            raise Fail("a shared-file link appears in another participant's or a public view")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run AstroCollab server conformance checks.")
    parser.add_argument("--api-root", required=True, help="API root, such as https://collab.example/v1")
    parser.add_argument("--participant-key", required=True, help="API key of an active member of the projects")
    parser.add_argument("--project-id", required=True, help="open project that takes calibrated subs")
    parser.add_argument("--second-participant-key", help="key for a second member; enables privacy checks")
    parser.add_argument("--masters-project-id", help="open project that takes stacked masters")
    parser.add_argument("--external-project-id", help="open project that accepts external delivery")
    parser.add_argument("--pairing-code", help="unused pairing code for any test account; enables pairing checks")
    parser.add_argument("--second-pairing-code",
                        help="a second code for the same account; checks that re-pairing revokes the old key")
    parser.add_argument("--allow-http-loopback", action="store_true",
                        help="accept http:// URLs on localhost, for local test servers")
    parser.add_argument("--contract", help="path to an OpenAPI contract file")
    parser.add_argument("--schemas", help="validate bodies against standalone JSON Schemas in this "
                                          "directory, such as schemas/, instead of the OpenAPI components")
    args = parser.parse_args(argv)
    contract = Contract(args.contract or DEFAULT_CONTRACT, args.allow_http_loopback, args.schemas)
    suite = Suite(Client(args.api_root, contract), args.participant_key, args.project_id,
                  args.second_participant_key, args.masters_project_id, args.external_project_id,
                  args.pairing_code, args.second_pairing_code)
    results = suite.run()
    for result in results:
        line = f"{result.outcome} {result.name} [{result.row}]"
        print(line + (f": {result.message}" if result.message else ""))
    for warning in suite.client.warnings:
        print(f"WARN {warning}")
    counts = {o: sum(r.outcome == o for r in results) for o in ("PASS", "FAIL", "SKIP")}
    print(f"\n{counts['PASS']} passed, {counts['FAIL']} failed, {counts['SKIP']} skipped, "
          f"{len(suite.client.warnings)} warnings.")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
