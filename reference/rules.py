"""The rules of AstroCollab 0.2, as plain functions: filter names, tiling, the
depth map, dealing a night, and judging a report.

Written from spec/protocol.md. Nothing here touches HTTP or storage, so each
rule can be read, and tested, on its own.
"""
from __future__ import annotations

import math
import re
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Filter names
# ---------------------------------------------------------------------------

#: Spellings of each filter, compared after lowering and removing spaces,
#: hyphens, underscores and slashes.
_SPELLINGS = {
    "L": ("l", "lum", "luminance", "clear", "uvircut", "uvir", "ircut", "none"),
    "R": ("r", "red"),
    "G": ("g", "green"),
    "B": ("b", "blue"),
    "H": ("h", "ha", "halpha", "hα", "hydrogenalpha"),
    "O": ("o", "oiii", "o3", "oxygen", "oxygeniii"),
    "S": ("s", "sii", "s2", "sulphur", "sulfur", "sulphurii", "sulfurii"),
}
_LETTER = {spelling: letter for letter, spellings in _SPELLINGS.items() for spelling in spellings}
_TRAILING_BANDPASS = re.compile(r"[\s(\[-]*\d+(?:\.\d+)?\s*nm[)\]]*\s*$", re.IGNORECASE)

#: Names a project uses for a one-shot colour camera's channel.
COLOUR_NAMES = {"rgb", "osc", "colour", "color"}
#: The red narrowband lines, which shoot through moonlight.
MOON_TOLERANT = {"H", "S"}
#: Lit fraction times the fraction of dark hours the Moon is up, at or above
#: which a night counts as bright.
BRIGHT_MOON = 0.2


def filter_letter(name: Any) -> str:
    """The one spelling of a filter: L R G B H O or S, or the name as written."""
    text = " ".join(str(name if name is not None else "").split())
    if not text:
        return ""
    bare = _TRAILING_BANDPASS.sub("", text).strip()
    key = re.sub(r"[\s_\-/]+", "", bare).lower()
    return _LETTER.get(key, text)


def by_letter(table: dict[str, Any] | None) -> dict[str, Any]:
    """A table keyed by filter name, re-keyed by letter. The first spelling wins."""
    out: dict[str, Any] = {}
    for name, value in (table or {}).items():
        letter = filter_letter(name)
        if letter and letter not in out:
            out[letter] = value
    return out


# ---------------------------------------------------------------------------
# Numbers as they arrive
# ---------------------------------------------------------------------------

def number(value: Any) -> float | None:
    """A number, or None when it is missing, blank or not a number."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


# ---------------------------------------------------------------------------
# Requirements
# ---------------------------------------------------------------------------

REQUIREMENT_DEFAULTS: dict[str, Any] = {
    "minFocalLength": None, "maxFocalLength": None, "minScale": None, "maxScale": None,
    "acceptColour": True, "colourMaxMoon": None, "maxHfr": None, "maxGuideRms": None,
    "minExposure": None, "maxExposure": None, "filters": {}, "maxMoonIllumination": None,
    "minMoonSeparation": None, "minAltitude": None, "requireCalibrated": False,
    "minFramesPerVisit": 10,
}


def requirements(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Every requirement, with defaults filled in and filters keyed by letter."""
    raw = raw or {}
    out = dict(REQUIREMENT_DEFAULTS)
    for key in REQUIREMENT_DEFAULTS:
        if key in raw:
            out[key] = raw[key]
    out["filters"] = {letter: number(limit) for letter, limit in by_letter(raw.get("filters")).items()}
    out["acceptColour"] = bool(out["acceptColour"])
    out["requireCalibrated"] = bool(out["requireCalibrated"])
    out["minFramesPerVisit"] = max(1, int(number(out["minFramesPerVisit"]) or 10))
    for key, value in list(out.items()):
        if key not in ("filters", "acceptColour", "requireCalibrated", "minFramesPerVisit"):
            out[key] = number(value)
    return out


# ---------------------------------------------------------------------------
# The rig
# ---------------------------------------------------------------------------

def scale(profile: dict[str, Any]) -> float | None:
    """Arcseconds per pixel, from focal length, pixel size and binning."""
    focal, pixel = number(profile.get("focalLength")), number(profile.get("pixelSize"))
    if not focal or not pixel:
        return None
    binning = max(1, int(number(profile.get("binning")) or 1))
    return 206.265 * pixel * binning / focal


