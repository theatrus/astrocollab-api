using Http;
using OpenAPI;

namespace AstroCollab;

model Position {
  @minValue(0) @maxValueExclusive(360) ra_degrees: numeric;
  @minValue(-90) @maxValue(90) dec_degrees: numeric;
}

model Footprint {
  center: Position;
  @maxValue(30) @minValueExclusive(0) width_degrees: numeric;
  @maxValue(30) @minValueExclusive(0) height_degrees: numeric;
  @minValue(0) @maxValueExclusive(360) position_angle_degrees: numeric;
}

model Target {
  id: Uuid;
  @minLength(1) @maxLength(200) name: string;
  footprint: Footprint;
}

/** A passband: a named center wavelength, with its width when known. */
model Bandpass {
  @minLength(1) @maxLength(80) name: string;
  @minValue(100) @maxValue(30000) center_nm: numeric;

  /** Full width at half maximum. In an objective, the widest band accepted. */
  @maxValue(30000) @minValueExclusive(0) width_nm?: numeric;
}

/** Inclusive range. Server MUST enforce min <= max. */
model Range {
  @minValue(0) min: numeric;
  @minValue(0) max: numeric;
}

/** Server MUST enforce min_seconds <= max_seconds. */
model ExposureRange {
  @maxValue(86400) @minValueExclusive(0) min_seconds: numeric;
  @maxValue(86400) @minValueExclusive(0) max_seconds: numeric;
}

