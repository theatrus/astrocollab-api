"""The AstroCollab 0.2 reference server, against the contract and the protocol's rules.

Every reply in these tests is checked against the schema the contract gives for
that route and status, using only schemas/ (a registry keyed by $id) and the
route table in openapi/astrocollab.yaml.
"""
import json
from pathlib import Path
import re
import sys
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reference import client, rules  # noqa: E402
from reference.server import serve  # noqa: E402

BASE = "https://astrocollabapi.com/schemas/"
REGISTRY = Registry().with_resources(
    (schema["$id"], Resource.from_contents(schema))
    for schema in (json.loads(p.read_text()) for p in (ROOT / "schemas").glob("*.schema.json")))


def _routes():
    """(method, path pattern, {status: schema name}) for every operation."""
    doc = yaml.safe_load((ROOT / "openapi/astrocollab.yaml").read_text())
    table = []
    for path, item in doc["paths"].items():
        pattern = re.compile(re.sub(r"\{[^}]+\}", "[^/]+", path))
        for method, op in item.items():
            schemas = {}
            for status, response in op["responses"].items():
                ref = response.get("content", {}).get("application/json", {}).get("schema", {}).get("$ref")
                if ref:
                    schemas[status] = ref.rsplit("/", 1)[1]
            table.append((method.upper(), pattern, schemas))
    return table


ROUTES = _routes()
HELLO = json.loads((ROOT / "examples/hello.request.json").read_text())
PROFILE = HELLO["profile"]
M31, M51 = "M31 halo in narrowband", "M51 in LRGB"


def schema_errors(name, value):
    validator = Draft202012Validator({"$ref": BASE + f"{name}.schema.json"}, registry=REGISTRY)
    return [f"{list(e.absolute_path)}: {e.message}" for e in validator.iter_errors(value)]


class Harness(unittest.TestCase):
    """A fresh server per test, and calls that check every reply."""

    def setUp(self):
        self.httpd = serve()
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = self.httpd.base_url
        self.tools = self.httpd.tools
        self.clock = [self.httpd.state.now()]
        self.httpd.state.now = lambda: self.clock[0]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def advance(self, seconds):
        self.clock[0] += seconds

    def call(self, method, path, token=None, body=None, raw=None, form=None):
        headers = {}
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request) as response:
                status, text, kind = response.status, response.read(), response.headers["Content-Type"]
        except urllib.error.HTTPError as error:
            with error:
                status, text, kind = error.code, error.read(), error.headers["Content-Type"]
        if not kind.startswith("application/json"):
            return status, text.decode()
        value = json.loads(text)
        bare = urllib.parse.urlsplit(path).path
        for route_method, pattern, schemas in ROUTES:
            if route_method == method and pattern.fullmatch(bare):
                name = schemas.get(str(status))
                self.assertIsNotNone(name, f"{method} {bare} answered {status}, which the contract "
                                           f"does not list: {value}")
                self.assertEqual(schema_errors(name, value), [], f"{method} {bare} {status}")
        return status, value

    # -- helpers -----------------------------------------------------------
    def telescope(self, name="Vega 530", profile=None, person="Vega Observatory"):
        """A paired telescope that has said hello. Returns its token."""
        code = self.tools.issue_pairing_code(person)
        status, made = self.call("POST", "/api/v1/pair", body={"code": code, "name": name})
        self.assertEqual(status, 200, made)
        token = made["token"]
        status, _ = self.call("POST", "/api/v1/agent/hello", token,
                              {"protocol": 1, "profile": {**PROFILE, "name": name, **(profile or {})}})
        self.assertEqual(status, 200)
        return token

    def join(self, token, project=M31, body=None):
        project_id = self.httpd.sample_project_ids.get(project, project)
        return self.call("POST", f"/api/v1/agent/projects/{project_id}/join", token,
                         body if body is not None else {"hours": 0})

    def tonight(self, token, night="2026-10-05", moon=None, moon_up=None):
        query = {}
        if night:
            query["night"] = night
        if moon is not None:
            query["moon"], query["moonUp"] = moon, moon_up
        status, answer = self.call("GET", "/api/v1/agent/task?" + urllib.parse.urlencode(query), token)
        self.assertEqual(status, 200, answer)
        return answer

    def record(self, share, index, **changes):
        cell = share["cells"][index]
        visit = share["visit"]
        letter = visit.get("filter") or next(iter(visit["frames"]))
        exposure = next(f["exposure"] for f in share["filters"] if f["filter"] == letter)
        frames = visit["frames"][letter]
        entry = {"project": share["project"], "task": share["id"], "night": "2026-10-05",
                 "panel": str(index), "filterName": letter, "frames": frames,
                 "seconds": frames * exposure, "exposure": exposure,
                 "footprint": {k: cell[k] for k in ("ra", "dec", "width", "height", "rotation")},
                 "scale": 1.46, "focalLength": 530.0, "hfr": 2.3, "guideRms": 0.6,
                 "moonIllumination": 0.1, "moonSeparation": 90.0, "calibrated": True,
                 "bandpass": 7.0, "colour": False}
        entry.update(changes)
        return entry


