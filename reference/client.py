"""Walk through the AstroCollab flow against a running server.

The owner publishes a project. A participant joins, offers equipment and time,
checks in, uploads one calibrated exposure and reads the credit. Request
bodies come from examples/; the client swaps in fresh IDs and current dates so
that it can run more than once.

    python -m reference.client --api-root http://127.0.0.1:8080/v1 \\
        --owner-key OWNER_SECRET --participant-key ALICE_SECRET

Use --owner-pairing-code and --participant-pairing-code (or --pairing-code)
to pair first instead of passing keys.
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

# Example IDs that must be new on each run.
FRESH = {
    "00000000-0000-4000-8000-000000000001": "project",
    "00000000-0000-4000-8000-000000000007": "equipment",
    "00000000-0000-4000-8000-000000000010": "capture",
    "00000000-0000-4000-8000-000000000011": "artifact",
    "00000000-0000-4000-8000-000000000012": "submission",
}


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
        if method == "POST" and self.key:  # Every authenticated POST needs a key.
            headers.setdefault("Idempotency-Key", str(uuid.uuid4()))  # New key per new action.
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.api_root + path, data=data, method=method,
                                         headers=headers)
        try:
            with urllib.request.urlopen(request) as response:
                status, text, etag = response.status, response.read(), response.headers.get("ETag")
        except urllib.error.HTTPError as error:
            with error:
                text = error.read()
            self.log(f"{method} {path} -> {error.code}  {note}")
            raise ApiError(error.code, json.loads(text or b"{}")) from None
        self.log(f"{method} {path} -> {status}  {note}")
        return (json.loads(text) if text else None), etag


def pair(api_root: str, code: str, client_name: str, log=print) -> str:
    """Trade a pairing code for an API key. Real clients keep installation_id
    and the key in a credential store; pairing again replaces the old key."""
    body = {"pairing_code": code, "installation_id": str(uuid.uuid4()), "client_name": client_name}
    paired, _ = Client(api_root, None, log).call("POST", "/pair", body,
                                                  "Pair this client; receive an API key once.")
    return paired["api_key"]


def example(name: str, ids: dict) -> dict:
    text = (EXAMPLES / name).read_text(encoding="utf-8")
    for old, new in ids.items():
        text = text.replace(old, new)
    return json.loads(text)


def stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def run(api_root: str, owner_key: str, participant_key: str, log=print) -> dict:
    """Run the whole flow. Returns the final project progress."""
    owner, alice = Client(api_root, owner_key, log), Client(api_root, participant_key, log)
    ids = {old: str(uuid.uuid4()) for old in FRESH}
    now = datetime.now(timezone.utc).replace(microsecond=0)

    log("\n# Discover the server")
    capabilities, _ = alice.call("GET", "/capabilities", note="Read limits and features.")

    log("\n# Owner: publish the project")
    create = example("createProject.request.json", ids)
    create["requirements"]["capture_deadline"] = stamp(now + timedelta(days=30))
    create["requirements"]["submission_deadline"] = stamp(now + timedelta(days=45))
    created, _ = owner.call("POST", "/projects", create, "Create a draft and owner membership.")
    project_id = created["project"]["id"]
    _, draft_etag = owner.call("GET", f"/projects/{project_id}/draft", note="Read the draft ETag.")
    revision, _ = owner.call("POST", f"/projects/{project_id}/publish",
                             headers={"If-Match": draft_etag}, note="Publish revision 1.")

    log("\n# Participant: join")
    terms = revision["requirements"]["terms"]
    part, _ = alice.call("POST", f"/projects/{project_id}/participations", {"accepted_terms": terms},
                         "Join with consent to the current terms.")
    pid = part["id"]
    ids["00000000-0000-4000-8000-000000000003"] = pid

    log("\n# Participant: offer equipment and time")
    equipment_id = ids["00000000-0000-4000-8000-000000000007"]
    registered, _ = alice.call("PUT", f"/participations/{pid}/equipment/{equipment_id}",
                               example("registerEquipment.request.json", ids),
                               "Register the camera and telescope.", {"If-None-Match": "*"})
    capacity = example("setCapacity.request.json", ids)
    capacity.update(month=now.strftime("%Y-%m"), usage_observed_at=stamp(now), availability=[
        {"start": stamp(now + timedelta(hours=2)), "end": stamp(now + timedelta(hours=7))}])
    alice.call("PUT", f"/participations/{pid}/capacity", capacity,
               "Offer 10 rig-hours this month.", {"If-None-Match": "*"})
    policy = example("setPlanningPolicy.request.json", ids)
    policy["accepted_terms"] = terms
    alice.call("PUT", f"/participations/{pid}/planning-policy", policy,
               "Save the sky regions and limits the user approved.", {"If-None-Match": "*"})
    checkin = example("checkIn.request.json", ids)
    checkin["observed_at"] = stamp(now)
    advice, _ = alice.call("POST", f"/participations/{pid}/checkins", checkin,
                           "Check in; the server answers with planning advice.")
    log(f"  advice: {advice['action']}")

    log("\n# Participant: submit one calibrated exposure")
    data = synthetic_frame(2_500_000)
    digest = hashlib.sha256(data).hexdigest()
    manifest = example("createSubmission.request.json", ids)
    artifact = manifest["artifacts"][0]
    for field in ("recommendation_id", "panel_id"):  # This run used no recommendation.
        artifact.pop(field)
    artifact.update(size_bytes=len(data), sha256=digest, captured_at=stamp(now - timedelta(hours=1)))
    artifact["solve"]["artifact_sha256"] = digest
    submission, _ = alice.call("POST", f"/projects/{project_id}/submissions", manifest,
                               "Send the manifest; get an upload session.")
    upload = submission["uploads"][0]
    size = upload["part_size_bytes"]
    for number in reversed(range(1, upload["part_count"] + 1)):  # Order does not matter.
        part_bytes = data[(number - 1) * size:number * size]
        alice.call("PUT", f"/uploads/{upload['id']}/parts/{number}", raw=part_bytes,
                   headers={"Content-Type": "application/octet-stream",
                            "X-Part-SHA256": hashlib.sha256(part_bytes).hexdigest()},
                   note=f"Upload part {number} of {upload['part_count']}.")
    job, _ = alice.call("POST", f"/submissions/{submission['id']}/finalize",
                        note="Finalize; the server queues assessment.")
    while job["state"] in ("queued", "running"):
        time.sleep(job["poll_after_seconds"])
        job, _ = alice.call("GET", f"/jobs/{job['id']}", note=f"Poll the job.")
    result = job["result"]["artifacts"][0]
    log(f"  artifact: {result['state']} {result['reason_codes'] or ''}")

    log("\n# Anyone: read progress")
    progress, _ = alice.call("GET", f"/projects/{project_id}/progress", note="Read accepted totals.")
    objective = progress["objectives"][0]
    log(f"  accepted {objective['accepted_frames']} of {objective['goal'].get('accepted_frames')} "
        f"frames, {objective['accepted_integration_seconds']:g} s of integration")
    return progress


def synthetic_frame(size: int) -> bytes:
    """Stand-in bytes for a calibrated FITS file. The reference server does not decode them."""
    seed = hashlib.sha256(b"astrocollab").digest()
    return (seed * (size // len(seed) + 1))[:size]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--api-root", default="http://127.0.0.1:8080/v1")
    parser.add_argument("--owner-key", help="Owner API key.")
    parser.add_argument("--participant-key", help="Participant API key.")
    parser.add_argument("--owner-pairing-code", help="Pair as the owner instead of using a key.")
    parser.add_argument("--participant-pairing-code", "--pairing-code",
                        help="Pair as the participant instead of using a key.")
    args = parser.parse_args()
    owner_key = args.owner_key
    participant_key = args.participant_key
    if args.owner_pairing_code:
        print("# Owner: pair")
        owner_key = pair(args.api_root, args.owner_pairing_code, "reference client (owner)")
    if args.participant_pairing_code:
        print("# Participant: pair")
        participant_key = pair(args.api_root, args.participant_pairing_code, "reference client")
    if not owner_key or not participant_key:
        parser.error("give a key or pairing code for both the owner and the participant")
    run(args.api_root, owner_key, participant_key)


if __name__ == "__main__":
    try:
        main()
    except ApiError as error:
        raise SystemExit(f"Stopped: {error}")
