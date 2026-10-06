// The protocol's types, as Starfront sends them. Field names are camelCase on the wire.
// Every object is open: a receiver keeps or ignores fields it does not know, so a newer
// program is never refused for sending one field more.
using Http;
using OpenAPI;

namespace AstroCollab;

// ---------------------------------------------------------------------------
// Sky
// ---------------------------------------------------------------------------

/**
 * A rectangle of sky, north-up. Degrees throughout: `ra` is degrees, not hours. `width`
 * and `height` are angles on the sky, not degrees of the RA coordinate.
 */
model Region {
  /** Right ascension of the centre, degrees. */
  ra: float64;

  /** Declination of the centre, degrees. */
  `dec`: float64;

  /** Width on the sky, degrees. */
  width: float64;

  /** Height on the sky, degrees. */
  height: float64;

  /** The position angle cameras should shoot at, degrees, so panels stack the same way up. */
  rotation?: float64 = 0;
}

/** One cell of a telescope's own tiling of a project, and where it sits in that grid. */
model Cell extends Region {
  row: int32;
  column: int32;
}

// ---------------------------------------------------------------------------
// Telescopes
// ---------------------------------------------------------------------------

/**
 * What a telescope is and what it achieves, sent with every hello. Numbers the program does
 * not know are null. The server uses this to say whether the rig can help a project and to
 * cut its cells and deal its nights.
 */
model RigProfile {
  name?: string;

  /** Millimetres. */
  focalLength?: float64 | null;

  /** Microns, unbinned. */
  pixelSize?: float64 | null;

  /** Pixels, unbinned. */
  sensorWidth?: int32 | null;

  /** Pixels, unbinned. */
  sensorHeight?: int32 | null;

  binning?: int32 = 1;

  /** Filter name to bandpass in nanometres, or null where the bandpass is not known. */
  filters?: Record<float64 | null>;

  /** A one-shot colour camera: broadband RGB, whatever is in front of it. */
  colour?: boolean = false;

  /**
   * The camera's position angle in degrees when nothing can turn it. Null when a rotator can
   * set whatever angle a project asks for.
   */
  rotation?: float64 | null;

  /** Typical star size on this rig, arcseconds. */
  typicalHfr?: float64 | null;

  /** Typical guiding error on this rig, arcseconds. */
  typicalGuideRms?: float64 | null;

  /**
   * The sub length this rig shoots each filter at, seconds: what its darks are built for.
   * Work is dealt at these lengths so every frame can be calibrated.
   */
  exposures?: Record<float64>;

  /** Hours a night the rig gives the collaboration. Null means as long as targets are up. */
  hoursPerNight?: float64 | null;

  /** Start of the rig's window, local clock time such as "21:00". */
  windowFrom?: string;

  /** End of the rig's window, local clock time. */
  windowTo?: string;

  /** Arcseconds per pixel, worked out by the program from the optics. */
  scale?: float64 | null;

  /** Field of view, [width, height] in degrees, worked out by the program. */
  field?: float64[] | null;
}

/** Where a telescope points and what it is doing, as much as it chooses to say. */
model Presence {
  /** Hours. */
  ra?: float64 | null;

  /** Degrees. */
  `dec`?: float64 | null;

  /** Such as "imaging", "slewing" or "idle". */
  state?: string;

  /** The target's name. */
  target?: string;

  /** The project being shot, if any. */
  project?: string;

  /** What to call the telescope on the group's chart, such as its equipment profile's name. */
  telescope?: string;
}

/** One telescope as the group sees it. */
model TelescopePresence {
  id: Id;

  /** What the telescope says to call it, or the name it was enrolled under. */
  name: string;

  /** The name it was enrolled under. */
  enrolledAs?: string;

  owner: string;

  /** Hours. */
  ra: float64 | null;

  /** Degrees. */
  `dec`: float64 | null;

  state: string;
  target: string;
  project: string;

  /** Seconds since it last checked in. */
  ageSeconds: int32;

  /** Checked in within `onlineSeconds`. */
  online: boolean;
}

