"""Walk through the AstroCollab contributor API against a running server.

A contributor pairs (or uses a key), lists their projects, describes a rig,
checks in for an assignment, submits one calibrated exposure, and reads the
credit. The manifest comes from examples/; the client fills in the IDs the
server handed out.

    python -m reference.client --api-root http://127.0.0.1:8080/v1 --key ALICE_SECRET

Use --pairing-code instead of --key to pair first. With --sample, three rigs
check in and the client prints what each should image.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import time
import urllib.error
import urllib.request
import uuid

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"

RIGS = [
    # A 400 mm f/5 refractor and two 2000 mm SCTs, all with a 6248×4176 3.76 µm mono camera.
    ("Rig 400 mm", 400),
    ("Rig 2000 mm A", 2000),
    ("Rig 2000 mm B", 2000),
]
FILTERS = [("H-alpha", "narrowband", 656.3, 3.0), ("OIII", "narrowband", 500.7, 3.0),
           ("Luminance", "luminance", 550.0, 300.0)]


class ApiError(Exception):
    def __init__(self, status: int, problem: dict):
        super().__init__(f"{status} {problem.get('code')}: {problem.get('detail')}")
        self.status, self.problem = status, problem


class Client:
    """A thin HTTP client: one API key, JSON in and out."""

    def __init__(self, api_root: str, key: str | None, log=print):
        self.api_root, self.key, self.log = api_root.rstrip("/"), key, log

    def call(self, method: str, path: str, body=None, note: str = "", headers: dict | None = None,
             raw: bytes | None = None):
        headers = dict(headers or {})
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
            headers.setdefault("Content-Type", "application/json")
        request = urllib.request.Request(self.api_root + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request) as response:
                status, text = response.status, response.read()
        except urllib.error.HTTPError as error:
            with error:
                text = error.read()
            self.log(f"{method} {path} -> {error.code}  {note}")
            raise ApiError(error.code, json.loads(text or b"{}")) from None
        self.log(f"{method} {path} -> {status}  {note}")
        return json.loads(text) if text else None


def pair(api_root: str, code: str, client_name: str, log=print) -> str:
    """Trade a pairing code for an API key. Real clients keep installation_id
    and the key in a credential store; pairing again replaces the old key."""
    body = {"pairing_code": code, "installation_id": str(uuid.uuid4()), "client_name": client_name}
    paired = Client(api_root, None, log).call("POST", "/pair", body, "Pair; receive an API key once.")
    return paired["api_key"]


def example(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def rig(name: str, focal_mm: float, filter_ids: list) -> dict:
    return {
        "name": name, "sensor_width_pixels": 6248, "sensor_height_pixels": 4176,
        "pixel_size_um": 3.76, "focal_length_mm": focal_mm, "color_state": "mono",
        "rotation": "fixed", "confirmed_position_angle_degrees": 0,
        "filters": [{"id": fid, "name": n, "kind": kind,
                     "bandpasses": [{"name": n, "center_nm": c, "width_nm": w}]}
                    for fid, (n, kind, c, w) in zip(filter_ids, FILTERS)],
    }


def run(api_root: str, key: str, log=print) -> dict:
    """Run the walkthrough. Returns the project's progress at the end."""
    me = Client(api_root, key, log)
    now = datetime.now(timezone.utc).replace(microsecond=0)

    log("\n# Discover the server and your projects")
    me.call("GET", "/capabilities", note="Read limits.")
    projects = me.call("GET", "/me/projects", note="List the projects you joined on the web.")
    project_id = next(p["project_id"] for p in projects["items"] if p["title"] == "Sample sky survey")
    project = me.call("GET", f"/projects/{project_id}", note="Read the requirements.")

    log("\n# Describe the rig")
    equipment_id = str(uuid.uuid4())
    config = rig("Rig 400 mm", 400, [str(uuid.uuid4()) for _ in FILTERS])
    equipment = me.call("PUT", f"/me/equipment/{equipment_id}", config,
                        "Describe the camera, telescope and filters once.")

    log("\n# Ask what to image")
    result = me.call("POST", "/me/checkins", {"equipment_id": equipment_id,
                                              "project_ids": [project_id], "observed_at": stamp(now)},
                     "Check in; the server assigns a panel.")
    assignment = result["assignment"]
    panel = assignment["panels"][0]
    for line in describe("Rig 400 mm", result, config):
        log(f"  {line}")

    log("\n# Submit one calibrated exposure")
    data = synthetic_frame(2_500_000)
    digest = hashlib.sha256(data).hexdigest()
    objective = next(o for o in project["requirements"]["objectives"] if o["id"] in panel["objective_ids"])
    used = next(f for f in config["filters"] if f["id"] == panel["filter_id"])
    manifest = example("createSubmission.request.json")
    artifact = manifest["artifacts"][0]
    artifact.update(
        id=str(uuid.uuid4()), capture_id=str(uuid.uuid4()), objective_ids=panel["objective_ids"],
        processing_group_id=objective["processing_group_id"],
        equipment={"equipment_id": equipment_id, "revision": equipment["revision"]},
        filter_id=used["id"], bandpasses=used["bandpasses"], exposure_seconds=panel["exposure_seconds"],
        captured_at=stamp(now - timedelta(hours=1)), size_bytes=len(data), sha256=digest,
        assignment_id=assignment["id"], panel_id=panel["id"])
    artifact["solve"].update(artifact_sha256=digest, center=panel["footprint"]["center"])
    manifest.update(id=str(uuid.uuid4()), project_revision=assignment["project_revision"])
    submission = me.call("POST", f"/projects/{project_id}/submissions", manifest,
                         "Send the manifest; get an upload session.")
    upload = submission["uploads"][0]
    size = upload["part_size_bytes"]
    for number in reversed(range(1, upload["part_count"] + 1)):  # Order does not matter.
        part_bytes = data[(number - 1) * size:number * size]
        me.call("PUT", f"/uploads/{upload['id']}/parts/{number}", raw=part_bytes,
                headers={"Content-Type": "application/octet-stream",
                         "X-Part-SHA256": hashlib.sha256(part_bytes).hexdigest()},
                note=f"Upload part {number} of {upload['part_count']}.")
    submission = me.call("POST", f"/submissions/{submission['id']}/finalize",
                         note="Finalize; the server starts assessment.")
    while submission["state"] != "complete":
        time.sleep(0.5)
        submission = me.call("GET", f"/submissions/{submission['id']}", note="Poll until complete.")
    outcome = submission["artifacts"][0]
    log(f"  artifact: {outcome['state']} {outcome['reason_codes'] or ''}")

    log("\n# Report frames not yet submitted, and keep going")
    exposure = panel["exposure_seconds"]
    result = me.call("POST", "/me/checkins", {
        "equipment_id": equipment_id, "assignment_id": assignment["id"],
        "unsubmitted_captures": [{"panel_id": panel["id"], "frames": 5,
                                  "integration_seconds": 5 * exposure}],
        "observed_at": stamp(now + timedelta(minutes=30))}, "Check in with 5 frames waiting.")
    log(f"  action: {result['action']}")
    progress = me.call("GET", f"/projects/{project_id}/progress", note="Read the totals.")
    done = next(o for o in progress["objectives"] if o["objective_id"] == objective["id"])
    log(f"  accepted {done['accepted_frames']} frames ({done['accepted_integration_seconds']:g} s), "
        f"{done['reported_frames']} reported, goal {objective['goal'].get('accepted_frames')} "
        f"frames on each panel")
    return progress


