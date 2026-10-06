"""Black-box conformance checks for an AstroCollab 0.2 server.

    python -m conformance.server_suite --server http://127.0.0.1:8800 \
        (--person-token TOKEN | --pairing-code CODE --pairing-code CODE) \
        --project-id MOSAIC_ID [--single-project-id SINGLE_ID]

The suite uses only the routes in the contract. The projects must exist and be
open before it runs; it reads their requirements and builds telescopes to fit
them. It prints one line per check and exits with status 1 if any check fails.
Every response is also checked against the OpenAPI document and the standalone
JSON Schemas.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import math
from pathlib import Path
import re
import secrets
import sys
from typing import Any, Callable

from .contract import Contract, DEFAULT_CONTRACT, DEFAULT_SCHEMAS
from .transport import Client, Response

API = "/api/v1"

# Filter letters and the names a rig might give them (spec/protocol.md section 2).
ALIASES = {
    "L": {"l", "lum", "luminance", "clear", "uvircut", "uv/ircut", "none"},
    "R": {"r", "red"}, "G": {"g", "green"}, "B": {"b", "blue"},
    "H": {"h", "ha", "halpha", "hydrogenalpha"},
    "O": {"o", "oiii", "o3", "oxygen", "oxygeniii"},
    "S": {"s", "sii", "s2", "sulphur", "sulfur", "sulphurii", "sulfurii"},
}
#: The names this suite's telescopes give their filters, so that folding is tested too.
RIG_NAMES = {"L": "Lum", "R": "Red", "G": "Green", "B": "Blue", "H": "Ha", "O": "OIII", "S": "SII"}
RED_NARROWBAND = {"H", "S"}
DARK_NIGHT = {"L", "R", "G", "B", "O"}
PIXEL_UM = 3.76
SENSOR = (6248, 4176)


BANDPASS = re.compile(r"[\s(\[-]*\d+(?:\.\d+)?\s*nm[)\]]*\s*$", re.IGNORECASE)


def fold(name: Any) -> str:
    """A filter name as its letter, where it has one: "Ha 3nm" is H, "Dual" stays "Dual"."""
    text = " ".join(str(name or "").split())
    key = re.sub(r"[\s_\-]+", "", BANDPASS.sub("", text)).lower()
    for letter, names in ALIASES.items():
        if key in names:
            return letter
    return text


class Skip(Exception):
    pass


class Failure(Exception):
    pass


def need(condition: Any, message: str) -> None:
    if not condition:
        raise Failure(message)


def expect(response: Response, status: int, what: str) -> Any:
    if response.status != status:
        detail = response.detail
        raise Failure(f"{what}: {response.describe()}, expected {status}"
                      + (f" ({detail})" if isinstance(detail, str) else ""))
    return response.json


@dataclass
class Rig:
    name: str
    token: str
    id: str = ""
    profile: dict[str, Any] = field(default_factory=dict)


@dataclass
class Result:
    status: str
    name: str
    row: str
    detail: str = ""

    def line(self) -> str:
        return f"{self.status:<4}  {self.name} [{self.row}]" + (f": {self.detail}" if self.detail else "")


class Suite:
    def __init__(self, client: Client, person_token: str | None, pairing_codes: list[str],
                 mosaic_id: str, single_id: str | None, out: Callable[[str], None] = print):
        self.client = client
        self.person = person_token
        self.codes = list(pairing_codes)
        self.mosaic_id = mosaic_id
        self.single_id = single_id
        self.out = out
        self.results: list[Result] = []
        self.nonce = secrets.token_hex(3)
        self.features: list[str] = []
        self.rigs: list[Rig] = []
        self.projects: dict[str, dict[str, Any]] = {}
        self.task: dict[str, Any] | None = None          # rig 1's mosaic share
        self.task2: dict[str, Any] | None = None         # rig 2's mosaic share
        self.night1: dict[str, Any] | None = None        # rig 1's mosaic share, night 1
        self.night1_rig2: dict[str, Any] | None = None
        self.night2: dict[str, Any] | None = None
        self.used_code: str | None = None             # a pairing code already spent
        self.focal = 530.0
        self.hours = 2.0

    # Running ------------------------------------------------------------------

    def night(self, label: str) -> str:
        return f"c{self.nonce}-{label}"[:16]

    def check(self, name: str, row: str, fn: Callable[[], str | None]) -> bool:
        start = len(self.client.issues)
        try:
            detail = fn() or ""
            status = "PASS"
        except Skip as skip:
            status, detail = "SKIP", str(skip)
        except Failure as failure:
            status, detail = "FAIL", str(failure)
        except Exception as error:  # a crash is a failure of the check, not of the suite
            status, detail = "FAIL", f"{type(error).__name__}: {error}"
        issues = self.client.issues[start:]
        if issues and status != "FAIL":
            status, detail = "FAIL", "response breaks the contract: " + "; ".join(issues[:3])
        result = Result(status, name, row, detail)
        self.results.append(result)
        self.out(result.line())
        return status == "PASS"

    def run(self) -> int:
        steps: list[tuple[str, str, Callable[[], str | None]]] = [
            ("health_names_the_protocol", "Discovery", self.health),
            ("sign_in_status_and_start", "Discovery", self.sign_in),
            ("telescopes_get_their_own_tokens", "Tokens", self.telescopes),
            ("pairing_code_works_once", "Tokens", self.pairing_once),
            ("bad_token_is_refused", "Tokens", self.bad_token),
            ("person_and_telescope_tokens_kept_apart", "Tokens", self.tokens_apart),
            ("projects_are_listed_with_compatibility", "Browsing", self.browse),
            ("hello_stores_the_profile", "Hello", self.hello_stores_profile),
            ("newer_protocol_is_refused", "Hello", self.newer_protocol),
            ("join_tiles_the_region_with_the_rig_s_own_field", "Joining", self.join),
            ("joining_twice_returns_the_same_share", "Joining", self.join_twice),
            ("an_undescribed_rig_is_refused_with_400", "Joining", self.join_undescribed),
            ("a_rig_that_cannot_help_is_refused", "Joining", self.join_refused),
            ("a_single_target_is_one_frame", "Joining", self.join_single),
            ("tonight_returns_every_share", "Tonight", self.tonight_first),
            ("bright_moon_deals_red_narrowband", "Moon", self.bright_moon),
            ("a_mosaic_night_is_one_filter", "Dealing", self.one_filter),
            ("visits_meet_min_frames_per_visit", "Dealing", self.min_frames),
            ("two_rigs_take_different_panels", "Dealing", self.two_rigs),
            ("the_list_holds_for_the_night", "Tonight", self.list_holds),
            ("report_answers_in_order", "Reports", self.report_order),
            ("next_night_moves_on", "Tonight", self.next_night),
            ("dark_moon_deals_broadband_or_oiii", "Moon", self.dark_moon),
            ("a_broken_rule_is_rejected_with_a_reason", "Judging", self.judged_reject),
            ("a_missing_measurement_is_unverified", "Judging", self.unverified),
            ("a_repeat_keeps_the_larger_figure", "Duplicates", self.duplicates),
            ("another_rig_s_share_is_not_yours_to_report", "Reports", self.report_foreign),
            ("presence_shows_the_telescope_online", "Presence", self.presence),
            ("unknown_things_are_404_with_detail", "Errors", self.not_found),
            ("an_invalid_body_is_422_with_fields", "Errors", self.invalid_body),
            ("accepting_a_share_keeps_it_accepted", "Tonight", self.accept_share),
            ("a_fixed_camera_tiles_at_its_own_angle", "Joining", self.fixed_rotation),
        ]
        for name, row, fn in steps:
            self.check(name, row, fn)
        counts = {s: sum(r.status == s for r in self.results) for s in ("PASS", "FAIL", "SKIP")}
        self.out(f"\n{counts['PASS']} passed, {counts['FAIL']} failed, {counts['SKIP']} skipped")
        return 1 if counts["FAIL"] else 0

    # Helpers ------------------------------------------------------------------

    def rig(self, index: int) -> Rig:
        if len(self.rigs) <= index:
            raise Skip(f"needs telescope {index + 1}, which could not be made")
        return self.rigs[index]

    def project(self, project_id: str | None) -> dict[str, Any]:
        if project_id is None or project_id not in self.projects:
            raise Skip("the project is not in the open listing")
        return self.projects[project_id]

    def wanted(self, project: dict[str, Any]) -> list[str]:
        filters = (project.get("requirements") or {}).get("filters") or {}
        return [fold(name) for name in (filters or project.get("goals") or {})]

    def field_of(self, profile: dict[str, Any]) -> tuple[float, float]:
        scale = 206.265 * profile["pixelSize"] * profile.get("binning", 1) / profile["focalLength"]
        return (scale * profile["sensorWidth"] / 3600.0, scale * profile["sensorHeight"] / 3600.0)

    def choose_optics(self) -> None:
        """A focal length every project accepts, giving the mosaic several cells."""
        mosaic = self.projects.get(self.mosaic_id) or {}
        region = mosaic.get("region") or {}
        best: tuple[int, float] | None = None
        for focal in (530.0, 400.0, 600.0, 800.0, 300.0, 1000.0, 250.0, 1200.0, 1500.0, 2000.0, 135.0):
            scale = 206.265 * PIXEL_UM / focal
            ok = True
            for project in self.projects.values():
                if project["id"] not in (self.mosaic_id, self.single_id):
                    continue
                wants = project.get("requirements") or {}
                if wants.get("minFocalLength") is not None and focal < wants["minFocalLength"]:
                    ok = False
                if wants.get("maxFocalLength") is not None and focal > wants["maxFocalLength"]:
                    ok = False
                if wants.get("minScale") is not None and scale < wants["minScale"]:
                    ok = False
                if wants.get("maxScale") is not None and scale > wants["maxScale"]:
                    ok = False
            if not ok:
                continue
            width = scale * SENSOR[0] / 3600.0
            height = scale * SENSOR[1] / 3600.0

            def across(span: float, size: float) -> int:
                return 1 if span <= size else math.ceil((span - size * 0.1) / (size * 0.9))
            cells = across(float(region.get("width") or 0), width) * across(float(region.get("height") or 0), height)
            if cells >= 6:
                self.focal = focal
                return
            if best is None or cells > best[0]:
                best = (cells, focal)
        if best is not None:
            self.focal = best[1]

    def good_profile(self, name: str, rotation: float | None = None) -> dict[str, Any]:
        """A rig every project in the suite can use."""
        letters: list[str] = []
        limits: dict[str, list[float]] = {}
        lows: dict[str, list[float]] = {}
        highs: dict[str, list[float]] = {}
        hfr: list[float] = []
        guide: list[float] = []
        for project in self.projects.values():
            if project["id"] not in (self.mosaic_id, self.single_id):
                continue
            wants = project.get("requirements") or {}
            for raw, limit in ((wants.get("filters") or {}) or
                               {k: None for k in project.get("goals") or {}}).items():
                letter = fold(raw)
                if letter not in letters:
                    letters.append(letter)
                if limit is not None:
                    limits.setdefault(letter, []).append(float(limit))
                if wants.get("minExposure") is not None:
                    lows.setdefault(letter, []).append(float(wants["minExposure"]))
                if wants.get("maxExposure") is not None:
                    highs.setdefault(letter, []).append(float(wants["maxExposure"]))
            if wants.get("maxHfr") is not None:
                hfr.append(float(wants["maxHfr"]))
            if wants.get("maxGuideRms") is not None:
                guide.append(float(wants["maxGuideRms"]))
        filters: dict[str, float | None] = {}
        exposures: dict[str, float] = {}
        for letter in letters:
            rig_name = RIG_NAMES.get(letter, letter)
            if letter in limits:
                filters[rig_name] = min(limits[letter])
            else:
                filters[rig_name] = 7.0 if letter in ("H", "O", "S") else None
            seconds = 300.0 if letter in ("H", "O", "S") else 120.0
            if letter in lows:
                seconds = max(seconds, max(lows[letter]))
            if letter in highs:
                seconds = min(seconds, min(highs[letter]))
            exposures[rig_name] = seconds
        scale = 206.265 * PIXEL_UM / self.focal
        return {
            "name": name, "focalLength": self.focal, "pixelSize": PIXEL_UM,
            "sensorWidth": SENSOR[0], "sensorHeight": SENSOR[1], "binning": 1,
            "filters": filters, "colour": False, "rotation": rotation,
            "typicalHfr": round(min(hfr) * 0.6, 2) if hfr else 2.0,
            "typicalGuideRms": round(min(guide) * 0.5, 2) if guide else 0.6,
            "exposures": exposures, "hoursPerNight": self.hours,
            "windowFrom": "21:00", "windowTo": "04:00",
            "scale": scale, "field": [scale * SENSOR[0] / 3600.0, scale * SENSOR[1] / 3600.0],
        }

    def hello(self, rig: Rig, profile: dict[str, Any], presence: dict[str, Any] | None = None,
              protocol: int = 1) -> Response:
        body: dict[str, Any] = {"protocol": protocol, "profile": profile}
        if presence is not None:
            body["presence"] = presence
        response = self.client.post(f"{API}/agent/hello", rig.token, body)
        if response.status == 200 and protocol == 1:
            rig.profile = profile
        return response

    def listing(self, rig: Rig) -> dict[str, dict[str, Any]]:
        data = expect(self.client.get(f"{API}/agent/projects", rig.token), 200, "browse")
        return {p["id"]: p for p in data.get("projects") or []}

    def tonight(self, rig: Rig, night: str, moon: float, up: float) -> dict[str, Any]:
        return expect(self.client.get(f"{API}/agent/task", rig.token, night=night, moon=moon, moonUp=up),
                      200, "tonight")

    def share_of(self, reply: dict[str, Any], project_id: str) -> dict[str, Any]:
        for task in reply.get("tasks") or []:
            if task.get("project") == project_id and task.get("state") in ("accepted", "offered"):
                return task
        raise Failure(f"tonight has no share of project {project_id}")

    def exposure(self, rig: Rig, letter: str) -> float:
        for name, seconds in (rig.profile.get("exposures") or {}).items():
            if fold(name) == letter:
                return float(seconds)
        raise Skip(f"the rig has no {letter} sub length")

    def bandpass(self, rig: Rig, letter: str) -> float | None:
        for name, width in (rig.profile.get("filters") or {}).items():
            if fold(name) == letter:
                return width
        return None

    def record(self, rig: Rig, task: dict[str, Any], index: int, night: str, letter: str,
               frames: int, hfr: float | None = None) -> dict[str, Any]:
        """One report record for a cell, shaped as a rig would send it."""
        project = self.project(task["project"])
        wants = project.get("requirements") or {}
        cell = task["cells"][index]
        exposure = self.exposure(rig, letter)
        scale = 206.265 * PIXEL_UM / float(rig.profile["focalLength"])
        good_hfr = float(rig.profile.get("typicalHfr") or 2.0)
        moon_limit = wants.get("maxMoonIllumination")
        separation = wants.get("minMoonSeparation")
        return {
            "project": task["project"], "task": task["id"], "night": night,
            "filterName": RIG_NAMES.get(letter, letter), "panel": str(index),
            "frames": frames, "seconds": frames * exposure, "exposure": exposure,
            "footprint": {k: cell[k] for k in ("ra", "dec", "width", "height", "rotation") if k in cell},
            "scale": scale, "focalLength": rig.profile["focalLength"],
            "hfr": good_hfr if hfr is None else hfr,
            "guideRms": float(rig.profile.get("typicalGuideRms") or 0.6),
            "moonIllumination": min(0.1, moon_limit) if moon_limit is not None else 0.1,
            "moonSeparation": max(90.0, float(separation or 0.0)),
            "calibrated": True, "bandpass": self.bandpass(rig, letter), "colour": False,
        }

    def report(self, rig: Rig, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        data = expect(self.client.post(f"{API}/agent/report", rig.token, {"contributions": records}),
                      200, "report")
        recorded = data.get("recorded") or []
        need(len(recorded) == len(records), f"{len(records)} records sent, {len(recorded)} answered")
        return recorded

    def collected(self, rig: Rig, letter: str) -> float:
        project = self.listing(rig).get(self.mosaic_id) or {}
        return sum(float(hours) for name, hours in (project.get("collected") or {}).items()
                   if fold(name) == letter)

    def mosaic_letter(self) -> str:
        visit = (self.night1 or {}).get("visit") or {}
        letter = fold(visit.get("filter") or "")
        if not letter:
            frames = visit.get("frames") or {}
            letter = fold(next(iter(frames), ""))
        if not letter:
            raise Skip("night 1 named no filter")
        return letter

    def min_frames_wanted(self, project_id: str) -> int:
        return int((self.project(project_id).get("requirements") or {}).get("minFramesPerVisit") or 10)

    # Discovery ----------------------------------------------------------------

    def health(self) -> str:
        data = expect(self.client.get(f"{API}/health"), 200, "health")
        need(data.get("ok") is True, "health does not say ok")
        need(data.get("protocol") == 1, f"protocol is {data.get('protocol')!r}; this draft is protocol 1")
        self.features = list(data.get("features") or [])
        return f"protocol 1, features {self.features or 'not listed'}"

    def sign_in(self) -> str:
        status = expect(self.client.get(f"{API}/auth"), 200, "sign-in status")
        # Without `features`, sign-in is offered when the status says so (protocol section 3).
        offered = "signin" in self.features if self.features else bool(status.get("discord"))
        started = self.client.post(f"{API}/auth/login")
        if started.status == 503:
            need(not offered, "sign-in is offered here, but starting it gives 503")
            return "sign-in is off here (503), which the contract allows"
        data = expect(started, 200, "start sign-in")
        poll = expect(self.client.get(f"{API}/auth/poll", code=data["code"]), 200, "poll")
        need(poll.get("state") == "pending", f"a fresh code polls as {poll.get('state')!r}, not pending")
        need("token" not in poll, "a pending poll handed over a token")
        return "a fresh code polls as pending"

    # Tokens -------------------------------------------------------------------

    def telescopes(self) -> str:
        made: list[str] = []
        names = [f"Conformance {self.nonce} A", f"Conformance {self.nonce} B"]
        if self.person:
            for name in names:
                data = expect(self.client.post(f"{API}/agents", self.person, {"name": name}), 200, "enrol")
                need(data.get("token"), "enrolling returned no token")
                self.rigs.append(Rig(name, data["token"], data["agent"]["id"]))
            listed = expect(self.client.get(f"{API}/agents", self.person), 200, "list telescopes")
            ids = {agent["id"] for agent in listed.get("agents") or []}
            need({r.id for r in self.rigs} <= ids, "the person's telescope list misses the new telescopes")
            need(not any("token" in agent for agent in listed.get("agents") or []),
                 "the telescope list shows tokens")
            made.append("enrolled two and listed them")
        else:
            for name in names:
                if not self.codes:
                    break
                code = self.codes.pop(0)
                data = expect(self.client.post(f"{API}/pair", None, {"code": code, "name": name}), 200, "pair")
                self.rigs.append(Rig(name, data["token"], data["agent"]["id"]))
                self.used_code = code
            made.append(f"paired {len(self.rigs)}")
        need(len(self.rigs) == 2, "the suite needs two telescopes")
        for rig in self.rigs:
            reply = expect(self.hello(rig, {"name": rig.name}), 200, "first hello")
            need(reply.get("agent") == rig.id, "hello names a different telescope")
        return "; ".join(made)

    def pairing_once(self) -> str:
        if "pairing" not in self.features and self.used_code is None:
            # Pairing counts as offered only when `features` lists it (protocol section 3).
            raise Skip("the server does not list pairing")
        if self.used_code is not None:
            code = self.used_code
        elif self.codes:
            code = self.codes.pop(0)
            expect(self.client.post(f"{API}/pair", None, {"code": code, "name": f"Pair {self.nonce}"}),
                   200, "pair")
        else:
            raise Skip("no --pairing-code given")
        again = self.client.post(f"{API}/pair", None, {"code": code, "name": f"Pair {self.nonce} again"})
        need(again.status == 401, f"a used code gave {again.status}, not 401")
        return "a used code is refused with 401"

    def bad_token(self) -> str:
        for path in (f"{API}/agent/task", f"{API}/agent/projects"):
            response = self.client.get(path, f"not-a-token-{self.nonce}")
            need(response.status == 401, f"{path} with a made-up token gave {response.status}")
            need(isinstance(response.detail, str), "the 401 has no detail sentence")
        missing = self.client.get(f"{API}/agent/task")
        need(missing.status == 401, f"no token at all gave {missing.status}")
        if self.person:
            response = self.client.get(f"{API}/agents", f"not-a-token-{self.nonce}")
            need(response.status == 401, f"a made-up person token gave {response.status}")
        return "401 with a detail"

    def tokens_apart(self) -> str:
        rig = self.rig(0)
        if not self.person:
            raise Skip("needs --person-token")
        as_rig = self.client.get(f"{API}/agent/task", self.person)
        need(as_rig.status == 401, f"a person token fetched work: {as_rig.status}")
        as_person = self.client.get(f"{API}/agents", rig.token)
        need(as_person.status == 401, f"a telescope token listed telescopes: {as_person.status}")
        enrol = self.client.post(f"{API}/agents", rig.token, {"name": "should not exist"})
        need(enrol.status == 401, f"a telescope token enrolled a telescope: {enrol.status}")
        return "each token is refused on the other's routes"

    # Browsing and hello ---------------------------------------------------------

    def browse(self) -> str:
        rig = self.rig(0)
        self.projects = self.listing(rig)
        need(self.mosaic_id in self.projects, f"project {self.mosaic_id} is not in the open listing")
        mosaic = self.projects[self.mosaic_id]
        need(mosaic.get("kind") == "mosaic", f"project {self.mosaic_id} is {mosaic.get('kind')}, not a mosaic")
        if self.single_id is not None:
            need(self.single_id in self.projects, f"project {self.single_id} is not in the open listing")
            need(self.projects[self.single_id].get("kind") == "single", "the single project is not single")
        need(self.wanted(mosaic), "the mosaic names no filters, in requirements or goals")
        self.choose_optics()
        return f"{len(self.projects)} open; using {self.focal:g} mm"

    def hello_stores_profile(self) -> str:
        rig = self.rig(0)
        bare = self.listing(rig)[self.mosaic_id]["compatibility"]
        need(not (bare.get("ok") and bare.get("certain")),
             "a rig that has said nothing about itself is certain to help")
        for index, one in enumerate(self.rigs):
            presence = ({"ra": float(self.project(self.mosaic_id)["region"]["ra"]) / 15.0,
                         "dec": float(self.project(self.mosaic_id)["region"]["dec"]),
                         "state": "imaging", "target": "conformance",
                         "project": self.mosaic_id} if index == 0 else None)
            expect(self.hello(one, self.good_profile(one.name), presence), 200, "hello")
        after = self.listing(rig)[self.mosaic_id]["compatibility"]
        need(after.get("ok") is True, f"a rig built for the project cannot help: {after.get('summary')}")
        return f"before: {bare.get('summary')!r}; after: {after.get('summary')!r}"

    def newer_protocol(self) -> str:
        rig = self.rig(0)
        response = self.hello(rig, rig.profile, protocol=999)
        need(response.status == 409, f"protocol 999 gave {response.status}, not 409")
        need(isinstance(response.detail, str), "the 409 has no detail sentence")
        return "409"

    # Joining ------------------------------------------------------------------

    def join_body(self, rig: Rig) -> dict[str, Any]:
        return {"hours": 0, "exposure": 0, "exposures": dict(rig.profile.get("exposures") or {})}

    def check_cells(self, rig: Rig, task: dict[str, Any], fixed: float | None = None) -> None:
        width, height = self.field_of(rig.profile)
        cells = task.get("cells") or []
        need(cells, "the share has no cells")
        for cell in cells:
            w, h = float(cell["width"]), float(cell["height"])
            if fixed is None:
                need(w <= width * 1.02 and h <= height * 1.02,
                     f"a cell is {w:.3f}°×{h:.3f}°, larger than the {width:.3f}°×{height:.3f}° field")
            else:
                pair = sorted((w, h))
                field = sorted((width, height))
                need(abs(pair[0] - field[0]) <= field[0] * 0.02 and abs(pair[1] - field[1]) <= field[1] * 0.02,
                     f"a cell is {w:.3f}°×{h:.3f}°, not the {width:.3f}°×{height:.3f}° field turned")
                turn = (float(cell.get("rotation") or 0.0) - fixed) % 180.0
                need(min(turn, 180.0 - turn) <= 1.0,
                     f"a cell is at {cell.get('rotation')}°, not the camera's fixed {fixed:g}°")

    def join(self) -> str:
        rig = self.rig(0)
        data = expect(self.client.post(f"{API}/agent/projects/{self.mosaic_id}/join", rig.token,
                                       self.join_body(rig)), 200, "join")
        task = data.get("task")
        need(task, "joining returned no share")
        need(task.get("project") == self.mosaic_id, "the share is for another project")
        need(task.get("state") == "accepted", f"a joined share is {task.get('state')!r}, not accepted")
        self.check_cells(rig, task)
        for entry in task.get("filters") or []:
            letter = fold(entry.get("filter"))
            mine = self.exposure(rig, letter)
            need(abs(float(entry.get("exposure") or 0) - mine) < 0.5,
                 f"{letter} is dealt at {entry.get('exposure')} s, not the rig's {mine:g} s")
        self.task = task
        return f"{len(task['cells'])} cells, each within the rig's field"

    def join_twice(self) -> str:
        rig = self.rig(0)
        if self.task is None:
            raise Skip("the first join failed")
        data = expect(self.client.post(f"{API}/agent/projects/{self.mosaic_id}/join", rig.token,
                                       self.join_body(rig)), 200, "join again")
        need((data.get("task") or {}).get("id") == self.task["id"], "joining again made a second share")
        return "same share"

    def join_undescribed(self) -> str:
        """A rig that has not said what it can see cannot be tiled: 400 (protocol section 5)."""
        rig = self.rig(1)
        expect(self.hello(rig, {"name": rig.name}), 200, "hello")
        refused = self.client.post(f"{API}/agent/projects/{self.mosaic_id}/join", rig.token, {})
        need(refused.status == 400, f"joining with no optics gave {refused.status}, not 400")
        need(isinstance(refused.detail, str) and refused.detail, "the 400 gives no reason")
        return f"400: {refused.detail}"

    def join_refused(self) -> str:
        rig = self.rig(1)
        wants = self.project(self.mosaic_id).get("requirements") or {}
        bad = dict(self.good_profile(rig.name))
        wanted = self.wanted(self.project(self.mosaic_id))
        if len(wanted) >= 1:
            # Every filter but one: a rig must carry every filter the project wants.
            dropped = wanted[-1]
            bad["filters"] = {k: v for k, v in bad["filters"].items() if fold(k) != dropped}
            bad["exposures"] = {k: v for k, v in bad["exposures"].items() if fold(k) != dropped}
        elif wants.get("maxFocalLength") is not None:
            bad["focalLength"] = float(wants["maxFocalLength"]) * 2
        elif wants.get("minFocalLength") is not None:
            bad["focalLength"] = float(wants["minFocalLength"]) / 2
        else:
            raise Skip("the mosaic has no filter or focal rule to fail")
        expect(self.hello(rig, bad), 200, "hello")
        refused = self.client.post(f"{API}/agent/projects/{self.mosaic_id}/join", rig.token, self.join_body(rig))
        need(refused.status == 409, f"a rig that cannot help joined: {refused.status}")
        need(isinstance(refused.detail, str) and refused.detail, "the 409 gives no reason")
        expect(self.hello(rig, self.good_profile(rig.name)), 200, "hello")
        data = expect(self.client.post(f"{API}/agent/projects/{self.mosaic_id}/join", rig.token,
                                       self.join_body(rig)), 200, "join after fixing the rig")
        self.task2 = data.get("task")
        return f"409: {refused.detail}"

    def join_single(self) -> str:
        rig = self.rig(0)
        if self.single_id is None:
            raise Skip("no --single-project-id")
        project = self.project(self.single_id)
        data = expect(self.client.post(f"{API}/agent/projects/{self.single_id}/join", rig.token,
                                       self.join_body(rig)), 200, "join single")
        task = data.get("task") or {}
        cells = task.get("cells") or []
        need(len(cells) == 1, f"a single target was cut into {len(cells)} cells")
        region = project["region"]
        cell = cells[0]
        need(abs(float(cell["ra"]) - float(region["ra"])) < 0.01 and abs(float(cell["dec"]) - float(region["dec"])) < 0.01,
             "the one cell is not centred on the object")
        self.check_cells(rig, task)
        return "one cell, centred, the rig's own frame"

    # Tonight, dealing and the Moon -------------------------------------------

    def tonight_first(self) -> str:
        rig = self.rig(0)
        reply = self.tonight(rig, self.night("n1"), 0.9, 0.9)
        projects = {task.get("project") for task in reply.get("tasks") or []}
        need(self.mosaic_id in projects, "tonight leaves out the mosaic share")
        if self.single_id is not None:
            need(self.single_id in projects, "tonight leaves out the single share")
        by_project = reply.get("requirementsByProject")
        if by_project is not None:
            need(self.mosaic_id in by_project, "requirementsByProject leaves out the mosaic")
        self.night1 = self.share_of(reply, self.mosaic_id)
        need(self.night1.get("share"), "tonight deals no panels on the mosaic")
        return f"{len(reply['tasks'])} shares; {len(self.night1['share'])} mosaic panels tonight"

    def bright_moon(self) -> str:
        if self.night1 is None:
            raise Skip("needs tonight's list")
        wanted = set(self.wanted(self.project(self.mosaic_id)))
        if not wanted & RED_NARROWBAND or not wanted & DARK_NIGHT:
            raise Skip("the mosaic needs both a red narrowband and a dark-night filter")
        letter = self.mosaic_letter()
        need(letter in RED_NARROWBAND, f"under a bright Moon the rig was dealt {letter}, not H or S")
        return f"dealt {letter}"

    def one_filter(self) -> str:
        if self.night1 is None:
            raise Skip("needs tonight's list")
        visit = self.night1.get("visit") or {}
        frames = visit.get("frames") or {}
        letters = {fold(name) for name in frames}
        need(len(letters) == 1, f"a mosaic night deals {sorted(letters) or 'no'} filters, not one")
        if visit.get("filter"):
            need(letters == {fold(visit["filter"])}, "visit.filter and visit.frames disagree")
        return f"one filter, {next(iter(letters))}"

    def min_frames(self) -> str:
        if self.night1 is None:
            raise Skip("needs tonight's list")
        least = self.min_frames_wanted(self.mosaic_id)
        frames = (self.night1.get("visit") or {}).get("frames") or {}
        need(frames, "the visit names no frames")
        short = {k: v for k, v in frames.items() if int(v) < least}
        need(not short, f"visits shorter than {least} frames: {short}")
        return f"each filter at least {least} frames"

    def two_rigs(self) -> str:
        rig = self.rig(1)
        if self.night1 is None or self.task2 is None:
            raise Skip("needs both rigs on the mosaic")
        reply = self.tonight(rig, self.night("n1"), 0.9, 0.9)
        self.night1_rig2 = self.share_of(reply, self.mosaic_id)
        first = {self.cell_key(self.night1, i) for i in self.night1.get("share") or []}
        second = {self.cell_key(self.night1_rig2, i) for i in self.night1_rig2.get("share") or []}
        need(second, "the second rig was dealt no panels")
        free = len(self.night1_rig2.get("cells") or []) - len(first)
        if free < len(second):
            raise Skip("too few cells for both rigs to have their own")
        both = first & second
        need(not both, f"both rigs were sent to the same panels: {sorted(both)}")
        return f"{len(first)} and {len(second)} panels, none shared"

    @staticmethod
    def cell_key(task: dict[str, Any], index: int) -> tuple[float, float]:
        cell = task["cells"][index]
        return (round(float(cell["ra"]), 3), round(float(cell["dec"]), 3))

    def list_holds(self) -> str:
        rig = self.rig(0)
        if self.night1 is None:
            raise Skip("needs tonight's list")
        if self.night1_rig2 is not None:
            # The other rig's frames come in mid-night; rig 1's list must not move.
            letter = fold((self.night1_rig2.get("visit") or {}).get("filter") or "") or self.mosaic_letter()
            records = [self.record(self.rig(1), self.night1_rig2, i, self.night("n1"), letter,
                                   self.min_frames_wanted(self.mosaic_id))
                       for i in self.night1_rig2.get("share") or []]
            self.report(self.rig(1), records)
        again = self.share_of(self.tonight(rig, self.night("n1"), 0.9, 0.9), self.mosaic_id)
        need(again.get("share") == self.night1.get("share"),
             f"the list moved within the night: {self.night1.get('share')} became {again.get('share')}")
        need(again.get("version") == self.night1.get("version"),
             f"the version moved within the night: {self.night1.get('version')} became {again.get('version')}")
        return "same panels and version" + (", after the other rig reported" if self.night1_rig2 else "")

    def report_order(self) -> str:
        rig = self.rig(0)
        if self.night1 is None:
            raise Skip("needs tonight's list")
        letter = self.mosaic_letter()
        wants = self.project(self.mosaic_id).get("requirements") or {}
        frames = max(self.min_frames_wanted(self.mosaic_id), 11)
        records = [self.record(rig, self.night1, i, self.night("n1"), letter, frames)
                   for i in self.night1.get("share") or []]
        if wants.get("maxHfr") is not None:
            records.append(self.record(rig, self.night1, self.night1["share"][0], self.night("bad"), letter,
                                       frames, hfr=float(wants["maxHfr"]) * 1.6))
        recorded = self.report(rig, records)
        for index, answer in enumerate(recorded[:len(self.night1["share"])]):
            need(answer.get("accepted") is True,
                 f"a good record was rejected: {(answer.get('verdict') or {}).get('summary')}")
            need(answer.get("duplicate") is False, f"record {index} was taken for a repeat")
        if len(recorded) > len(self.night1["share"]):
            need(recorded[-1].get("accepted") is False, "the bad record, sent last, was answered as accepted")
        return f"{len(recorded)} verdicts in the order sent"

    def next_night(self) -> str:
        rig = self.rig(0)
        if self.night1 is None:
            raise Skip("needs tonight's list")
        self.night2 = self.share_of(self.tonight(rig, self.night("n2"), 0.0, 0.0), self.mosaic_id)
        first = {self.cell_key(self.night1, i) for i in self.night1.get("share") or []}
        second = {self.cell_key(self.night2, i) for i in self.night2.get("share") or []}
        need(second, "the next night deals no panels")
        free = len(self.night2.get("cells") or []) - len(first)
        if free < len(second):
            raise Skip("too few cells to move on")
        need(not first & second,
             f"the next night sent the rig back to panels it shot last night: {sorted(first & second)}")
        return "new panels, away from last night's"

    def dark_moon(self) -> str:
        night2 = self.night2
        if night2 is None:
            raise Skip("needs the next night's list")
        wanted = set(self.wanted(self.project(self.mosaic_id)))
        if not wanted & DARK_NIGHT or not wanted & RED_NARROWBAND:
            raise Skip("the mosaic needs both a red narrowband and a dark-night filter")
        visit = night2.get("visit") or {}
        letter = fold(visit.get("filter") or next(iter(visit.get("frames") or {}), ""))
        need(letter in DARK_NIGHT, f"on a dark night the rig was dealt {letter or 'nothing'}, not L, R, G, B or O")
        return f"dealt {letter}"

    # Judging and duplicates ---------------------------------------------------

    def judged_reject(self) -> str:
        rig = self.rig(0)
        if self.night1 is None:
            raise Skip("needs tonight's list")
        wants = self.project(self.mosaic_id).get("requirements") or {}
        if wants.get("maxHfr") is None:
            raise Skip("the mosaic sets no maxHfr")
        letter = self.mosaic_letter()
        record = self.record(rig, self.night1, self.night1["share"][0], self.night("hfr"), letter,
                             max(self.min_frames_wanted(self.mosaic_id), 11), hfr=float(wants["maxHfr"]) * 1.6)
        answer = self.report(rig, [record])[0]
        verdict = answer.get("verdict") or {}
        need(answer.get("accepted") is False and verdict.get("accepted") is False,
             "stars far past maxHfr were accepted")
        need(verdict.get("reasons"), "the rejection gives no reason")
        return f"rejected: {verdict['reasons'][0]}"

    def unverified(self) -> str:
        rig = self.rig(0)
        if self.night1 is None:
            raise Skip("needs tonight's list")
        wants = self.project(self.mosaic_id).get("requirements") or {}
        if wants.get("maxHfr") is None:
            raise Skip("the mosaic sets no maxHfr")
        record = self.record(rig, self.night1, self.night1["share"][0], self.night("unv"), self.mosaic_letter(),
                             max(self.min_frames_wanted(self.mosaic_id), 11))
        record["hfr"] = None
        verdict = self.report(rig, [record])[0].get("verdict") or {}
        need(verdict.get("unverified"), "a missing star size is not listed as unverified")
        return f"unverified: {verdict['unverified'][0]}"

    def duplicates(self) -> str:
        rig = self.rig(0)
        if self.night1 is None:
            raise Skip("needs tonight's list")
        letter = self.mosaic_letter()
        least = max(self.min_frames_wanted(self.mosaic_id), 11)
        exposure = self.exposure(rig, letter)
        index = self.night1["share"][0]
        night = self.night("dup")
        before = self.collected(rig, letter)
        first = self.report(rig, [self.record(rig, self.night1, index, night, letter, least)])[0]
        need(first.get("accepted") and first.get("duplicate") is False, "the first record was not taken")
        larger = self.report(rig, [self.record(rig, self.night1, index, night, letter, least + 4)])[0]
        need(larger.get("duplicate") is True, "the same night, filter and panel again was not a duplicate")
        smaller = self.report(rig, [self.record(rig, self.night1, index, night, letter, least - 6)])[0]
        need(smaller.get("duplicate") is True, "a smaller repeat was not a duplicate")
        gained = self.collected(rig, letter) - before
        want = (least + 4) * exposure / 3600.0
        need(abs(gained - want) <= 0.02,
             f"collected {letter} rose by {gained:.2f} h; the larger figure alone is {want:.2f} h")
        return f"one record at the larger figure ({want:.2f} h)"

    def report_foreign(self) -> str:
        if self.task is None:
            raise Skip("needs rig 1's share")
        rig = self.rig(1)
        record = {"project": self.mosaic_id, "task": self.task["id"], "night": self.night("foreign"),
                  "filterName": "Ha", "panel": "0", "frames": 11, "seconds": 3300.0, "exposure": 300.0}
        response = self.client.post(f"{API}/agent/report", rig.token, {"contributions": [record]})
        need(response.status == 403, f"reporting on another rig's share gave {response.status}, not 403")
        return "403"

    # Presence and errors ------------------------------------------------------

    def presence(self) -> str:
        rig = self.rig(0)
        data = expect(self.client.get(f"{API}/presence", rig.token), 200, "presence")
        mine = [t for t in data.get("telescopes") or [] if t.get("id") == rig.id]
        need(mine, "the telescope is not in presence")
        need(mine[0].get("online") is True, "a telescope that just said hello is not online")
        need(mine[0].get("state") == "imaging", f"presence state is {mine[0].get('state')!r}, not what it shared")
        need(int(data.get("online") or 0) >= 1, "presence counts nobody online")
        return "online, with what it shared"

    def not_found(self) -> str:
        rig = self.rig(0)
        nobody = "f" * 12
        join = self.client.post(f"{API}/agent/projects/{nobody}/join", rig.token, self.join_body(rig))
        need(join.status == 404, f"joining an unknown project gave {join.status}")
        need(isinstance(join.detail, str), "the 404 has no detail sentence")
        state = self.client.post(f"{API}/agent/task/{nobody}", rig.token, {"state": "accepted"})
        need(state.status == 404, f"answering an unknown share gave {state.status}")
        if self.task2 is not None:
            other = self.client.post(f"{API}/agent/task/{self.task2['id']}", rig.token, {"state": "declined"})
            need(other.status == 404, f"answering another rig's share gave {other.status}")
        return "404 with detail"

    def invalid_body(self) -> str:
        rig = self.rig(0)
        target = (self.task or {}).get("id") or "f" * 12
        response = self.client.post(f"{API}/agent/task/{target}", rig.token, {"state": "maybe"})
        need(response.status == 422, f"a state outside the protocol gave {response.status}")
        detail = response.detail
        need(isinstance(detail, list) and detail and "loc" in detail[0],
             "the 422 does not list the bad fields")
        return "422 naming the field"

    def accept_share(self) -> str:
        rig = self.rig(0)
        if self.task is None:
            raise Skip("needs rig 1's share")
        data = expect(self.client.post(f"{API}/agent/task/{self.task['id']}", rig.token, {"state": "accepted"}),
                      200, "accept")
        need((data.get("task") or {}).get("state") == "accepted", "an accepted share does not say accepted")
        return "accepted"

    def fixed_rotation(self) -> str:
        rig = self.rig(1)
        if self.task2 is None:
            raise Skip("needs rig 2 on the mosaic")
        expect(self.hello(rig, self.good_profile(rig.name, rotation=90.0)), 200, "hello")
        task = self.share_of(self.tonight(rig, self.night("n3"), 0.0, 0.0), self.mosaic_id)
        self.check_cells(rig, task, fixed=90.0)
        return f"{len(task['cells'])} cells at 90°"


def main(argv: list[str] | None = None, out: Callable[[str], None] = print) -> int:
    parser = argparse.ArgumentParser(description="Check an AstroCollab 0.2 server.")
    parser.add_argument("--server", required=True, help="the server's address, such as http://127.0.0.1:8800")
    parser.add_argument("--person-token", help="a signed-in person's token, used to enrol two telescopes")
    parser.add_argument("--pairing-code", action="append", default=[],
                        help="an unused pairing code; give two to pair both telescopes, or one more "
                             "to check pairing alongside --person-token")
    parser.add_argument("--project-id", required=True, help="an open mosaic project")
    parser.add_argument("--single-project-id", help="an open single-target project")
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT), help="the OpenAPI document")
    parser.add_argument("--schemas", default=str(DEFAULT_SCHEMAS), help="the standalone JSON Schemas")
    args = parser.parse_args(argv)
    if not args.person_token and len(args.pairing_code) < 2:
        parser.error("give --person-token, or two --pairing-code values")
    contract = Contract(Path(args.contract), Path(args.schemas))
    client = Client(args.server, contract)
    suite = Suite(client, args.person_token, args.pairing_code, args.project_id, args.single_project_id, out)
    return suite.run()


if __name__ == "__main__":
    sys.exit(main())