class Tokens(Harness):
    def test_device_sign_in_then_enrol(self):
        status, started = self.call("POST", "/api/v1/auth/login")
        self.assertEqual(status, 200)
        code = started["code"]
        self.assertEqual(self.call("GET", f"/api/v1/auth/poll?code={code}")[1]["state"], "pending")
        status, page = self.call("GET", urllib.parse.urlsplit(started["url"]).path + "?code=" + code)
        self.assertIn("Approve", page)
        status, page = self.call("POST", "/signin", form={"code": code, "name": "Vega Observatory"})
        self.assertEqual(status, 200)
        status, done = self.call("GET", f"/api/v1/auth/poll?code={code}")
        self.assertEqual(done["state"], "done")
        person = done["token"]
        self.assertEqual(self.call("GET", f"/api/v1/auth/poll?code={code}")[1]["state"], "claimed")
        self.assertEqual(self.call("GET", "/api/v1/auth/me", person)[1]["name"], "Vega Observatory")
        status, made = self.call("POST", "/api/v1/agents", person, {"name": "Vega 530"})
        self.assertEqual(status, 200)
        listed = self.call("GET", "/api/v1/agents", person)[1]["agents"]
        self.assertEqual([t["name"] for t in listed], ["Vega 530"])
        self.assertNotIn("token", listed[0])
        self.assertEqual(self.call("POST", "/api/v1/agent/hello", made["token"], {"profile": PROFILE})[0], 200)
        self.assertEqual(self.call("POST", "/api/v1/auth/logout", person)[1], {"signedOut": True})
        self.assertEqual(self.call("GET", "/api/v1/auth/me", person)[0], 401)

    def test_an_expired_sign_in_code_says_so(self):
        code = self.call("POST", "/api/v1/auth/login")[1]["code"]
        self.advance(601)
        self.assertEqual(self.call("GET", f"/api/v1/auth/poll?code={code}")[1]["state"], "expired")

    def test_tokens_are_kept_apart(self):
        telescope = self.telescope()
        person = self.tools.sign_in("Vega Observatory")
        for path in ("/api/v1/auth/me", "/api/v1/agents"):
            self.assertEqual(self.call("GET", path, telescope)[0], 401, path)
        self.assertEqual(self.call("POST", "/api/v1/agents", telescope, {"name": "Sneaky"})[0], 401)
        self.assertEqual(self.call("POST", "/api/v1/agent/hello", person, {"profile": PROFILE})[0], 401)
        self.assertEqual(self.call("GET", "/api/v1/agent/task", person)[0], 401)
        self.assertEqual(self.call("GET", "/api/v1/agent/task")[0], 401)
        self.assertEqual(self.call("GET", "/api/v1/agent/task", "not-a-token")[0], 401)

    def test_a_pairing_code_works_once_and_expires(self):
        code = self.tools.issue_pairing_code("Vega Observatory")
        self.assertEqual(self.call("POST", "/api/v1/pair", body={"code": code, "name": "A"})[0], 200)
        self.assertEqual(self.call("POST", "/api/v1/pair", body={"code": code, "name": "B"})[0], 401)
        late = self.tools.issue_pairing_code("Vega Observatory")
        self.advance(3601)
        self.assertEqual(self.call("POST", "/api/v1/pair", body={"code": late, "name": "C"})[0], 401)

    def test_repeated_bad_pairing_codes_get_429(self):
        for attempt in range(5):
            self.assertEqual(self.call("POST", "/api/v1/pair", body={"code": f"wrong-{attempt}",
                                                                     "name": "X"})[0], 401)
        good = self.tools.issue_pairing_code("Vega Observatory")
        self.assertEqual(self.call("POST", "/api/v1/pair", body={"code": good, "name": "X"})[0], 429)
        self.advance(61)
        self.assertEqual(self.call("POST", "/api/v1/pair", body={"code": good, "name": "X"})[0], 200)

    def test_discovery(self):
        status, health = self.call("GET", "/api/v1/health")
        self.assertEqual((status, health["protocol"]), (200, 1))
        self.assertEqual(set(health["features"]), {"signin", "pairing"})
        self.assertTrue(self.call("GET", "/api/v1/auth")[1]["discord"])