def describe(name: str, result: dict, config: dict) -> list[str]:
    """Say an assignment in plain words: one line per panel, in order."""
    assignment = result.get("assignment")
    if assignment is None:
        return [f"{name}: {result['action']} ({', '.join(result['reason_codes'])})"]
    lines = []
    for number, panel in enumerate(assignment["panels"]):
        used = next(f for f in config["filters"] if f["id"] == panel["filter_id"])
        band = used.get("name") or " + ".join(b["name"] for b in used["bandpasses"])
        grid = panel["layout"]
        if grid["columns"] * grid["rows"] == 1:
            where = f"1 panel {panel['footprint']['width_degrees']:.2g}°×" \
                    f"{panel['footprint']['height_degrees']:.2g}°"
        else:
            index = (grid["row"] - 1) * grid["columns"] + grid["column"]
            where = f"panel {index} of {grid['columns'] * grid['rows']} " \
                    f"(column {grid['column']}, row {grid['row']})"
        frames = f"{panel['suggested_frames']} × {panel['exposure_seconds']:g} s"
        lead = f"{name}: image" if number == 0 else f"{' ' * len(name)}  then"
        lines.append(f"{lead} {panel['target_name']}, {where}, {band}, {frames}")
    return lines


def sample(api_root: str, key: str, log=print) -> list:
    """Describe three rigs, check each in, and say what each should image."""
    me = Client(api_root, key, log)
    now = stamp(datetime.now(timezone.utc))
    lines = []
    for name, focal in RIGS:
        equipment_id = str(uuid.uuid4())
        config = rig(name, focal, [str(uuid.uuid4()) for _ in FILTERS])
        me.call("PUT", f"/me/equipment/{equipment_id}", config, f"Describe {name}.")
        result = me.call("POST", "/me/checkins", {"equipment_id": equipment_id, "observed_at": now},
                         f"Check in {name}.")
        lines += describe(name, result, config)
    log("")
    for line in lines:
        log(line)
    return lines


def synthetic_frame(size: int) -> bytes:
    """Stand-in bytes for a calibrated FITS file. The reference server does not decode them."""
    seed = hashlib.sha256(b"astrocollab").digest()
    return (seed * (size // len(seed) + 1))[:size]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--api-root", default="http://127.0.0.1:8080/v1")
    parser.add_argument("--key", "--participant-key", help="API key.")
    parser.add_argument("--pairing-code", help="Pair first instead of passing a key.")
    parser.add_argument("--sample", action="store_true",
                        help="Check in three rigs and print what each should image.")
    args = parser.parse_args()
    key = args.key
    if args.pairing_code:
        key = pair(args.api_root, args.pairing_code, "reference client")
    if not key:
        parser.error("give --key or --pairing-code")
    if args.sample:
        sample(args.api_root, key)
    else:
        run(args.api_root, key)


if __name__ == "__main__":
    try:
        main()
    except ApiError as error:
        raise SystemExit(f"Stopped: {error}")
