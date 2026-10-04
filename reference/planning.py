"""Framing rules for the reference server.

These functions turn a rig and a project's requirements into work: which
target and filter, which panel of the project's layout, how long each exposure
and how many frames. They hold no server state, so you can read and test them
alone.

The rules are deliberately simple:

- Field of view per axis: 2·atan(sensor_px · pixel_µm / 1000 / (2 · focal_mm)).
  Sensor pixels are unbinned, so binning does not change the field.
- Sampling: 206.265 · pixel_µm · binning / focal_mm arcseconds per pixel.
- A filter serves an objective when any of its passbands matches any passband
  the objective accepts: the center lies inside the accepted band, and the
  filter's width is between a quarter of the accepted width and the full
  accepted width. A dual-band filter can serve two objectives with one frame.
- The rig works on the objective with the largest remaining deficit that one
  of its filters serves, whose color state and sampling range it fits, and
  whose target rises above 30° at the rig's site latitude, when known.
- The project, not the rig, owns the mosaic. A target may have several grids
  of panels with 15% overlap, one per panel size. Each panel needs the
  objective's full goal. A target that fits a field with 10% to spare gets 1×1.
- Exposure is the objective's minimum, raised to the policy's minimum if needed.
- Estimates assume 20% acquisition overhead and that 80% of frames pass.
"""
from __future__ import annotations

import math

MARGIN = 1.10  # A single panel needs this much room around the target.
OVERLAP = 0.15  # Mosaic overlap between neighbouring panels.
MAX_PLAN_PANELS = 3  # A plan lists at most this many panels, in priority order.
NIGHT_SECONDS = 6 * 3600  # Rig time one plan aims to fill.
OVERHEAD = 1.20  # Rig time per second of exposure.
PASS_RATE = 0.80  # Share of captured frames expected to pass assessment.
SMALL_TARGET = 0.15  # Below this share of the field width, the target is small.
MIN_ALTITUDE = 30.0  # Degrees; a target that never rises this high is skipped.
PLANNING_FIELDS = ("sensor_width_pixels", "sensor_height_pixels", "pixel_size_um",
                   "focal_length_mm", "color_state")


def field_of_view(config: dict) -> tuple[float, float]:
    """Return the rig's (width, height) field in degrees."""
    def axis(pixels: int) -> float:
        size_mm = pixels * config["pixel_size_um"] / 1000
        return math.degrees(2 * math.atan(size_mm / (2 * config["focal_length_mm"])))
    return axis(config["sensor_width_pixels"]), axis(config["sensor_height_pixels"])


def missing(config: dict) -> list[str]:
    """What the rig description lacks for planning."""
    gaps = [field for field in PLANNING_FIELDS if field not in config]
    if not config.get("filters"):
        gaps.append("filters")
    return gaps


def sampling(config: dict) -> float:
    """Return arcseconds per (binned) pixel."""
    return 206.265 * config["pixel_size_um"] * config.get("binning_x", 1) / config["focal_length_mm"]


def band_matches(rig: dict, accepted: dict) -> bool:
    """One rig passband against one accepted passband (protocol section 6).

    The center must lie inside the accepted band. When both widths are known,
    the rig's width must be between a quarter of the accepted width and the
    full accepted width. With no accepted width, centers must be within 5 nm.
    """
    distance = abs(rig["center_nm"] - accepted["center_nm"])
    if "width_nm" not in accepted:
        return distance <= 5
    if distance > accepted["width_nm"] / 2:
        return False
    if "width_nm" not in rig:
        return True
    return accepted["width_nm"] / 4 <= rig["width_nm"] <= accepted["width_nm"]


def serves(passbands: list, accepted: list) -> bool:
    """True when any of a filter's passbands matches any accepted passband."""
    return any(band_matches(rig, want) for rig in passbands for want in accepted)


def max_altitude(latitude: float, dec: float) -> float:
    return 90 - abs(latitude - dec)


def angle_difference(a: float, b: float) -> float:
    """Smallest difference between two camera angles; 180° apart frames the same field."""
    d = abs(a - b) % 180
    return min(d, 180 - d)


def offset(center: dict, east: float, north: float, angle: float) -> dict:
    """Move a sky position by (east, north) degrees in a frame rotated by angle."""
    theta = math.radians(angle)
    de = east * math.cos(theta) + north * math.sin(theta)
    dn = -east * math.sin(theta) + north * math.cos(theta)
    dec = max(-90.0, min(90.0, center["dec_degrees"] + dn))
    cos_dec = max(math.cos(math.radians(center["dec_degrees"])), 1e-6)
    ra = (center["ra_degrees"] + de / cos_dec) % 360
    return {"ra_degrees": round(ra, 6), "dec_degrees": round(dec, 6)}