class Hello(Harness):
    def test_a_newer_protocol_is_refused(self):
        token = self.telescope()
        status, answer = self.call("POST", "/api/v1/agent/hello", token, {"protocol": 2, "profile": PROFILE})
        self.assertEqual(status, 409)
        self.assertIn("protocol", answer["detail"])

    def test_blank_numbers_are_unknown_not_refused(self):
        token = self.telescope()
        status, _ = self.call("POST", "/api/v1/agent/hello", token,
                              {"profile": {"name": "Blank", "focalLength": "", "sensorWidth": "abc",
                                           "filters": {"Ha": ""}, "binning": ""}})
        self.assertEqual(status, 200)
        listing = self.call("GET", "/api/v1/agent/projects", token)[1]["projects"]
        scale = next(c for c in listing[0]["compatibility"]["checks"] if c["check"] == "scale")
        self.assertIsNone(scale["ok"])

    def test_bodies_that_break_their_type_get_422(self):
        token = self.telescope()
        status, answer = self.call("POST", "/api/v1/agent/hello", token, {"profile": ["not", "a", "profile"]})
        self.assertEqual(status, 422)
        self.assertEqual(answer["detail"][0]["loc"][:2], ["body", "profile"])
        self.assertEqual(self.call("POST", "/api/v1/agent/hello", token, raw=b"{nope")[0], 422)
        self.assertEqual(self.join(token, body={"hours": 30})[0], 422)
        self.assertEqual(self.call("GET", "/api/v1/agent/task?moon=2&moonUp=0.5", token)[0], 422)
        person = self.tools.sign_in("Someone")
        self.assertEqual(self.call("POST", "/api/v1/agents", person, {"name": ""})[0], 422)

    def test_presence_shows_who_is_on_the_sky(self):
        token = self.telescope()
        self.call("POST", "/api/v1/agent/hello", token,
                  {"profile": PROFILE, "presence": {"ra": 0.71, "dec": 41.3, "state": "imaging",
                                                    "target": "M31"}})
        other = self.telescope("Deneb 200", person="Deneb Observatory")
        sky = self.call("GET", "/api/v1/presence", token)[1]
        self.assertEqual((sky["online"], sky["people"]), (2, 2))
        mine = next(t for t in sky["telescopes"] if t["name"] == "Vega 530")
        self.assertEqual((mine["state"], mine["target"], mine["ra"]), ("imaging", "M31", 0.71))
        self.advance(26 * 60)
        self.call("POST", "/api/v1/agent/hello", other, {"profile": PROFILE})
        sky = self.call("GET", "/api/v1/presence", other)[1]
        self.assertEqual(sky["online"], 1)
        self.assertFalse(next(t for t in sky["telescopes"] if t["name"] == "Vega 530")["online"])
        self.advance(25 * 3600)
        self.call("POST", "/api/v1/agent/hello", other, {"profile": PROFILE})
        names = [t["name"] for t in self.call("GET", "/api/v1/presence", other)[1]["telescopes"]]
        self.assertEqual(names, ["Deneb 200"])


