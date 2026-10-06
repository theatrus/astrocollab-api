"""Capture AstroCollab 0.2 example payloads from Starfront's real server.

Drives Starfront's collaboration server (server/app.py) through one night with
FastAPI's TestClient, the way the Starfront program calls it, and writes every
request and response body to examples/. IDs, tokens and clocks are fixed,
so running it again produces identical files.

    /home/atrus/repos/starfront/.venv/bin/python tools/capture_starfront_examples.py \
        [--starfront ~/repos/starfront]
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import secrets
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "examples"

parser = argparse.ArgumentParser()
parser.add_argument("--starfront", default=str(Path.home() / "repos/starfront"))
args = parser.parse_args()
STARFRONT = Path(args.starfront).expanduser().resolve()

# -- fixed clock, IDs and secrets ------------------------------------------
CLOCK = [1791171000.0]          # 2026-10-05T03:30:00Z, a night in progress


def fake_time() -> float:
    CLOCK[0] += 1.0
    return CLOCK[0]


time.time = fake_time

_ids = itertools.count(1)
_tokens = itertools.count(1)


def fake_id() -> str:
    # Starfront's own IDs are 12 hex characters; these are too, counting up.
    return f"{next(_ids):012x}"


def fake_token(nbytes: int | None = 32) -> str:
    return f"EXAMPLE_ONLY_TOKEN_{next(_tokens):02d}_" + "x" * 8


secrets.token_urlsafe = fake_token

DATA = tempfile.mkdtemp(prefix="starfront-examples-")
ADMIN = "EXAMPLE_ONLY_ADMIN_TOKEN"
os.environ["ASTROCOLLAB_DATA"] = DATA
os.environ["ASTROCOLLAB_ADMIN_TOKEN"] = ADMIN
for key in [k for k in os.environ if k.startswith("ASTROCOLLAB_DISCORD")]:
    del os.environ[key]
sys.path.insert(0, str(STARFRONT))

from fastapi.testclient import TestClient  # noqa: E402

from astrocontrol import collab  # noqa: E402
import server.app as server  # noqa: E402
from server import auth  # noqa: E402

collab.new_id = fake_id

# Device sign-in needs Discord configured; nothing here talks to Discord, the
# person's identity is bound to the code directly, as the callback would.
server.DISCORD = auth.DiscordSettings(
    client_id="EXAMPLE_ONLY_CLIENT", client_secret="EXAMPLE_ONLY_SECRET",
    guild="000000000000000001", public_url="https://collab.example", owners=[])
server.discord = auth.Discord(server.DISCORD)

client = TestClient(server.app)
manifest: list[dict] = []

# Everything here is regenerated except extra/, which holds hand-written examples
# for routes Starfront does not have yet, listed in extra/manifest.json.
OUT.mkdir(exist_ok=True)
for item in OUT.iterdir():
    if item.name == "extra":
        continue
    if item.is_dir():
        shutil.rmtree(item)
    else:
        item.unlink()


def save(name: str, value, operation: str | None, direction: str,
         schema: str | None, status: int | None = None) -> None:
    path = OUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    entry: dict = {"file": name}
    if operation:
        entry["operationId"] = operation
    entry["direction"] = direction
    if status is not None:
        entry["status"] = status
    if schema:
        entry["schema"] = schema
    manifest.append(entry)


def call(method: str, path: str, *, name: str, operation: str,
         request_schema: str | None = None, response_schema: str | None = None,
         body=None, token: str | None = None, expect: int = 200,
         response_name: str | None = None, save_request: bool = True):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = client.request(method, path, json=body, headers=headers)
    if response.status_code != expect:
        raise SystemExit(f"{method} {path} -> {response.status_code}: {response.text}")
    if body is not None and save_request:
        save(f"{name}.request.json", body, operation, "request", request_schema)
    save(response_name or f"{name}.response.json", response.json(), operation,
         "response", response_schema, response.status_code)
    return response.json()


# -- discovery and sign-in --------------------------------------------------
call("GET", "/api/v1/health", name="health", operation="health", response_schema="Health")
call("GET", "/api/v1/auth", name="authStatus", operation="authStatus", response_schema="AuthStatus")
login = call("POST", "/api/v1/auth/login", name="authLogin", operation="authLogin",
             response_schema="LoginStarted")
code = login["code"]
# An alternate example: listed by type, not as the operation's main example.
call("GET", f"/api/v1/auth/poll?code={code}", name="authPoll", operation=None,
     response_schema="LoginPoll", response_name="authPoll.pending.response.json")
# What the Discord callback does once the person has signed in there.
server.store.upsert_user("000000000000000042", auth.new_user_token(), "Vega Observatory",
                         "", [])
server.store.bind_login(code, "000000000000000042")
done = call("GET", f"/api/v1/auth/poll?code={code}", name="authPoll", operation="authPoll",
            response_schema="LoginPoll", response_name="authPoll.done.response.json")
person = done["token"]
call("GET", "/api/v1/auth/me", name="authMe", operation="authMe", response_schema="Me",
     token=person)

# -- enrol a telescope ------------------------------------------------------
enrolled = call("POST", "/api/v1/agents", name="enrolTelescope", operation="enrolTelescope",
                request_schema="EnrolRequest", response_schema="TelescopeCreated",
                body={"name": "Vega 530"}, token=person)
rig = enrolled["token"]

# -- setup: two projects, made by the coordinator with their own tools -------
mosaic = call("POST", "/api/v1/projects", name="setup/project-mosaic",
              operation=None, token=ADMIN, response_schema="ProjectEnvelope", body={
                  "name": "M31 halo in narrowband", "kind": "mosaic",
                  "region": {"ra": 10.6847, "dec": 41.269, "width": 7.0, "height": 4.5,
                             "rotation": 35.0},
                  "goals": {"Ha": 10, "OIII": 10},
                  "requirements": {"filters": {"Ha": 7, "OIII": 7}, "maxHfr": 3.5,
                                   "minExposure": 120, "maxExposure": 600,
                                   "minMoonSeparation": 30, "minAltitude": 30},
                  "notes": "Faint H-alpha and OIII around M31. Calibrated subs only."})
single = call("POST", "/api/v1/projects", name="setup/project-single",
              operation=None, token=ADMIN, response_schema="ProjectEnvelope", body={
                  "name": "M51 in LRGB", "kind": "single",
                  "region": {"ra": 202.4696, "dec": 47.1952, "width": 0.25, "height": 0.2},
                  "goals": {"L": 20, "R": 5, "G": 5, "B": 5},
                  "requirements": {"filters": {"L": None, "R": None, "G": None, "B": None},
                                   "maxFocalLength": 400, "maxHfr": 3.0}})
mosaic_id = mosaic["project"]["id"]
single_id = single["project"]["id"]
# The setup files are context, not protocol examples.
manifest[:] = [entry for entry in manifest if not entry["file"].startswith("setup/")]

# -- the telescope's night ---------------------------------------------------
# Exactly what collabclient.profile() sends: RigProfile.payload(), which adds
# the computed scale and field.
profile = collab.RigProfile(
    name="Vega 530", focalLength=530.0, pixelSize=3.76, sensorWidth=6248,
    sensorHeight=4176, binning=1,
    filters={"Ha": 7.0, "OIII": 7.0, "SII": 7.0, "L": None},
    colour=False, rotation=None, typicalHfr=2.4, typicalGuideRms=0.62,
    exposures={"Ha": 300.0, "OIII": 300.0, "SII": 300.0, "L": 120.0},
    hoursPerNight=6.0, windowFrom="21:30", windowTo="04:30").payload()
call("POST", "/api/v1/agent/hello", name="hello", operation="hello",
     request_schema="HelloRequest", response_schema="HelloResponse", token=rig, body={
         "protocol": collab.PROTOCOL, "profile": profile,
         "presence": {"ra": 0.7123, "dec": 41.27, "state": "imaging",
                      "target": "M31 halo in narrowband", "project": mosaic_id}})

call("GET", "/api/v1/agent/projects", name="openProjects", operation="openProjects",
     response_schema="OpenProjects", token=rig)
joined = call("POST", f"/api/v1/agent/projects/{mosaic_id}/join", name="joinProject",
              operation="joinProject", request_schema="JoinRequest",
              response_schema="JoinResponse", token=rig,
              body={"hours": 0.0, "exposure": 0.0,
                    "exposures": {"Ha": 300.0, "OIII": 300.0, "SII": 300.0, "L": 120.0}})

query = {"night": "2026-10-05", "moon": 0.12, "moonUp": 0.3}
save("tonight.query.json", query, "tonight", "query", None)
tonight = call("GET", "/api/v1/agent/task?night=2026-10-05&moon=0.120&moonUp=0.300",
               name="tonight", operation="tonight", response_schema="TaskResponse", token=rig)
task = tonight["task"]

call("POST", f"/api/v1/agent/task/{task['id']}", name="setTaskState", operation="setTaskState",
     request_schema="TaskStateRequest", response_schema="TaskEnvelope", token=rig,
     body={"state": "accepted"})


def contribution(index: int, *, hfr_px: float, frames: int) -> dict:
    """One panel's night, as collabclient.report_pending() builds it."""
    cell = task["cells"][index]
    scale = profile["scale"]
    seconds = frames * 300.0
    return {
        "project": mosaic_id, "task": task["id"], "night": "2026-10-05",
        # The rig's own name for the filter; the server reads "OIII" as "O".
        "panel": str(index), "filterName": "OIII",
        "frames": frames, "seconds": seconds, "exposure": seconds / frames,
        "footprint": collab.Region(ra=cell["ra"], dec=cell["dec"], width=cell["width"],
                                   height=cell["height"],
                                   rotation=cell.get("rotation") or 0.0).payload(),
        "scale": scale, "focalLength": 530.0, "colour": False,
        "hfr": hfr_px * scale, "guideRms": 0.58, "bandpass": 7.0,
    }