@extension("anyOf", #[#{required: #["accepted_frames"]}, #{required: #["accepted_integration_seconds"]}])
model Goal {
  @minValue(1) accepted_frames?: integer;
  @minValueExclusive(0) accepted_integration_seconds?: numeric;
}

@extension("anyOf", #[#{required: #["min_value"]}, #{required: #["max_value"]}])
model MetricRule {
  @pattern("^[a-z][a-z0-9_.-]+$") metric: string;
  method: Text;
  method_version: Text;
  unit: Text;
  required: boolean;
  min_value?: numeric;
  max_value?: numeric;
}

model ProcessingGroup {
  id: Uuid;
  name: Text;
  sampling_arcsec_per_pixel: Range;
  color_state: "mono" | "cfa" | "debayered_rgb";
  registration: "unregistered" | "registered";
  linear: true;
  @minItems(1) @maxItems(4) required_calibration_steps: ("bias_subtract" | "dark_subtract" | "flat_correct" | "dark_flat_correct")[];
}

model Objective {
  id: Uuid;
  target_id: Uuid;
  processing_group_id: Uuid;

  /** Accepted passbands. A frame qualifies when one of its passbands matches one of these. */
  @minItems(1) @maxItems(8) bandpasses: Bandpass[];

  purpose: "deep" | "short_dynamic_range" | "continuum" | "other";
  exposure: ExposureRange;
  goal: Goal;
  @minValue(0) @maxValue(1) minimum_coverage_fraction: numeric;
  fresh_pixel_solve: boolean;
  @minItems(0) @maxItems(32) quality_rules: MetricRule[];
  @minItems(0) @maxItems(32) required_features: FeatureName[];
}

model Terms {
  id: Uuid;
  @minValue(1) version: integer;
  url: HttpsUrl;
  sha256: Sha256;
}

@extension("allOf", #[
  #{
    `if`: #{properties: #{deliverable: #{`const`: "stacked_masters"}}},
    `then`: #{required: #["master_rules"]},
    `else`: #{not: #{required: #["master_rules"]}}
  }
])
/** What the project needs. Owners set it with the server's own tools. */
model RequirementSet {
  @minLength(1) @maxLength(200) title: string;
  description: Text;
  @minItems(1) @maxItems(100) targets: Target[];
  @minItems(1) @maxItems(100) processing_groups: ProcessingGroup[];
  @minItems(1) @maxItems(100) objectives: Objective[];
  terms: Terms;
  capture_deadline: Timestamp;
  submission_deadline: Timestamp;
  @minItems(0) @maxItems(32) required_features: FeatureName[];

  /** What contributors submit: calibrated subs, or masters they stack themselves. */
  deliverable: "calibrated_subs" | "stacked_masters";

  /** Rules for stacked masters. Required when `deliverable` is `stacked_masters`. */
  master_rules?: MasterRules;

  /** Present when the project accepts files shared outside the API. */
  external_delivery?: ExternalDelivery;

  @extension("propertyNames", #{pattern: "^[a-z0-9]+(?:[.-][a-z0-9]+)+$"}) @extension("maxProperties", 32) extensions?: Record<unknown>;
}

/** What a stacked master must meet, beyond the objective's own rules. */
model MasterRules {
  /** Fewest subs one master may combine. */
  @minValue(1) @maxValue(5000) min_sub_count: integer;

  /** Whether drizzled masters are accepted. Sampling rules apply to the master's pixels. */
  allow_drizzle: boolean;
}

/** File-sharing services a project accepts, and how contributors should share. */
model ExternalDelivery {
  @extension("uniqueItems", true) @minItems(1) @maxItems(6) providers: ExternalProvider[];

  /** For example: "Share files with collab@example.org; read access is enough." */
  instructions: Text;
}

union ExternalProvider {
  "google_drive",
  "dropbox",
  "onedrive",
  "box",
  "s3",
  "https",
}

/** A project and its current requirements. */
model Project {
  id: Uuid;
  title: Text;
  state: "open" | "paused" | "closed";

  /** Rises each time the owners publish new requirements. Submissions cite it. */
  @minValue(1) revision: integer;

  published_at: Timestamp;
  requirements: RequirementSet;
}

/** A project the account has joined, and the account's standing in it. */
model MyProject {
  project_id: Uuid;
  title: Text;
  state: "open" | "paused" | "closed";

  /** Only `active` members can submit or receive assignments. */
  membership: "requested" | "active" | "paused" | "withdrawn" | "revoked";
}

model MyProjectPage {
  @minItems(0) @maxItems(100) items: MyProject[];
  @maxLength(4096) next_cursor: string | null;
}

model ProgressObjective {
  objective_id: Uuid;
  goal: Goal;

  /** Frames in unexpired assignments. */
  @minValue(0) assigned_frames: integer;

  /** Frames rigs report as captured but not yet submitted. They earn no credit until accepted. */
  @minValue(0) reported_frames: integer;

  /** Frames uploaded and not yet assessed. */
  @minValue(0) pending_frames: integer;

  @minValue(0) accepted_frames: integer;
  @minValue(0) accepted_integration_seconds: numeric;
  @minValue(0) rejected_frames: integer;

  /** Accepted after the goal was met; credited to the contributor, not to the goal. */
  @minValue(0) surplus_frames: integer;

  /** True when every panel of the target has the goal's depth. */
  complete: boolean;
}

/** How far the shared picture has come. Counts are subs, whether sent alone or in masters. */
model Progress {
  project_id: Uuid;
  @minValue(1) revision: integer;
  as_of: Timestamp;
  @minItems(0) @maxItems(100) objectives: ProgressObjective[];
  @minValue(0) accepted_frames: integer;
  @minValue(0) accepted_integration_seconds: numeric;
}

/** Join projects on the server's web pages. */
@summary("List the projects you have joined")
@tag("projects")
@get @route("/me/projects")
op listMyProjects(...Paging): Ok<MyProjectPage> | Errors;

@summary("Read a project's requirements")
@useAuth(NoAuth | bearerAuth)
@tag("projects")
@get @route("/projects/{project_id}")
op getProject(@path project_id: Uuid): Ok<Project> | Errors;

@summary("Read a project's progress")
@useAuth(NoAuth | bearerAuth)
@tag("projects")
@get @route("/projects/{project_id}/progress")
op getProgress(@path project_id: Uuid): Ok<Progress> | Errors;