def field(profile: dict[str, Any]) -> tuple[float, float] | None:
    """Degrees of sky the sensor spans, width by height. Binning does not change it."""
    focal, pixel = number(profile.get("focalLength")), number(profile.get("pixelSize"))
    width, height = number(profile.get("sensorWidth")), number(profile.get("sensorHeight"))
    if not (focal and pixel and width and height):
        return None
    per_pixel = 206.265 * pixel / focal / 3600.0
    return per_pixel * width, per_pixel * height


def rig_filters(profile: dict[str, Any]) -> dict[str, float | None]:
    """The rig's filters by letter, each with its bandpass or None."""
    return {letter: number(width) for letter, width in by_letter(profile.get("filters")).items()}


# ---------------------------------------------------------------------------
# Sky geometry
# ---------------------------------------------------------------------------

def frames_needed(span: float, frame: float, overlap: float = 0.1) -> int:
    """How many frames, overlapping by `overlap`, it takes to cover `span`."""
    if frame <= 0 or span <= frame:
        return 1
    return 1 + math.ceil((span - frame) / (frame * (1.0 - overlap)) - 1e-9)


def _offsets(count: int, span: float, frame: float) -> list[float]:
    """Frame centres spread evenly so the outer frames reach the span's edges."""
    if count == 1:
        return [0.0]
    pitch = (span - frame) / (count - 1)
    return [-(span - frame) / 2.0 + pitch * index for index in range(count)]


def _along_axes(ra: float, dec: float, x: float, y: float, angle: float) -> tuple[float, float]:
    """The sky position `x` degrees along a camera's width axis and `y` along its
    height axis from (ra, dec), for a camera at position angle `angle`.

    The height axis points `angle` degrees east of north; the width axis points
    east when the angle is zero. Offsets are on a tangent plane, which is
    accurate enough over a few degrees.
    """
    theta = math.radians(angle)
    east = x * math.cos(theta) + y * math.sin(theta)
    north = -x * math.sin(theta) + y * math.cos(theta)
    cell_dec = dec + north
    cosine = max(math.cos(math.radians(cell_dec)), 1e-6)
    return (ra + east / cosine) % 360.0, cell_dec


def shooting_angle(region: dict[str, Any], profile: dict[str, Any]) -> float:
    """A fixed camera shoots at its own angle; a rig with a rotator at the project's."""
    fixed = number(profile.get("rotation"))
    return (fixed if fixed is not None else number(region.get("rotation")) or 0.0) % 360.0


def tile(region: dict[str, Any], kind: str, profile: dict[str, Any],
         overlap: float = 0.1) -> list[dict[str, Any]]:
    """A rig's own tiling of a project's region.

    Each cell is one frame of this rig's camera, at the angle it will shoot.
    A `single` project is one cell centred on the object. A `mosaic` is a grid
    laid along the camera's axes, big enough to cover the north-up region as
    the camera sees it, with neighbours overlapping by `overlap`.
    """
    size = field(profile)
    if size is None:
        return []
    width, height = size
    angle = shooting_angle(region, profile)
    ra, dec = float(region["ra"]), float(region["dec"])
    if kind == "single":
        return [{"ra": ra % 360.0, "dec": dec, "width": width, "height": height,
                 "rotation": angle, "row": 0, "column": 0}]
    theta = math.radians(angle)
    cos_t, sin_t = abs(math.cos(theta)), abs(math.sin(theta))
    region_w, region_h = abs(float(region["width"])), abs(float(region["height"]))
    # The region's extent measured along the camera's own axes.
    span_x = region_w * cos_t + region_h * sin_t
    span_y = region_w * sin_t + region_h * cos_t
    columns = frames_needed(span_x, width, overlap)
    rows = frames_needed(span_y, height, overlap)
    cells = []
    for row, y in enumerate(_offsets(rows, span_y, height)):
        for column, x in enumerate(_offsets(columns, span_x, width)):
            cell_ra, cell_dec = _along_axes(ra, dec, x, y, angle)
            cells.append({"ra": cell_ra, "dec": cell_dec, "width": width, "height": height,
                          "rotation": angle, "row": row, "column": column})
    return cells


def contains(frame: dict[str, Any], ra: float, dec: float) -> bool:
    """Whether a point lies inside a (possibly turned) rectangle of sky."""
    cosine = max(math.cos(math.radians((dec + float(frame["dec"])) / 2.0)), 1e-6)
    east = (((ra - float(frame["ra"])) + 180.0) % 360.0 - 180.0) * cosine
    north = dec - float(frame["dec"])
    theta = math.radians(number(frame.get("rotation")) or 0.0)
    x = east * math.cos(theta) - north * math.sin(theta)
    y = east * math.sin(theta) + north * math.cos(theta)
    return abs(x) <= float(frame["width"]) / 2.0 + 1e-9 and abs(y) <= float(frame["height"]) / 2.0 + 1e-9


