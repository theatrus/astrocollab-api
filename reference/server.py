"""A small AstroCollab 0.2 server, for testing programs and for reading.

Everything lives in memory and is lost when the server stops. It serves plain
HTTP; a real server must use HTTPS. The rules themselves are in rules.py.

    python -m reference.server --port 8080

prints a person token, a pairing code and the sample projects.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
from pathlib import Path
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from reference import rules

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"
SCHEMA_BASE = "https://astrocollabapi.com/schemas/"
PROTOCOL = 1
VERSION = "0.2.0-draft.1-reference"
LOGIN_SECONDS = 600.0           # how long a sign-in code stays good
PAIRING_SECONDS = 3600.0        # how long a pairing code stays good
BAD_CODES_PER_MINUTE = 5        # bad pairing codes from one address before 429
ONLINE_SECONDS = 25 * 60        # a telescope counts as online this long after a call
LISTED_SECONDS = 24 * 3600      # presence lists telescopes seen this recently
HOLD_SECONDS = 20 * 3600        # a list dealt without a night holds this long
SAMPLES = Path(__file__).with_name("sample_projects.json")


class Problem(Exception):
    """An error reply: a status and a sentence for the operator, or a field list."""

    def __init__(self, status: int, detail: Any):
        super().__init__(str(detail))
        self.status = status
        self.detail = detail


def _new_id() -> str:
    return secrets.token_hex(6)


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Checking request bodies against schemas/
# ---------------------------------------------------------------------------

def _registry() -> Registry:
    resources = []
    for path in SCHEMAS.glob("*.schema.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        resources.append((schema["$id"], Resource.from_contents(schema)))
    return Registry().with_resources(resources)


REGISTRY = _registry()


def _wants_number(schema: dict[str, Any]) -> str | None:
    """'number' or 'integer' when a schema accepts one, else None."""
    options = [schema] + list(schema.get("anyOf", []))
    for option in options:
        if option.get("type") in ("number", "integer"):
            return option["type"]
    return None


def _nullable(schema: dict[str, Any]) -> bool:
    return any(option.get("type") == "null" for option in schema.get("anyOf", []))


def check(schema_name: str, body: Any) -> Any:
    """The body, checked against its schema, or a 422 Problem.

    Numbers sent as blank or unreadable strings are read as unknown rather than
    refused, as the protocol recommends: null where the field allows it, absent
    where it does not.
    """
    validator = Draft202012Validator({"$ref": SCHEMA_BASE + f"{schema_name}.schema.json"},
                                     registry=REGISTRY)
    body = copy.deepcopy(body)
    for _ in range(50):
        errors = list(validator.iter_errors(body))
        # A map of numbers, such as filter bandpasses, is checked as a whole.
        maps = [e for e in errors if e.validator == "unevaluatedProperties"
                and isinstance(e.instance, dict)
                and _wants_number(e.schema.get("unevaluatedProperties") or {})]
        for error in maps:
            values = error.schema["unevaluatedProperties"]
            for key, value in list(error.instance.items()):
                if isinstance(value, str):
                    number = rules.number(value)
                    if number is None and not _nullable(values):
                        del error.instance[key]
                    else:
                        error.instance[key] = number
        fixable = [e for e in errors if isinstance(e.instance, str) and _wants_number(e.schema)
                   and e.absolute_path]
        if not fixable and not maps:
            break
        for error in fixable:
            *parent_path, key = list(error.absolute_path)
            parent = body
            for part in parent_path:
                parent = parent[part]
            value = rules.number(error.instance)
            if value is not None and _wants_number(error.schema) == "integer":
                value = int(value)
            if value is None and not _nullable(error.schema):
                del parent[key]
            else:
                parent[key] = value
    if errors:
        raise Problem(422, [{"loc": ["body", *list(e.absolute_path)], "msg": e.message,
                             "type": str(e.validator)} for e in errors])
    return body


# ---------------------------------------------------------------------------
# Everything the server knows
# ---------------------------------------------------------------------------

class State:
    """All server data, behind one lock. `now` can be replaced in tests."""

    def __init__(self, base_url: Callable[[], str]):
        self.lock = threading.RLock()
        self.now: Callable[[], float] = time.time
        self.base_url = base_url
        self.people: dict[str, dict[str, Any]] = {}
        self.tokens: dict[str, tuple[str, str]] = {}          # hash -> (kind, id)
        self.logins: dict[str, dict[str, Any]] = {}           # sign-in code -> state
        self.pairing: dict[str, dict[str, Any]] = {}          # hash -> code state
        self.bad_codes: dict[str, list[float]] = {}           # address -> times of bad codes
        self.telescopes: dict[str, dict[str, Any]] = {}
        self.projects: dict[str, dict[str, Any]] = {}
        self.shares: dict[str, dict[str, Any]] = {}
        self.records: dict[str, dict[str, Any]] = {}
        self.record_keys: dict[tuple, str] = {}

    # -- people and tokens -------------------------------------------------
    def person(self, name: str) -> dict[str, Any]:
        for person in self.people.values():
            if person["name"] == name:
                return person
        person = {"id": _new_id(), "name": name}
        self.people[person["id"]] = person
        return person

    def mint(self, kind: str, owner_id: str) -> str:
        token = secrets.token_urlsafe(32)
        self.tokens[_hash(token)] = (kind, owner_id)
        return token

    def who(self, authorization: str, kind: str) -> dict[str, Any]:
        """The person or telescope behind a bearer token, or 401."""
        token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        found = self.tokens.get(_hash(token)) if token else None
        if found is None or found[0] != kind:
            what = "a person's token from signing in" if kind == "person" else "a telescope token"
            raise Problem(401, f"this route needs {what}")
        table = self.people if kind == "person" else self.telescopes
        if found[1] not in table:
            raise Problem(401, "that token no longer belongs to anybody")
        return table[found[1]]

    def telescope_view(self, telescope: dict[str, Any]) -> dict[str, Any]:
        return {key: telescope[key] for key in
                ("id", "name", "owner", "owner_id", "created", "seen", "profile", "presence")}

    def enrol(self, person: dict[str, Any], name: str) -> dict[str, Any]:
        telescope = {"id": _new_id(), "name": name, "owner": person["name"],
                     "owner_id": person["id"], "created": self.now(), "seen": 0.0,
                     "profile": {}, "presence": {}}
        self.telescopes[telescope["id"]] = telescope
        return {"agent": self.telescope_view(telescope), "token": self.mint("telescope", telescope["id"])}

    # -- projects ----------------------------------------------------------
    def add_project(self, name: str, region: dict[str, Any], kind: str, goals: dict[str, float],
                    requirements: dict[str, Any], notes: str = "") -> str:
        project = {
            "id": _new_id(), "name": name, "coordinator": "reference tools", "ownerId": "",
            "region": {"ra": float(region["ra"]), "dec": float(region["dec"]),
                       "width": float(region["width"]), "height": float(region["height"]),
                       "rotation": float(region.get("rotation") or 0.0)},
            "kind": "single" if kind == "single" else "mosaic",
            "requirements": rules.requirements(requirements),
            "goals": {letter: float(hours) for letter, hours in rules.by_letter(goals).items()},
            "notes": notes, "status": "open", "created": self.now(),
        }
        self.projects[project["id"]] = project
        return project["id"]

    def shares_of(self, telescope_id: str, states=("offered", "accepted")) -> list[dict[str, Any]]:
        return sorted((s for s in self.shares.values()
                       if s["agent"] == telescope_id and s["state"] in states),
                      key=lambda s: (s["issued"], s["id"]))

    def shares_on(self, project_id: str) -> list[dict[str, Any]]:
        return [s for s in self.shares.values()
                if s["project"] == project_id and s["state"] in ("offered", "accepted")]

    def accepted_records(self, project_id: str) -> list[dict[str, Any]]:
        return [r for r in self.records.values() if r["project"] == project_id]

    def collected(self, project_id: str) -> dict[str, float]:
        hours: dict[str, float] = {}
        for record in self.accepted_records(project_id):
            if record["accepted"]:
                hours[record["filter"]] = hours.get(record["filter"], 0.0) + record["seconds"] / 3600.0
        return {letter: round(value, 2) for letter, value in hours.items()}

    # -- dealing -----------------------------------------------------------
    def _current(self, share: dict[str, Any], night: str | None) -> bool:
        """Whether a share's list still holds for the night being asked about."""
        if not share.get("share"):
            return False
        if night:
            return share.get("assignedNight") == night
        return self.now() - float(share.get("assignedAt") or 0.0) < HOLD_SECONDS

    def deal(self, share: dict[str, Any], night: str | None, badness: float | None) -> None:
        """Deal a share for tonight, unless its list already holds for this night."""
        if self._current(share, night):
            return
        project = self.projects[share["project"]]
        wants = project["requirements"]
        telescope = self.telescopes[share["agent"]]
        cells = share["cells"]
        filters = share["filters"]
        if not cells or not filters:
            return
        records = self.accepted_records(project["id"])
        depths = rules.depth(cells, records)
        mine = rules.depth(cells, records, agent=telescope["id"])
        hours_tonight = rules.number(telescope["profile"].get("hoursPerNight")) or rules.DEFAULT_HOURS
        budget = hours_tonight * 3600.0
        min_frames = wants["minFramesPerVisit"]

        # What other rigs on this project hold for the same night.
        others: list[dict[str, Any]] = []
        committed: dict[str, float] = {}
        for other in self.shares_on(project["id"]):
            if other["id"] == share["id"] or not self._current(other, night):
                continue
            others.extend(other["cells"][i] for i in other.get("share") or [])
            visit = other.get("visit") or {}
            for letter, frames in (visit.get("frames") or {}).items():
                exposure = next((f["exposure"] for f in other["filters"] if f["filter"] == letter), 0.0)
                committed[letter] = committed.get(letter, 0.0) + frames * exposure * len(other["share"])

        visit: dict[str, Any] = {}
        if project["kind"] == "mosaic":
            chosen = rules.choose_filter(filters, depths, committed, badness)
            order = rules.order_cells(cells, depths, mine, others, [chosen["filter"]],
                                      {chosen["filter"]: chosen["hours"]})
            visits, frames, seconds = rules.visit_plan(chosen["exposure"], chosen["hours"], budget,
                                                       len(order), min_frames)
            picked = order[:visits]
            visit = {"seconds": seconds, "frames": {chosen["filter"]: frames}, "filter": chosen["filter"]}
        else:
            picked = [0]
            weights = {f["filter"]: max(rules.remaining(depths[0], f["filter"], f["hours"]), 0.0)
                       for f in filters}
            total = sum(weights.values()) or float(len(filters))
            frames_by: dict[str, int] = {}
            for f in filters:
                share_of = (weights[f["filter"]] / total) if sum(weights.values()) else 1.0 / len(filters)
                frames_by[f["filter"]] = max(min_frames, int(budget * share_of // f["exposure"]))
            visit = {"seconds": sum(n * next(f["exposure"] for f in filters if f["filter"] == k)
                                    for k, n in frames_by.items()),
                     "frames": frames_by}
        if badness is not None:
            visit["moon"] = round(badness, 4)
        changed = picked != share.get("share") or visit != share.get("visit")
        share.update({"share": picked, "visit": visit, "assignedNight": night or "",
                      "assignedAt": self.now(), "dealtHours": hours_tonight})
        if changed:
            share["version"] += 1


# ---------------------------------------------------------------------------
# Server tools: what a server's own pages and admins do, outside the protocol
# ---------------------------------------------------------------------------

class Tools:
    """Things a real server does on its web pages or for its coordinators."""

    def __init__(self, state: State):
        self.state = state

    def sign_in(self, name: str) -> str:
        """A person token for `name`, as if they had signed in."""
        with self.state.lock:
            return self.state.mint("person", self.state.person(name)["id"])

    def approve_login(self, code: str, name: str) -> None:
        """Finish a device sign-in as `name`, as the sign-in page does."""
        with self.state.lock:
            login = self.state.logins.get(code)
            if login is None or self.state.now() - login["created"] > LOGIN_SECONDS:
                raise KeyError("that sign-in code is unknown or expired")
            login["person"] = self.state.person(name)["id"]

    def issue_pairing_code(self, name: str = "Observer") -> str:
        """A single-use pairing code that enrols a telescope for `name`."""
        with self.state.lock:
            code = secrets.token_urlsafe(12)
            self.state.pairing[_hash(code)] = {"person": self.state.person(name)["id"],
                                               "expires": self.state.now() + PAIRING_SECONDS,
                                               "used": False}
            return code

    def create_project(self, name: str, region: dict[str, Any], kind: str,
                       goals: dict[str, float], requirements: dict[str, Any],
                       notes: str = "") -> str:
        with self.state.lock:
            return self.state.add_project(name, region, kind, goals, requirements, notes)


# ---------------------------------------------------------------------------
# The routes
# ---------------------------------------------------------------------------

class Api:
    """One method per operation. Each takes a request and returns a JSON body."""

    def __init__(self, state: State):
        self.state = state
        self.routes: list[tuple[str, re.Pattern, Callable]] = []
        for method, pattern, handler in (
            ("GET", r"/api/v1/health", self.health),
            ("GET", r"/api/v1/auth", self.auth_status),
            ("POST", r"/api/v1/auth/login", self.auth_login),
            ("GET", r"/api/v1/auth/poll", self.auth_poll),
            ("GET", r"/api/v1/auth/me", self.auth_me),
            ("POST", r"/api/v1/auth/logout", self.auth_logout),
            ("POST", r"/api/v1/agents", self.enrol),
            ("GET", r"/api/v1/agents", self.list_telescopes),
            ("POST", r"/api/v1/pair", self.pair),
            ("POST", r"/api/v1/agent/hello", self.hello),
            ("GET", r"/api/v1/presence", self.presence),
            ("GET", r"/api/v1/agent/projects", self.open_projects),
            ("POST", r"/api/v1/agent/projects/(?P<project_id>[^/]+)/join", self.join),
            ("GET", r"/api/v1/agent/task", self.tonight),
            ("POST", r"/api/v1/agent/task/(?P<task_id>[^/]+)", self.set_task_state),
            ("POST", r"/api/v1/agent/report", self.report),
        ):
            self.routes.append((method, re.compile(pattern + r"/?"), handler))

    # -- discovery and sign-in ---------------------------------------------
    def health(self, request) -> dict[str, Any]:
        return {"ok": True, "protocol": PROTOCOL, "time": self.state.now(), "version": VERSION,
                "features": ["signin", "pairing"]}

    def auth_status(self, request) -> dict[str, Any]:
        return {"discord": True, "guild": "", "roleRequired": False,
                "publicUrl": self.state.base_url()}

    def auth_login(self, request) -> dict[str, Any]:
        code = secrets.token_urlsafe(9)
        self.state.logins[code] = {"created": self.state.now(), "person": None, "claimed": False}
        return {"code": code, "url": f"{self.state.base_url()}/signin?code={code}",
                "expiresIn": int(LOGIN_SECONDS)}

    def auth_poll(self, request) -> dict[str, Any]:
        code = request.query_one("code", required=True, max_length=64)
        login = self.state.logins.get(code)
        if login is None or self.state.now() - login["created"] > LOGIN_SECONDS:
            return {"state": "expired"}
        if login["person"] is None:
            return {"state": "pending"}
        if login["claimed"]:
            return {"state": "claimed"}
        login["claimed"] = True
        person = self.state.people[login["person"]]
        return {"state": "done", "token": self.state.mint("person", person["id"]),
                "user": {"id": person["id"], "name": person["name"], "avatar": "",
                         "admin": False, "canStart": False}}

    def auth_me(self, request) -> dict[str, Any]:
        person = self.state.who(request.authorization, "person")
        return {"id": person["id"], "name": person["name"], "admin": False, "canStart": False}

    def auth_logout(self, request) -> dict[str, Any]:
        self.state.who(request.authorization, "person")
        self.state.tokens.pop(_hash(request.authorization[7:].strip()), None)
        return {"signedOut": True}

    # -- telescopes and their tokens ---------------------------------------
    def enrol(self, request) -> dict[str, Any]:
        person = self.state.who(request.authorization, "person")
        body = check("EnrolRequest", request.json())
        return self.state.enrol(person, body["name"].strip())

    def list_telescopes(self, request) -> dict[str, Any]:
        person = self.state.who(request.authorization, "person")
        mine = [t for t in self.state.telescopes.values() if t["owner_id"] == person["id"]]
        return {"agents": [self.state.telescope_view(t) for t in sorted(mine, key=lambda t: t["name"])]}

    def pair(self, request) -> dict[str, Any]:
        now = self.state.now()
        recent = [t for t in self.state.bad_codes.get(request.address, []) if now - t < 60.0]
        self.state.bad_codes[request.address] = recent
        if len(recent) >= BAD_CODES_PER_MINUTE:
            raise Problem(429, "too many bad pairing codes; wait a minute and try again")
        body = check("PairRequest", request.json())
        code = self.state.pairing.get(_hash(body["code"]))
        if code is None or code["used"] or now > code["expires"]:
            recent.append(now)
            raise Problem(401, "that pairing code is unknown, used or expired; issue a new one")
        code["used"] = True
        return self.state.enrol(self.state.people[code["person"]], body["name"].strip())

    # -- the telescope -----------------------------------------------------
    def _telescope(self, request) -> dict[str, Any]:
        telescope = self.state.who(request.authorization, "telescope")
        telescope["seen"] = self.state.now()
        return telescope

    def hello(self, request) -> dict[str, Any]:
        telescope = self._telescope(request)
        body = check("HelloRequest", request.json())
        protocol = body.get("protocol", PROTOCOL)
        if protocol > PROTOCOL:
            raise Problem(409, f"this server speaks protocol {PROTOCOL}; the program speaks "
                               f"{protocol}. Update the server.")
        if "profile" in body:
            telescope["profile"] = body["profile"]
        if body.get("presence") is not None:
            telescope["presence"] = body["presence"]
        return {"agent": telescope["id"], "name": telescope["name"], "protocol": PROTOCOL,
                "serverTime": self.state.now()}

    def presence(self, request) -> dict[str, Any]:
        self._telescope(request)
        now = self.state.now()
        rows = []
        for telescope in self.state.telescopes.values():
            age = now - float(telescope["seen"] or 0.0)
            if not telescope["seen"] or age > LISTED_SECONDS:
                continue
            said = telescope.get("presence") or {}
            rows.append({"id": telescope["id"], "name": telescope["name"], "owner": telescope["owner"],
                         "ra": rules.number(said.get("ra")), "dec": rules.number(said.get("dec")),
                         "state": str(said.get("state") or ""), "target": str(said.get("target") or ""),
                         "project": str(said.get("project") or ""), "ageSeconds": int(round(age)),
                         "online": age <= ONLINE_SECONDS, "_owner_id": telescope["owner_id"]})
        rows.sort(key=lambda row: (not row["online"], row["ageSeconds"]))
        online = [row for row in rows if row["online"]]
        people = {row["_owner_id"] for row in online}
        for row in rows:
            del row["_owner_id"]
        return {"telescopes": rows, "online": len(online), "people": len(people),
                "onlineSeconds": ONLINE_SECONDS, "serverTime": now}

    def open_projects(self, request) -> dict[str, Any]:
        telescope = self._telescope(request)
        joined = {s["project"] for s in self.state.shares_of(telescope["id"], ("offered", "accepted", "complete"))}
        listed = []
        now = self.state.now()
        for project in sorted(self.state.projects.values(), key=lambda p: (p["created"], p["id"])):
            if project["status"] != "open":
                continue
            holders = [self.state.telescopes[s["agent"]] for s in self.state.shares_on(project["id"])]
            listed.append({
                "id": project["id"], "name": project["name"], "coordinator": project["coordinator"],
                "ownerId": project["ownerId"], "region": project["region"], "kind": project["kind"],
                "requirements": project["requirements"], "goals": project["goals"],
                "notes": project["notes"],
                "compatibility": rules.compatibility(telescope["profile"], project["requirements"],
                                                     project["goals"]),
                "joined": project["id"] in joined,
                "collected": self.state.collected(project["id"]),
                "participants": len(holders),
                "participantsOnline": sum(1 for t in holders if now - t["seen"] <= ONLINE_SECONDS),
                "participantNames": [t["name"] for t in holders],
            })
        return {"projects": listed, "protocol": PROTOCOL}

    def join(self, request, project_id: str) -> dict[str, Any]:
        telescope = self._telescope(request)
        body = check("JoinRequest", request.json())
        project = self.state.projects.get(project_id)
        if project is None or project["status"] != "open":
            raise Problem(404, "no such open project")
        wants = project["requirements"]
        held = [s for s in self.state.shares_of(telescope["id"]) if s["project"] == project_id]
        if held:
            return {"task": held[0], "requirements": wants, "alreadyJoined": True}
        profile = telescope["profile"]
        if rules.field(profile) is None:
            raise Problem(400, "describe the rig first: say hello with its focal length, "
                               "pixel size and sensor size")
        verdict = rules.compatibility(profile, wants, project["goals"])
        if not verdict["ok"]:
            raise Problem(409, verdict["summary"])
        usable, _ = rules.usable_filters(profile, wants, project["goals"])
        asked = rules.by_letter(body.get("exposures"))
        own = rules.by_letter(profile.get("exposures"))
        low, high = wants["minExposure"], wants["maxExposure"]
        middle = ((low + high) / 2.0 if low is not None and high is not None else low or high or 300.0)
        filters = []
        for letter in usable:
            exposure = (rules.number(asked.get(letter)) or rules.number(own.get(letter))
                        or rules.number(body.get("exposure")) or middle)
            if low is not None and exposure < low:
                raise Problem(409, f"this telescope shoots {letter} at {exposure:g}s; the project "
                                   f"wants {low:g}s or longer")
            if high is not None and exposure > high:
                raise Problem(409, f"this telescope shoots {letter} at {exposure:g}s; the project "
                                   f"wants {high:g}s or shorter")
            goal = project["goals"].get(letter, 1.0)
            hours = min(body.get("hours") or goal, goal) if goal else (body.get("hours") or 1.0)
            filters.append({"filter": letter, "exposure": float(exposure), "hours": float(hours)})
        share = {
            "id": _new_id(), "project": project_id, "projectName": project["name"],
            "agent": telescope["id"], "region": project["region"], "filters": filters,
            "state": "accepted", "version": 1, "issued": self.state.now(),
            "note": f"joined by {telescope['name']}",
            "seconds": sum(f["hours"] for f in filters) * 3600.0,
            "cells": rules.tile(project["region"], project["kind"], profile),
            "share": [], "kind": project["kind"], "visit": {}, "assignedNight": "",
            "assignedAt": 0.0, "dealtHours": None,
            "tiledRotation": rules.number(profile.get("rotation")),
        }
        self.state.shares[share["id"]] = share
        self.state.deal(share, None, None)
        return {"task": share, "requirements": wants}

    def tonight(self, request) -> dict[str, Any]:
        telescope = self._telescope(request)
        night = request.query_one("night", max_length=16) or None
        moon = request.query_number("moon")
        moon_up = request.query_number("moonUp")
        badness = rules.moon_badness(moon, moon_up)
        shares = self.state.shares_of(telescope["id"])
        for share in shares:
            # A camera turned since its cells were cut gets them cut again.
            fixed = rules.number(telescope["profile"].get("rotation"))
            if fixed != share.get("tiledRotation") and rules.field(telescope["profile"]):
                project = self.state.projects[share["project"]]
                share.update({"cells": rules.tile(project["region"], project["kind"], telescope["profile"]),
                              "share": [], "tiledRotation": fixed})
                share["version"] += 1
            self.state.deal(share, night, badness)
        if not shares:
            return {"task": None, "tasks": [], "version": 0, "protocol": PROTOCOL}
        by_project = {s["project"]: self.state.projects[s["project"]]["requirements"] for s in shares}
        return {"task": shares[0], "tasks": shares, "requirementsByProject": by_project,
                "version": shares[0]["version"], "protocol": PROTOCOL,
                "requirements": by_project[shares[0]["project"]]}

    def set_task_state(self, request, task_id: str) -> dict[str, Any]:
        telescope = self._telescope(request)
        body = check("TaskStateRequest", request.json())
        share = self.state.shares.get(task_id)
        if share is None or share["agent"] != telescope["id"]:
            raise Problem(404, "no such share for this telescope")
        if share["state"] != body["state"]:
            share["state"] = body["state"]
            share["version"] += 1
        return {"task": share}

    def report(self, request) -> dict[str, Any]:
        telescope = self._telescope(request)
        body = check("ReportRequest", request.json())
        recorded = []
        for entry in body.get("contributions") or []:
            share = self.state.shares.get(entry.get("task") or "")
            if share is not None and share["agent"] != telescope["id"]:
                raise Problem(403, "that share belongs to another telescope")
            project_id = entry.get("project") or (share or {}).get("project") or ""
            project = self.state.projects.get(project_id)
            letter = rules.filter_letter(entry.get("filterName"))
            verdict = rules.judge(entry, project["requirements"] if project else None)
            key = (telescope["id"], entry.get("task") or "", entry.get("night") or "",
                   letter, str(entry.get("panel") or ""))
            seconds = rules.number(entry.get("seconds")) or 0.0
            existing = self.state.records.get(self.state.record_keys.get(key, ""))
            if existing is not None:
                # The same night, filter and panel again: the larger figure stands.
                if seconds > existing["seconds"]:
                    existing.update({"seconds": seconds, "payload": entry, "verdict": verdict,
                                     "accepted": verdict["accepted"]})
                recorded.append({"id": existing["id"], "accepted": existing["accepted"],
                                 "duplicate": True, "verdict": existing["verdict"]})
                continue
            record = {"id": _new_id(), "agent": telescope["id"], "project": project_id,
                      "task": key[1], "night": key[2], "filter": letter, "panel": key[4],
                      "seconds": seconds, "accepted": verdict["accepted"], "payload": entry,
                      "verdict": verdict, "received": self.state.now()}
            self.state.records[record["id"]] = record
            self.state.record_keys[key] = record["id"]
            recorded.append({"id": record["id"], "accepted": record["accepted"],
                             "duplicate": False, "verdict": verdict})
        return {"recorded": recorded}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

SIGNIN_PAGE = """<!doctype html><meta charset="utf-8"><title>Sign in</title>
<body style="font-family:sans-serif;max-width:30em;margin:3em auto">
<h1>Sign in to the AstroCollab reference server</h1>
<p>{message}</p>
<form method="post" action="/signin">
<input type="hidden" name="code" value="{code}">
<label>Your name <input name="name" value="Observer" required></label>
<button>Approve</button>
</form></body>"""


class Request:
    """What a route needs from an HTTP request."""

    def __init__(self, handler: BaseHTTPRequestHandler, query: dict[str, list[str]]):
        self.authorization = handler.headers.get("Authorization", "")
        self.address = handler.client_address[0]
        self.query = query
        self._handler = handler
        self._body: bytes | None = None

    def raw(self) -> bytes:
        if self._body is None:
            length = int(self._handler.headers.get("Content-Length") or 0)
            self._body = self._handler.rfile.read(length) if length else b""
        return self._body

    def json(self) -> Any:
        raw = self.raw()
        if not raw:
            raise Problem(422, [{"loc": ["body"], "msg": "a JSON body is required", "type": "missing"}])
        try:
            return json.loads(raw)
        except ValueError as error:
            raise Problem(422, [{"loc": ["body"], "msg": f"not JSON: {error}", "type": "json_invalid"}])

    def query_one(self, name: str, required: bool = False, max_length: int = 4096) -> str:
        values = self.query.get(name)
        if not values:
            if required:
                raise Problem(422, [{"loc": ["query", name], "msg": "required", "type": "missing"}])
            return ""
        if len(values[0]) > max_length:
            raise Problem(422, [{"loc": ["query", name], "msg": f"at most {max_length} characters",
                                 "type": "string_too_long"}])
        return values[0]

    def query_number(self, name: str) -> float | None:
        text = self.query_one(name)
        if not text:
            return None
        value = rules.number(text)
        if value is None or not 0.0 <= value <= 1.0:
            raise Problem(422, [{"loc": ["query", name], "msg": "a number from 0 to 1",
                                 "type": "value_error"}])
        return value


def _handler_class(api: Api, tools: Tools):
    class Handler(BaseHTTPRequestHandler):
        server_version = "AstroCollabReference/0.2"

        def log_message(self, *args):  # quiet unless asked
            if getattr(self.server, "verbose", False):
                super().log_message(*args)

        def _send(self, status: int, body: Any, content_type: str = "application/json") -> None:
            data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _signin_page(self, method: str, query: dict[str, list[str]]) -> None:
            if method == "GET":
                code = (query.get("code") or [""])[0]
                self._send(200, SIGNIN_PAGE.format(code=html.escape(code), message="Approve this "
                           "sign-in to let your program enrol telescopes for you."),
                           "text/html; charset=utf-8")
                return
            length = int(self.headers.get("Content-Length") or 0)
            form = parse_qs(self.rfile.read(length).decode() if length else "")
            code, name = (form.get("code") or [""])[0], (form.get("name") or ["Observer"])[0]
            try:
                tools.approve_login(code, name.strip() or "Observer")
            except KeyError as error:
                self._send(400, f"<p>{html.escape(str(error))}. Start again from your program.</p>",
                           "text/html; charset=utf-8")
                return
            self._send(200, f"<p>Signed in as {html.escape(name)}. Go back to your program.</p>",
                       "text/html; charset=utf-8")

        def _dispatch(self, method: str) -> None:
            parts = urlsplit(self.path)
            query = parse_qs(parts.query)
            if parts.path.rstrip("/") == "/signin":
                self._signin_page(method, query)
                return
            matched_path = False
            for route_method, pattern, handler in api.routes:
                found = pattern.fullmatch(parts.path)
                if not found:
                    continue
                matched_path = True
                if route_method != method:
                    continue
                try:
                    with api.state.lock:
                        body = handler(Request(self, query), **found.groupdict())
                    self._send(200, body)
                except Problem as problem:
                    self._send(problem.status, {"detail": problem.detail})
                return
            if matched_path:
                self._send(405, {"detail": "Method Not Allowed"})
            else:
                self._send(404, {"detail": "Not Found"})

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

    return Handler


def serve(host: str = "127.0.0.1", port: int = 0, sample: bool = True) -> ThreadingHTTPServer:
    """A server bound to (host, port), not yet serving. Call serve_forever().

    `httpd.tools` does what a real server's web pages and admins do;
    `httpd.sample_project_ids` maps each sample project's name to its ID;
    `httpd.state.now` can be replaced to move the clock in tests.
    """
    holder: dict[str, ThreadingHTTPServer] = {}

    def base_url() -> str:
        bound_host, bound_port = holder["httpd"].server_address[:2]
        return f"http://{bound_host}:{bound_port}"

    state = State(base_url)
    api = Api(state)
    tools = Tools(state)
    httpd = ThreadingHTTPServer((host, port), _handler_class(api, tools))
    httpd.daemon_threads = True
    holder["httpd"] = httpd
    httpd.state = state
    httpd.tools = tools
    httpd.base_url = base_url()
    httpd.verbose = False
    httpd.sample_project_ids = {}
    if sample:
        for project in json.loads(SAMPLES.read_text(encoding="utf-8")):
            httpd.sample_project_ids[project["name"]] = tools.create_project(**project)
    return httpd


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the AstroCollab 0.2 reference server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--no-sample", action="store_true", help="start with no projects")
    parser.add_argument("--verbose", action="store_true", help="log every request")
    args = parser.parse_args()
    httpd = serve(args.host, args.port, sample=not args.no_sample)
    httpd.verbose = args.verbose
    print(f"AstroCollab reference server on {httpd.base_url}")
    print(f"  person token   {httpd.tools.sign_in('Observer')}")
    print(f"  pairing code   {httpd.tools.issue_pairing_code('Observer')}")
    for name, project_id in httpd.sample_project_ids.items():
        print(f"  project        {project_id}  {name}")
    print("Everything is kept in memory and lost when the server stops.", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
