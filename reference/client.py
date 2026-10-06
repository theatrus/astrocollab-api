"""An example telescope program: one night against an AstroCollab 0.2 server.

    python -m reference.client --server http://127.0.0.1:8080 --pairing-code CODE
    python -m reference.client --server http://127.0.0.1:8080 --person-token TOKEN

It gets a telescope token, says hello, browses, joins the first project it can
help, asks what to shoot tonight, reports two panels and looks at who is on the
sky. Each step prints the request and what came back.
"""
from __future__ import annotations

import argparse
import datetime
import json
import urllib.error
import urllib.request
from typing import Any

#: A 530 mm refractor with a full-frame mono camera and narrowband filters.
PROFILE = {
    "name": "Example 530",
    "focalLength": 530.0, "pixelSize": 3.76, "sensorWidth": 6248, "sensorHeight": 4176,
    "binning": 1, "colour": False, "rotation": None,
    "filters": {"Ha": 7.0, "OIII": 7.0, "SII": 7.0, "L": None},
    "exposures": {"Ha": 300.0, "OIII": 300.0, "SII": 300.0, "L": 120.0},
    "typicalHfr": 2.4, "typicalGuideRms": 0.6,
    "hoursPerNight": 6.0, "windowFrom": "21:30", "windowTo": "04:30",
}


class Server:
    def __init__(self, base: str, log=print):
        self.base = base.rstrip("/")
        self.log = log

    def call(self, method: str, path: str, token: str | None = None, body: Any = None) -> Any:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request) as response:
                status, answer = response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as error:
            with error:
                status, answer = error.code, json.loads(error.read() or b"null")
        self.log(f"{method} {path} -> {status}")
        if status != 200:
            raise RuntimeError(f"{method} {path}: {answer.get('detail') if isinstance(answer, dict) else answer}")
        return answer


def run(server_url: str, pairing_code: str | None = None, person_token: str | None = None,
        night: str | None = None, log=print) -> dict[str, Any]:
    """Run one night and return the report's reply."""
    server = Server(server_url, log)
    health = server.call("GET", "/api/v1/health")
    log(f"  protocol {health['protocol']}, offers {', '.join(health.get('features') or [])}")

    if pairing_code:
        made = server.call("POST", "/api/v1/pair", body={"code": pairing_code, "name": PROFILE["name"]})
    elif person_token:
        made = server.call("POST", "/api/v1/agents", person_token, {"name": PROFILE["name"]})
    else:
        raise SystemExit("give --pairing-code or --person-token")
    token = made["token"]
    log(f"  telescope {made['agent']['id']} enrolled; its token is kept, never printed")

    server.call("POST", "/api/v1/agent/hello", token,
                {"protocol": 1, "profile": PROFILE,
                 "presence": {"state": "idle", "target": "", "project": ""}})

    projects = server.call("GET", "/api/v1/agent/projects", token)["projects"]
    for project in projects:
        log(f"  {project['name']} ({project['kind']}): {project['compatibility']['summary']}")
    usable = [p for p in projects if p["compatibility"]["ok"]]
    if not usable:
        raise SystemExit("this rig cannot help any open project")
    project = usable[0]
    joined = server.call("POST", f"/api/v1/agent/projects/{project['id']}/join", token,
                         {"hours": 0, "exposures": PROFILE["exposures"]})["task"]
    log(f"  joined {project['name']}: {len(joined['cells'])} cells of this rig's own frame")

    night = night or datetime.date.today().isoformat()
    tonight = server.call("GET", f"/api/v1/agent/task?night={night}&moon=0.12&moonUp=0.3", token)
    share = next(s for s in tonight["tasks"] if s["project"] == project["id"])
    visit = share["visit"]
    frames = next(iter(visit["frames"].values()))
    log(f"  tonight: {len(share['share'])} panels, {visit.get('filter') or 'all filters'}, "
        f"{frames} frames of {visit['seconds'] / frames:g} s on each")

    contributions = []
    for index in share["share"][:2]:
        cell = share["cells"][index]
        exposure = visit["seconds"] / frames
        contributions.append({
            "project": project["id"], "task": share["id"], "night": night, "panel": str(index),
            "filterName": visit.get("filter") or next(iter(visit["frames"])),
            "frames": frames, "seconds": frames * exposure, "exposure": exposure,
            "footprint": {k: cell[k] for k in ("ra", "dec", "width", "height", "rotation")},
            "scale": 1.46, "focalLength": PROFILE["focalLength"], "hfr": 2.3, "guideRms": 0.6,
            "moonIllumination": 0.12, "moonSeparation": 95.0, "calibrated": True,
            "bandpass": 7.0, "colour": False,
        })
    reply = server.call("POST", "/api/v1/agent/report", token, {"contributions": contributions})
    for entry, recorded in zip(contributions, reply["recorded"]):
        log(f"  panel {entry['panel']}: {recorded['verdict']['summary']}")

    sky = server.call("GET", "/api/v1/presence", token)
    log(f"  {sky['online']} telescopes online, belonging to {sky['people']} people")
    return reply


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one night against an AstroCollab 0.2 server.")
    parser.add_argument("--server", default="http://127.0.0.1:8080")
    parser.add_argument("--pairing-code", help="a code from the server's web pages")
    parser.add_argument("--person-token", help="a signed-in person's token, to enrol the telescope")
    parser.add_argument("--night", help="the night's name, such as 2026-10-05 (default: today)")
    args = parser.parse_args()
    run(args.server, args.pairing_code, args.person_token, args.night)


if __name__ == "__main__":
    main()