class Joining(Harness):
    def test_browsing_says_whether_the_rig_can_help(self):
        token = self.telescope()
        listing = {p["name"]: p for p in self.call("GET", "/api/v1/agent/projects", token)[1]["projects"]}
        self.assertTrue(listing[M31]["compatibility"]["ok"])
        self.assertFalse(listing[M51]["compatibility"]["ok"])
        self.assertIn("800 mm", listing[M51]["compatibility"]["summary"])

    def test_a_rig_that_cannot_help_is_refused_with_the_reason(self):
        token = self.telescope()
        status, answer = self.join(token, M51)
        self.assertEqual(status, 409)
        self.assertIn("800 mm", answer["detail"])
        self.assertEqual(self.join(token, "000000000000")[0], 404)

    def test_a_rig_with_no_optics_must_describe_itself_first(self):
        code = self.tools.issue_pairing_code()
        token = self.call("POST", "/api/v1/pair", body={"code": code, "name": "Blank"})[1]["token"]
        status, answer = self.join(token)
        self.assertEqual(status, 400)
        self.assertIn("focal length", answer["detail"])

    def test_a_rig_with_some_of_the_filters_is_dealt_only_those(self):
        token = self.telescope(profile={"filters": {"Ha": 7.0}, "exposures": {"Ha": 300}})
        listing = {p["name"]: p for p in self.call("GET", "/api/v1/agent/projects", token)[1]["projects"]}
        verdict = listing[M31]["compatibility"]
        self.assertTrue(verdict["ok"])
        self.assertTrue(any("no O" in c["detail"] for c in verdict["checks"] if c["ok"]))
        status, joined = self.join(token)
        self.assertEqual(status, 200, joined)
        self.assertEqual([f["filter"] for f in joined["task"]["filters"]], ["H"])
        for night, moon in (("2026-10-05", 0.05), ("2026-10-06", 0.95)):
            visit = self.tonight(token, night, moon, 0.9)["tasks"][0]["visit"]
            self.assertEqual((visit["filter"], list(visit["frames"])), ("H", ["H"]))
        # A filter wider than the limit does not count: this rig is dealt O only.
        wide = self.telescope("Wide", profile={"filters": {"Ha": 12.0, "OIII": 7.0}})
        self.assertEqual([f["filter"] for f in self.join(wide)[1]["task"]["filters"]], ["O"])

    def test_a_rig_with_none_of_the_filters_cannot_join(self):
        token = self.telescope(profile={"filters": {"L": None, "Ha": 12.0}})
        listing = {p["name"]: p for p in self.call("GET", "/api/v1/agent/projects", token)[1]["projects"]}
        self.assertFalse(listing[M31]["compatibility"]["ok"])
        status, answer = self.join(token)
        self.assertEqual(status, 409)
        self.assertIn("no O", answer["detail"])

    def test_sub_lengths_outside_the_project_are_refused(self):
        token = self.telescope()
        status, answer = self.join(token, body={"exposures": {"Ha": 900}})
        self.assertEqual(status, 409)
        self.assertIn("900", answer["detail"])

    def test_joining_gives_an_accepted_share_tiled_with_the_rigs_own_camera(self):
        token = self.telescope()
        status, joined = self.join(token)
        self.assertEqual(status, 200)
        share = joined["task"]
        self.assertEqual(share["state"], "accepted")
        width, height = rules.field(PROFILE)
        for cell in share["cells"]:
            self.assertAlmostEqual(cell["width"], width, places=6)
            self.assertAlmostEqual(cell["height"], height, places=6)
        # The cells cover the whole region.
        region = share["region"]
        for dx in (-0.49, 0, 0.49):
            for dy in (-0.49, 0, 0.49):
                dec = region["dec"] + dy * region["height"]
                ra = region["ra"] + dx * region["width"] / max(0.1, __import__("math").cos(__import__("math").radians(dec)))
                self.assertTrue(any(rules.contains(c, ra, dec) for c in share["cells"]), (dx, dy))
        again = self.join(token)[1]
        self.assertTrue(again["alreadyJoined"])
        self.assertEqual(again["task"]["id"], share["id"])

    def test_a_fixed_camera_is_tiled_at_its_own_angle(self):
        token = self.telescope(profile={"rotation": 90.0})
        cells = self.join(token)[1]["task"]["cells"]
        self.assertTrue(all(c["rotation"] == 90.0 for c in cells))
        rotator = self.telescope("Rotator", profile={"rotation": None})
        self.assertTrue(all(c["rotation"] == 0.0 for c in self.join(rotator)[1]["task"]["cells"]))

    def test_a_single_target_is_one_frame_for_everybody(self):
        project = self.tools.create_project("Small", {"ra": 202.47, "dec": 47.2, "width": 0.2,
                                                      "height": 0.15}, "single", {"L": 5},
                                            {"filters": {"L": None}})
        token = self.telescope()
        share = self.join(token, project)[1]["task"]
        self.assertEqual(len(share["cells"]), 1)
        self.assertEqual(share["share"], [0])

    def test_declining_a_share_takes_it_off_tonight(self):
        token = self.telescope()
        share = self.join(token)[1]["task"]
        status, answer = self.call("POST", f"/api/v1/agent/task/{share['id']}", token, {"state": "declined"})
        self.assertEqual((status, answer["task"]["state"]), (200, "declined"))
        self.assertEqual(self.tonight(token)["tasks"], [])
        other = self.telescope("Other", person="Other")
        self.assertEqual(self.call("POST", f"/api/v1/agent/task/{share['id']}", other,
                                   {"state": "accepted"})[0], 404)