share = task.get("share") or [0, 1]
call("POST", "/api/v1/agent/report", name="report", operation="report",
     request_schema="ReportRequest", response_schema="ReportResponse", token=rig,
     body={"contributions": [contribution(share[0], hfr_px=1.6, frames=11),
                             contribution(share[1 % len(share)] if len(share) > 1 else share[0] + 1,
                                          hfr_px=1.7, frames=11)]})
call("POST", "/api/v1/agent/report", name="report.rejected", operation=None,
     request_schema="ReportRequest", response_schema="ReportResponse", token=rig,
     body={"contributions": [{**contribution(share[0], hfr_px=3.4, frames=11),
                              "night": "2026-10-06"}]})

call("GET", "/api/v1/presence", name="presence", operation="presence",
     response_schema="PresenceResponse", token=rig)
call("GET", "/api/v1/agents", name="listTelescopes", operation="listTelescopes",
     response_schema="TelescopeList", token=person)

# -- errors -----------------------------------------------------------------
call("GET", "/api/v1/agent/task", name="errors/unknown-token", operation="tonight",
     response_schema="ErrorBody", token="EXAMPLE_ONLY_NOT_A_TOKEN", expect=401,
     response_name="errors/unknown-token.response.json")
# A 135 mm lens cannot meet the single project's 400 mm limit... a 1000 mm one
# exceeds it; say hello as the long rig, then try to join.
long_rig = call("POST", "/api/v1/agents", name="setup/enrol-long", operation=None,
                token=person, body={"name": "Vega 1000"})["token"]
manifest[:] = [entry for entry in manifest if not entry["file"].startswith("setup/")]
client.post("/api/v1/agent/hello", headers={"Authorization": f"Bearer {long_rig}"}, json={
    "protocol": collab.PROTOCOL,
    "profile": {**profile, "name": "Vega 1000", "focalLength": 1000.0}})
call("POST", f"/api/v1/agent/projects/{single_id}/join", name="errors/cannot-join",
     operation="joinProject", response_schema="ErrorBody", token=long_rig, expect=409,
     body={"hours": 0, "exposure": 0, "exposures": {"L": 120.0}}, save_request=False,
     response_name="errors/cannot-join.response.json")

extra = OUT / "extra" / "manifest.json"
if extra.exists():
    manifest.extend(json.loads(extra.read_text(encoding="utf-8")))
(OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
shutil.rmtree(DATA, ignore_errors=True)
print(f"wrote {len(manifest)} files to {OUT.relative_to(ROOT)}")