/** An enrolled telescope. The token is never listed. */
model Telescope {
  id: Id;
  name: string;

  /** The owner's display name. */
  owner?: string;

  owner_id?: string;
  created: UnixTime;

  /** Last check-in, or 0 if never. */
  seen?: UnixTime;

  profile?: RigProfile;
  presence?: Presence | null;
}

// ---------------------------------------------------------------------------
// Projects
// ---------------------------------------------------------------------------

/**
 * What a project will take. Units mean the same on every rig: star size and guiding are in
 * arcseconds, never pixels. Null means no limit.
 */
model Requirements {
  /** Millimetres. */
  minFocalLength?: float64 | null;

  /** Millimetres. */
  maxFocalLength?: float64 | null;

  /** Arcseconds per pixel. */
  minScale?: float64 | null;

  /** Arcseconds per pixel. */
  maxScale?: float64 | null;

  /** Whether one-shot colour cameras may take part. */
  acceptColour?: boolean = true;

  /** Moon illumination (0–1) above which a colour camera's night is refused. */
  colourMaxMoon?: float64 | null;

  /** Mean star size over a night, arcseconds. */
  maxHfr?: float64 | null;

  /** Mean guiding error over a night, arcseconds. */
  maxGuideRms?: float64 | null;

  /** Shortest sub, seconds. */
  minExposure?: float64 | null;

  /** Longest sub, seconds. */
  maxExposure?: float64 | null;

  /** Filters wanted, each to the widest bandpass accepted in nanometres, or null for any. */
  filters?: Record<float64 | null>;

  /** Moon illumination, 0–1. */
  maxMoonIllumination?: float64 | null;

  /** Degrees from the Moon. */
  minMoonSeparation?: float64 | null;

  /** Lowest altitude to shoot at, degrees. */
  minAltitude?: float64 | null;

  /** Frames must be calibrated. */
  requireCalibrated?: boolean = false;

  /** The fewest frames a visit to one panel is worth: a rig stacks its own frames. */
  minFramesPerVisit?: int32 = 10;
}

/** One check of a rig against a project. `ok` is null when it cannot be told. */
model CompatibilityCheck {
  check: string;
  ok: boolean | null;
  detail: string;
}

/** Whether a rig meets a project's requirements, check by check. */
model Compatibility {
  ok: boolean;

  /** True when every check could be made. */
  certain: boolean;

  checks: CompatibilityCheck[];
  summary: string;
}

/** How far one filter has got across the whole region, everybody's accepted data together. */
model FilterProgress {
  /** Depth wanted at every point, hours. */
  goalHours: float64;

  /** Share of the region at the goal depth (within a tenth of it), 0–1. */
  atGoal: float64;

  /** The region's depth against the goal, each point capped at the goal, 0–1. */
  average: float64;

  /** The least-covered point's depth against the goal, 0–1. */
  thinnest: float64;
}

/** An open project, as a telescope browsing for one sees it. */
model OpenProject {
  id: Id;
  name: string;
  coordinator: string;

  /** Who started it. */
  ownerId: string;

  region: Region;

  /** `single`: one object everybody frames. `mosaic`: a region each rig tiles with its own camera. */
  kind: "single" | "mosaic";

  requirements: Requirements;

  /** Depth wanted at every point of the region, per filter, in hours. */
  goals: Record<float64>;

  notes: string;

  /** Whether this telescope can help, from the profile it last sent. */
  compatibility: Compatibility;

  /** This telescope already holds a share. */
  joined: boolean;

  /** Accepted hours so far, per filter. */
  collected: Record<float64>;

  participants: int32;
  participantsOnline: int32;
  participantNames: string[];

  /** Per filter, how far the region has got. */
  progress?: Record<FilterProgress>;
}

/** One cell of a depth map's fine grid. */
model DepthCell {
  ra: float64;
  `dec`: float64;
  width: float64;
  height: float64;
  row: int32;
  column: int32;
}

/**
 * A project's depth map: the region cut into a fine north-up grid, with everybody's accepted
 * seconds on each cell, per filter. Each accepted record credits the cells its footprint
 * covers, in proportion to how much of each it covers.
 */