class Dealing(Harness):
    def test_a_list_holds_for_the_night_and_moves_on_the_next(self):
        token = self.telescope()
        self.join(token)
        first = self.tonight(token, "2026-10-05", 0.1, 0.1)["tasks"][0]
        again = self.tonight(token, "2026-10-05", 0.9, 0.9)["tasks"][0]
        self.assertEqual((again["share"], again["visit"], again["version"]),
                         (first["share"], first["visit"], first["version"]))
        nextnight = self.tonight(token, "2026-10-06", 0.9, 0.9)["tasks"][0]
        self.assertEqual(nextnight["assignedNight"], "2026-10-06")
        self.assertGreater(nextnight["version"], first["version"])

    def test_a_list_with_no_night_holds_twenty_hours(self):
        token = self.telescope()
        self.join(token)
        first = self.tonight(token, None)["tasks"][0]
        self.advance(19 * 3600)
        self.assertEqual(self.tonight(token, None)["tasks"][0]["assignedAt"], first["assignedAt"])
        self.advance(2 * 3600)
        self.assertGreater(self.tonight(token, None)["tasks"][0]["assignedAt"], first["assignedAt"])

    def test_one_filter_a_night_on_a_mosaic_chosen_by_the_moon(self):
        token = self.telescope()
        self.join(token)
        dark = self.tonight(token, "2026-10-05", 0.05, 0.1)["tasks"][0]["visit"]
        bright = self.tonight(token, "2026-10-06", 0.95, 0.9)["tasks"][0]["visit"]
        self.assertEqual(len(dark["frames"]), 1)
        self.assertEqual(dark["filter"], "O")
        self.assertIn(bright["filter"], ("H", "S"))

    def test_a_visit_is_never_shorter_than_the_minimum(self):
        token = self.telescope(profile={"hoursPerNight": 0.1})
        self.join(token)
        visit = self.tonight(token)["tasks"][0]["visit"]
        self.assertGreaterEqual(min(visit["frames"].values()), 10)
        roomy = self.telescope("Roomy", profile={"hoursPerNight": 6})
        self.join(roomy)
        share = self.tonight(roomy)["tasks"][0]
        self.assertGreater(len(share["share"]), 1)
        self.assertGreaterEqual(min(share["visit"]["frames"].values()), 10)

    def test_two_rigs_on_one_night_get_different_panels(self):
        first = self.telescope("A", profile={"hoursPerNight": 2})
        second = self.telescope("B", profile={"hoursPerNight": 2}, person="B's owner")
        self.join(first)
        self.join(second)
        a = self.tonight(first)["tasks"][0]["share"]
        b = self.tonight(second)["tasks"][0]["share"]
        self.assertTrue(a and b)
        self.assertFalse(set(a) & set(b), (a, b))

    def test_a_rig_moves_on_from_panels_it_has_shot(self):
        token = self.telescope(profile={"hoursPerNight": 2})
        self.join(token)
        share = self.tonight(token, "2026-10-05")["tasks"][0]
        shot = share["share"]
        self.call("POST", "/api/v1/agent/report", token,
                  {"contributions": [self.record(share, i) for i in shot]})
        nextnight = self.tonight(token, "2026-10-06")["tasks"][0]["share"]
        self.assertFalse(set(nextnight) & set(shot), (shot, nextnight))

    def test_a_single_target_night_spreads_over_its_filters(self):
        project = self.tools.create_project("LRGB", {"ra": 202.47, "dec": 47.2, "width": 0.2,
                                                     "height": 0.15}, "single",
                                            {"L": 10, "R": 2}, {"filters": {"L": None, "R": None}})
        token = self.telescope(profile={"filters": {"L": None, "R": None},
                                        "exposures": {"L": 120, "R": 120}})
        self.join(token, project)
        visit = self.tonight(token)["tasks"][0]["visit"]
        self.assertEqual(set(visit["frames"]), {"L", "R"})
        self.assertGreater(visit["frames"]["L"], visit["frames"]["R"])