#: Sample points per cell side when measuring depth and overlap.
SAMPLES = 5


def sample_points(cell: dict[str, Any]) -> list[tuple[float, float]]:
    """Points spread evenly over a cell, for measuring what lands on it."""
    width, height = float(cell["width"]), float(cell["height"])
    angle = number(cell.get("rotation")) or 0.0
    points = []
    for i in range(SAMPLES):
        for j in range(SAMPLES):
            x = (i + 0.5) / SAMPLES * width - width / 2.0
            y = (j + 0.5) / SAMPLES * height - height / 2.0
            points.append(_along_axes(float(cell["ra"]), float(cell["dec"]), x, y, angle))
    return points


def covered_fraction(cell: dict[str, Any], frames: Iterable[dict[str, Any]]) -> float:
    """The share of a cell's sample points that any of `frames` covers."""
    frames = list(frames)
    if not frames:
        return 0.0
    points = sample_points(cell)
    hit = sum(1 for ra, dec in points if any(contains(frame, ra, dec) for frame in frames))
    return hit / len(points)


def depth(cells: list[dict[str, Any]], records: list[dict[str, Any]],
          agent: str | None = None) -> list[dict[str, float]]:
    """Seconds of accepted integration on each cell, per filter.

    Depth is integration time at a point on the sky. A cell's depth is the mean,
    over its sample points, of the seconds of every accepted footprint covering
    that point. With `agent`, only that telescope's records count.
    """
    result: list[dict[str, float]] = []
    usable = [r for r in records if r["accepted"] and r["payload"].get("footprint")
              and (agent is None or r["agent"] == agent)]
    for cell in cells:
        points = sample_points(cell)
        seconds: dict[str, float] = {}
        for record in usable:
            footprint = record["payload"]["footprint"]
            hit = sum(1 for ra, dec in points if contains(footprint, ra, dec))
            if hit:
                letter = record["filter"]
                seconds[letter] = seconds.get(letter, 0.0) + float(record["seconds"]) * hit / len(points)
        result.append(seconds)
    return result


# ---------------------------------------------------------------------------
# Can this rig help?
# ---------------------------------------------------------------------------

def usable_filters(profile: dict[str, Any], wants: dict[str, Any],
                   goals: dict[str, float]) -> tuple[list[str], list[str]]:
    """The project's filters this rig can shoot, and why it cannot shoot the rest."""
    wanted = list(wants["filters"]) or list(goals) or ["L"]
    carried = rig_filters(profile)
    # A colour camera with nothing in front of it shoots its colour channel only.
    colour_only = bool(profile.get("colour")) and not carried
    usable, missing = [], []
    for letter in wanted:
        limit = wants["filters"].get(letter)
        if colour_only:
            if letter.lower() in COLOUR_NAMES:
                usable.append(letter)
            else:
                missing.append(f"a colour camera cannot shoot {letter}")
        elif letter not in carried:
            missing.append(f"no {letter}")
        elif limit is not None and carried[letter] is None:
            missing.append(f"{letter} bandpass not known, project wants {limit:g} nm or narrower")
        elif limit is not None and carried[letter] > limit:
            missing.append(f"{letter} is {carried[letter]:g} nm, project wants {limit:g} nm or narrower")
        else:
            usable.append(letter)
    return usable, missing