model DepthMap {
  project: Id;
  name: string;
  region: Region;
  columns: int32;
  rows: int32;
  cells: DepthCell[];

  /** Per filter, seconds on each cell, in the order of `cells`. */
  seconds: Record<float64[]>;

  /** Hours wanted per filter at every point. */
  goals: Record<float64>;

  progress: Record<FilterProgress>;
}

// ---------------------------------------------------------------------------
// Tasks
// ---------------------------------------------------------------------------

/** One filter's part of a share: sub length, and depth wanted on each panel. */
model FilterTask {
  filter: string;

  /** Sub length, seconds. */
  exposure: float64;

  /** Depth wanted on each panel over all nights, hours. */
  hours: float64;
}

/** Tonight's slice of a share. */
model Visit {
  /** Seconds to spend on each panel tonight. */
  seconds?: float64 | null;

  /** Frames per filter on each panel tonight. */
  frames?: Record<int32>;

  /** Tonight's one filter, on a mosaic. */
  filter?: string | null;

  /** How much the Moon spoils the night the list was made for, 0–1. */
  moon?: float64 | null;
}

/**
 * A telescope's share of a project: the whole region tiled with its own camera, and which
 * of those cells are its to shoot tonight. A change to the share bumps `version`.
 */
model Task {
  id: Id;
  project: Id;
  projectName?: string;

  /** The telescope this share belongs to. */
  agent?: Id;

  /** The whole project's sky. */
  region: Region;

  filters?: FilterTask[];

  /**
   * `accepted` once the rig has joined or accepted it. `offered` when a coordinator pushed it
   * and nobody has accepted yet. `superseded` when replaced by a newer share of the same project.
   */
  state?: "offered" | "accepted" | "declined" | "complete" | "superseded" = "offered";

  /** Rises whenever the share changes; a rig with this version has nothing new to do. */
  version?: int32 = 1;

  issued?: UnixTime;
  note?: string;

  /** Total seconds the share is worth over all nights. */
  seconds?: float64 | null;

  /** This telescope's tiling of the region. */
  cells?: Cell[];

  /** Indexes into `cells` to shoot tonight, in order. */
  share?: int32[];

  kind?: "single" | "mosaic" = "mosaic";
  visit?: Visit | null;

  /** The night the share was dealt for. */
  assignedNight?: NightName | null;

  assignedAt?: UnixTime | null;

  /** Hours of the night the share was dealt for. */
  dealtHours?: float64 | null;

  /** The camera angle the cells were cut for. */
  tiledRotation?: float64 | null;
}

// ---------------------------------------------------------------------------
// Reports
// ---------------------------------------------------------------------------

/**
 * One night's work on one filter and one panel, as measured rather than as planned. The
 * footprint is the solved one: where the telescope really pointed.
 */
model Contribution {
  project?: Id | "";
  task?: Id | "";
  night?: NightName;
  filterName?: string;

  /** Which panel of the rig's tiling, as the rig numbers them; empty for a single frame. */
  panel?: string;

  frames?: int32 = 0;

  /** Total exposure, seconds. */
  seconds?: float64 = 0;

  /** Sub length, seconds. */
  exposure?: float64 = 0;

  footprint?: Region | null;

  /** Arcseconds per pixel. */
  scale?: float64 | null;

  /** Millimetres. */
  focalLength?: float64 | null;

  /** Mean star size over the night, arcseconds. */
  hfr?: float64 | null;

  /** Mean guiding error over the night, arcseconds. */
  guideRms?: float64 | null;

  /** 0–1. */
  moonIllumination?: float64 | null;

  /** Degrees. */
  moonSeparation?: float64 | null;

  calibrated?: boolean = false;

  /** Bandpass of the filter used, nanometres. */
  bandpass?: float64 | null;

  colour?: boolean = false;
}

/**
 * The server's judgement of one contribution. Advisory: a project's coordinator can overrule
 * it. `unverified` lists rules that could not be checked because a measurement was missing.
 */
model Verdict {
  accepted: boolean;
  reasons?: string[];
  unverified?: string[];
  summary?: string;
  overriddenBy?: string | null;
  overrideReason?: string | null;
}

/** What happened to one contribution in a report. */
model Recorded {
  id: Id;
  accepted: boolean;

  /** Already reported; the larger figure stands. */
  duplicate: boolean;

  verdict: Verdict;
}