class Reports(Harness):
    def setUp(self):
        super().setUp()
        self.token = self.telescope()
        self.join(self.token)
        self.share = self.tonight(self.token)["tasks"][0]

    def report(self, *entries, token=None):
        status, answer = self.call("POST", "/api/v1/agent/report", token or self.token,
                                   {"contributions": list(entries)})
        self.assertEqual(status, 200, answer)
        return answer["recorded"]

    def test_good_data_is_accepted_and_counted(self):
        index = self.share["share"][0]
        [result] = self.report(self.record(self.share, index))
        self.assertTrue(result["accepted"])
        listing = self.call("GET", "/api/v1/agent/projects", self.token)[1]["projects"]
        project = next(p for p in listing if p["name"] == M31)
        letter = self.share["visit"]["filter"]
        frames = self.share["visit"]["frames"][letter]
        self.assertAlmostEqual(project["collected"][letter], round(frames * 300 / 3600, 2))
        self.assertEqual(project["participants"], 1)

    def test_each_broken_rule_is_named(self):
        index = self.share["share"][0]
        cases = {
            "stars": {"hfr": 5.0},
            "filter": {"filterName": "L"},
            "bandpass": {"bandpass": 12.0},
            "sub length": {"exposure": 30.0},
            "Moon": {"moonSeparation": 10.0},
        }
        for night, (rule, change) in enumerate(cases.items()):
            [result] = self.report(self.record(self.share, index, night=f"2026-11-{night + 1:02d}", **change))
            self.assertFalse(result["accepted"], rule)
            self.assertTrue(result["verdict"]["reasons"], rule)

    def test_a_missing_measurement_is_unverified_not_passed(self):
        [result] = self.report(self.record(self.share, self.share["share"][0], hfr=None, scale=None))
        self.assertTrue(result["accepted"])
        unverified = " ".join(result["verdict"]["unverified"])
        self.assertIn("star size", unverified)
        self.assertNotIn("scale", unverified)  # this project sets no scale rule

    def test_other_rules_are_judged_only_on_what_the_record_gives(self):
        [result] = self.report(self.record(self.share, self.share["share"][0], bandpass=None,
                                           moonSeparation=None, focalLength=None))
        self.assertTrue(result["accepted"])
        self.assertEqual(result["verdict"]["unverified"], [])

    def test_colour_cameras_and_calibration_follow_the_project(self):
        project = self.tools.create_project("Strict", {"ra": 10.68, "dec": 41.27, "width": 1,
                                                       "height": 1}, "mosaic", {"H": 2},
                                            {"filters": {"H": None}, "acceptColour": False,
                                             "requireCalibrated": True})
        entry = {"project": project, "night": "2026-10-05", "panel": "0", "filterName": "Ha",
                 "frames": 10, "seconds": 3000, "exposure": 300, "colour": True, "calibrated": False}
        [result] = self.report(entry)
        reasons = " ".join(result["verdict"]["reasons"])
        self.assertIn("colour", reasons)
        self.assertIn("calibrated", reasons)

    def test_the_same_panel_again_keeps_the_larger_figure(self):
        index = self.share["share"][0]
        big = self.record(self.share, index)
        [first] = self.report(big)
        [again] = self.report(dict(big, frames=5, seconds=1500.0))
        self.assertEqual((again["id"], again["duplicate"]), (first["id"], True))
        listing = self.call("GET", "/api/v1/agent/projects", self.token)[1]["projects"]
        letter = self.share["visit"]["filter"]
        collected = next(p for p in listing if p["name"] == M31)["collected"][letter]
        self.assertAlmostEqual(collected, round(big["seconds"] / 3600, 2))

    def test_another_telescopes_share_is_forbidden(self):
        other = self.telescope("Other", person="Other")
        status, _ = self.call("POST", "/api/v1/agent/report", other,
                              {"contributions": [self.record(self.share, 0)]})
        self.assertEqual(status, 403)


class Client(Harness):
    def test_the_example_client_runs_a_night_by_pairing(self):
        lines = []
        reply = client.run(self.base, pairing_code=self.tools.issue_pairing_code("Observer"),
                           night="2026-10-05", log=lines.append)
        self.assertEqual(len(reply["recorded"]), 2)
        self.assertTrue(all(r["accepted"] for r in reply["recorded"]))
        self.assertTrue(any(line.startswith("POST /api/v1/agent/report -> 200") for line in lines))

    def test_the_example_client_enrols_with_a_person_token(self):
        reply = client.run(self.base, person_token=self.tools.sign_in("Observer"),
                           night="2026-10-05", log=lambda line: None)
        self.assertEqual(len(reply["recorded"]), 2)


if __name__ == "__main__":
    unittest.main()