def compatibility(profile: dict[str, Any], wants: dict[str, Any],
                  goals: dict[str, float]) -> dict[str, Any]:
    """Whether a rig can help a project, check by check. `ok` None means unknown."""
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool | None, detail: str) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail})

    focal = number(profile.get("focalLength"))
    low, high = wants["minFocalLength"], wants["maxFocalLength"]
    if low is not None or high is not None:
        if focal is None:
            check("focal length", None, "focal length not known")
        elif low is not None and focal < low:
            check("focal length", False, f"{focal:g} mm is shorter than the {low:g} mm the project wants")
        elif high is not None and focal > high:
            check("focal length", False, f"{focal:g} mm is longer than the {high:g} mm the project wants")
        else:
            check("focal length", True, f"{focal:g} mm")

    rig_scale = scale(profile)
    low, high = wants["minScale"], wants["maxScale"]
    if rig_scale is None:
        check("scale", None, "image scale not known: give focal length and pixel size")
    elif low is not None and rig_scale < low:
        check("scale", False, f'{rig_scale:.2f}"/px is finer than the {low:g}"/px the project wants')
    elif high is not None and rig_scale > high:
        check("scale", False, f'{rig_scale:.2f}"/px is coarser than the {high:g}"/px the project wants')
    else:
        check("scale", True, f'{rig_scale:.2f}"/px')

    if profile.get("colour"):
        if not wants["acceptColour"]:
            check("camera", False, "a one-shot colour camera; this project wants mono cameras")
        else:
            check("camera", True, "one-shot colour")

    # A rig must carry every filter the project wants, within its bandpass limits.
    usable, missing = usable_filters(profile, wants, goals)
    if not missing:
        check("filters", True, "can shoot " + ", ".join(usable))
    else:
        check("filters", False, "; ".join(missing)
              + (" (can shoot " + ", ".join(usable) + ")" if usable else ""))

    exposures = by_letter(profile.get("exposures"))
    low, high = wants["minExposure"], wants["maxExposure"]
    if (low is not None or high is not None) and exposures:
        wrong = []
        for letter in usable:
            seconds = number(exposures.get(letter))
            if seconds is None:
                continue
            if low is not None and seconds < low:
                wrong.append(f"{letter} at {seconds:g}s is shorter than the {low:g}s minimum")
            elif high is not None and seconds > high:
                wrong.append(f"{letter} at {seconds:g}s is longer than the {high:g}s maximum")
        check("sub length", not wrong, "; ".join(wrong) if wrong else "sub lengths suit the project")

    for name, have, limit in (("stars", number(profile.get("typicalHfr")), wants["maxHfr"]),
                              ("guiding", number(profile.get("typicalGuideRms")), wants["maxGuideRms"])):
        if limit is None:
            continue
        if have is None:
            check(name, None, f'not measured; the project wants {limit:g}" or better')
        else:
            check(name, have <= limit, f'usually {have:.2f}", the project wants {limit:g}" or better')

    failed = [c for c in checks if c["ok"] is False]
    unknown = [c for c in checks if c["ok"] is None]
    if failed:
        summary = "cannot contribute: " + "; ".join(c["detail"] for c in failed)
    elif unknown:
        summary = "can contribute, but not everything could be checked"
    else:
        summary = "can contribute"
    return {"ok": not failed, "certain": not failed and not unknown, "checks": checks,
            "summary": summary}


# ---------------------------------------------------------------------------
# Judging a report
# ---------------------------------------------------------------------------

def judge(entry: dict[str, Any], wants: dict[str, Any] | None) -> dict[str, Any]:
    """Accept or reject one night's work on one filter and panel, and say why.

    Every rule the project sets is checked against what the record gives. A
    star-size, guiding or image-scale rule whose measurement is missing goes under
    `unverified`: a missing measurement is not a pass. Other rules with nothing to
    check against are skipped.
    """
    if wants is None:
        reasons = ["not a project on this server"]
        return {"accepted": False, "reasons": reasons, "unverified": [], "summary": reasons[0]}
    reasons: list[str] = []
    unverified: list[str] = []

    def limit(have: float | None, low: float | None, high: float | None,
              what: str, unit: str, missing: str | None = None) -> None:
        if low is None and high is None:
            return
        if have is None:
            if missing:
                unverified.append(missing)
            return
        if low is not None and have < low:
            reasons.append(f"{what} was {have:g}{unit}, the project wants at least {low:g}{unit}")
        if high is not None and have > high:
            reasons.append(f"{what} was {have:g}{unit}, the project wants at most {high:g}{unit}")

    letter = filter_letter(entry.get("filterName"))
    colour = bool(entry.get("colour"))
    moon = number(entry.get("moonIllumination"))
    if wants["filters"] and letter not in wants["filters"]:
        reasons.append(f"{letter or 'no filter'} is not a filter this project wants")
    elif letter in wants["filters"] and wants["filters"][letter] is not None:
        limit(number(entry.get("bandpass")), None, wants["filters"][letter],
              f"the {letter} bandpass", " nm")
    if colour and not wants["acceptColour"]:
        reasons.append("shot on a one-shot colour camera; this project wants mono cameras")
    if colour and wants["colourMaxMoon"] is not None:
        limit(moon, None, wants["colourMaxMoon"], "for a colour camera, the Moon", " lit")
    limit(number(entry.get("focalLength")), wants["minFocalLength"], wants["maxFocalLength"],
          "the focal length", " mm")
    limit(number(entry.get("scale")), wants["minScale"], wants["maxScale"],
          "the image scale", '"/px', "the image scale was not given; was it plate solved?")
    limit(number(entry.get("hfr")), None, wants["maxHfr"],
          "mean star size", '"', "star size was not measured")
    limit(number(entry.get("guideRms")), None, wants["maxGuideRms"],
          "mean guiding error", '"', "guiding was not measured")
    exposure = number(entry.get("exposure")) or None
    limit(exposure, wants["minExposure"], wants["maxExposure"],
          "the sub length", "s")
    limit(moon, None, wants["maxMoonIllumination"], "the Moon", " lit")
    limit(number(entry.get("moonSeparation")), wants["minMoonSeparation"], None,
          "the distance from the Moon", "°")
    if wants["requireCalibrated"] and not entry.get("calibrated"):
        reasons.append("the frames are not calibrated")

    if reasons:
        summary = "; ".join(reasons)
    elif unverified:
        summary = "accepted, but " + "; ".join(unverified)
    else:
        summary = "accepted"
    return {"accepted": not reasons, "reasons": reasons, "unverified": unverified, "summary": summary}