def footprint_offset(position: dict, region: dict) -> tuple[float, float]:
    """A position's (width-axis, height-axis) offset in degrees from a footprint's
    center, in the footprint's own frame (tangent-plane approximation)."""
    center = region["center"]
    dra = (position["ra_degrees"] - center["ra_degrees"] + 180) % 360 - 180
    east = dra * math.cos(math.radians(center["dec_degrees"]))
    north = position["dec_degrees"] - center["dec_degrees"]
    theta = math.radians(region["position_angle_degrees"])
    return (east * math.cos(theta) - north * math.sin(theta),
            east * math.sin(theta) + north * math.cos(theta))


def inside(position: dict, region: dict) -> bool:
    """True when a position lies in a footprint."""
    u, v = footprint_offset(position, region)
    return abs(u) <= region["width_degrees"] / 2 + 1e-6 and abs(v) <= region["height_degrees"] / 2 + 1e-6


def coverage(center: dict, panel: dict) -> float:
    """Share of a panel covered by a same-size frame centered at center."""
    u, v = footprint_offset(center, panel)
    return max(0.0, 1 - abs(u) / panel["width_degrees"]) * max(0.0, 1 - abs(v) / panel["height_degrees"])


def layout(target: dict, fov: tuple[float, float]) -> tuple[int, int]:
    """Columns and rows of field-sized panels, with 15% overlap, that cover the target."""
    def needed(size: float, field: float) -> int:
        if field >= size * MARGIN:
            return 1
        return math.ceil((size * MARGIN - field) / (field * (1 - OVERLAP))) + 1
    return (needed(target["width_degrees"], fov[0]), needed(target["height_degrees"], fov[1]))


def panel_centers(target: dict, fov: tuple[float, float], columns: int, rows: int) -> list[dict]:
    step_x, step_y = fov[0] * (1 - OVERLAP), fov[1] * (1 - OVERLAP)
    angle = target["position_angle_degrees"]
    return [offset(target["center"], (c - (columns - 1) / 2) * step_x,
                   (r - (rows - 1) / 2) * step_y, angle)
            for r in range(rows) for c in range(columns)]


def candidates(config: dict, requirements: dict, deficits: dict):
    """Rank the objectives this rig can work on. Returns (choices, reason_codes).

    choices is a list of (deficit_seconds, objective, filter), largest first.
    deficits maps objective ID to remaining seconds of accepted integration.
    """
    groups = {g["id"]: g for g in requirements["processing_groups"]}
    targets = {t["id"]: t for t in requirements["targets"]}
    rig_sampling = sampling(config)
    latitude = config.get("site", {}).get("latitude_degrees")
    reasons, choices = set(), []
    for objective in requirements["objectives"]:
        if deficits[objective["id"]] <= 0:
            reasons.add("goals_met")
            continue
        matched = [f for f in config["filters"] if serves(f["bandpasses"], objective["bandpasses"])]
        if not matched:
            reasons.add("no_matching_filter")
            continue
        group = groups[objective["processing_group_id"]]
        if group["color_state"] != config["color_state"]:
            reasons.add("color_state_mismatch")
            continue
        limits = group["sampling_arcsec_per_pixel"]
        if not limits["min"] <= rig_sampling <= limits["max"]:
            reasons.add("sampling_out_of_range")
            continue
        dec = targets[objective["target_id"]]["footprint"]["center"]["dec_degrees"]
        if latitude is not None and max_altitude(latitude, dec) < MIN_ALTITUDE:
            reasons.add("target_too_low")
            continue
        # Prefer the widest matching filter: luminance over H-alpha for a luminance goal.
        best = max(matched, key=lambda f: max(b.get("width_nm", 0) for b in f["bandpasses"]))
        choices.append((deficits[objective["id"]], objective, best))
    choices.sort(key=lambda choice: -choice[0])
    if choices:
        return choices, []
    # Report the most useful reason: what would the user need to change?
    for code in ("sampling_out_of_range", "color_state_mismatch", "target_too_low",
                 "no_matching_filter", "goals_met"):
        if code in reasons:
            return [], [code]
    return [], ["goals_met"]