# ---------------------------------------------------------------------------
# Dealing a night
# ---------------------------------------------------------------------------

#: Slew, centring and settling per visit, in seconds.
VISIT_OVERHEAD = 90.0
#: Hours a night for a rig that has not said.
DEFAULT_HOURS = 6.0


def moon_badness(moon: float | None, moon_up: float | None) -> float | None:
    """How much the Moon spoils a night: lit fraction times the fraction it is up."""
    if moon is None or moon_up is None:
        return None
    return max(0.0, min(1.0, moon)) * max(0.0, min(1.0, moon_up))


def remaining(cell_depth: dict[str, float], letter: str, hours: float) -> float:
    """Seconds a cell still wants in one filter."""
    return max(0.0, hours * 3600.0 - cell_depth.get(letter, 0.0))


def choose_filter(filters: list[dict[str, Any]], depths: list[dict[str, float]],
                  committed: dict[str, float], badness: float | None) -> dict[str, Any]:
    """Tonight's one filter for a rig on a mosaic.

    The Moon first: a bright night goes to H or S, a dark one to the rest. Then
    the filter with the most depth still wanted across the rig's cells, once
    what other rigs are putting in tonight is taken off. Ties go to the
    project's order.
    """
    wanted = {f["filter"]: sum(remaining(d, f["filter"], f["hours"]) for d in depths)
              - committed.get(f["filter"], 0.0) for f in filters}
    candidates = [f for f in filters if wanted[f["filter"]] > 0] or list(filters)
    if badness is not None:
        bright = badness >= BRIGHT_MOON
        preferred = [f for f in candidates if (f["filter"] in MOON_TOLERANT) == bright]
        candidates = preferred or candidates
    best = candidates[0]
    for option in candidates[1:]:
        if wanted[option["filter"]] > wanted[best["filter"]] + 1e-6:
            best = option
    return best


def visit_plan(exposure: float, hours_wanted: float, budget: float, open_cells: int,
               min_frames: int) -> tuple[int, int, float]:
    """How many panels to visit tonight, frames on each, and seconds on each.

    As many visits as the night holds, each at least `min_frames` long and no
    longer than a panel's full depth. Always at least one visit.
    """
    floor_seconds = min_frames * exposure
    visits = int(budget // (floor_seconds + VISIT_OVERHEAD)) if floor_seconds > 0 else 1
    visits = max(1, min(open_cells, visits))
    per_visit = budget / visits - VISIT_OVERHEAD
    per_visit = max(floor_seconds, min(hours_wanted * 3600.0, per_visit))
    frames = max(min_frames, int(per_visit // exposure)) if exposure > 0 else min_frames
    return visits, frames, frames * exposure


def order_cells(cells: list[dict[str, Any]], depths: list[dict[str, float]],
                mine: list[dict[str, float]], others_tonight: list[dict[str, Any]],
                letters: list[str], hours: dict[str, float]) -> list[int]:
    """Cells in the order a rig should take them tonight.

    Open cells first; among them: least covered by other rigs tonight, then least
    shot by this rig, then most depth still wanted. When every cell has its full
    depth, all of them are in play: more data is still welcome.
    """
    def wanted(index: int) -> float:
        return sum(remaining(depths[index], letter, hours[letter]) for letter in letters)

    open_cells = [i for i in range(len(cells)) if wanted(i) > 0] or list(range(len(cells)))
    claimed = {i: covered_fraction(cells[i], others_tonight) for i in open_cells}
    # Where this rig has been in any filter: no patch should be one camera's alone.
    own = {i: sum(mine[i].values()) for i in open_cells}
    return sorted(open_cells, key=lambda i: (round(claimed[i], 3), round(own[i], 1),
                                             -round(wanted(i), 1), i))
