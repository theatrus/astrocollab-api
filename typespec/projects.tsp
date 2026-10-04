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

model Bandpass {
  @minLength(1) @maxLength(80) name: string;
  @minValue(100) @maxValue(30000) center_nm: numeric;
  @maxValue(30000) @minValueExclusive(0) width_nm: numeric;
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
  bandpass: Bandpass;
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

model RequirementSet {
  @minLength(1) @maxLength(200) title: string;
  description: Text;
  visibility: "public" | "members";
  enrollment: "open" | "approval_required";
  state: "open" | "paused" | "closed";
  @minItems(1) @maxItems(100) targets: Target[];
  @minItems(1) @maxItems(100) processing_groups: ProcessingGroup[];
  @minItems(1) @maxItems(100) objectives: Objective[];
  terms: Terms;
  capture_deadline: Timestamp;
  submission_deadline: Timestamp;
  surplus_policy: "retain_and_attribute" | "reject_excess";
  @minValue(1) acceptance_policy_revision: integer;
  @minItems(0) @maxItems(32) required_features: FeatureName[];

  /** Present when the project accepts files shared outside the API. */
  external_delivery?: ExternalDelivery;

  @extension("propertyNames", #{pattern: "^[a-z0-9]+(?:[.-][a-z0-9]+)+$"}) @extension("maxProperties", 32) extensions?: Record<unknown>;
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

model ProjectCreate {
  id: Uuid;
  requirements: RequirementSet;
}

model Project {
  id: Uuid;
  owner_account_id: Uuid;
  title: Text;
  visibility: "public" | "members";
  state: "draft" | "open" | "paused" | "closed";
  @minValue(1) current_revision: integer | null;
  updated_at: Timestamp;
}

model ProjectDraft {
  project_id: Uuid;
  @minValue(1) draft_revision: integer;
  requirements: RequirementSet;
}

model ProjectRevision {
  project_id: Uuid;
  @minValue(1) revision: integer;
  published_at: Timestamp;
  requirements: RequirementSet;
}

model ProjectCreated {
  project: Project;
  owner_participation: Participation;
}

model ProjectPage {
  @minItems(0) @maxItems(100) items: Project[];
  @maxLength(4096) next_cursor: string | null;
}

model TargetPage {
  @minItems(0) @maxItems(100) items: Target[];
  @maxLength(4096) next_cursor: string | null;
}

model ObjectivePage {
  @minItems(0) @maxItems(100) items: Objective[];
  @maxLength(4096) next_cursor: string | null;
}

/**
 * Anonymous requests see public listings. An optional account bearer token with account:read
 * also exposes that account's memberships; invalid supplied credentials return 401.
 */
@summary("List visible projects")
@access("public_or_account")
@useAuth(NoAuth | bearerAuth)
@tag("projects")
@get @route("/projects")
op listProjects(
  ...Paging
): Ok<ProjectPage> | Errors;

@summary("Create a draft project and owner membership")
@access("account", "project:create")
@tag("projects")
@post @route("/projects")
op createProject(
  ...IdempotencyKey,
  @body body: ProjectCreate
): Created<ProjectCreated> | WriteErrors;

/**
 * Public summaries allow anonymous reads; private summaries require a project token.
 * Account-authorized sync separately returns selected project metadata.
 */
@summary("Read a visible project summary")
@access("public_or_project", "project:read")
@useAuth(NoAuth | bearerAuth)
@tag("projects")
@get @route("/projects/{project_id}")
op getProject(
  @path project_id: Uuid,
  ...ConditionalRead
): Ok<Project> | NotModified | Errors;

@summary("Read the current owner draft")
@access("project", "project:manage")
@tag("projects")
@get @route("/projects/{project_id}/draft")
op getDraft(
  @path project_id: Uuid,
  ...ConditionalRead
): Ok<ProjectDraft> | NotModified | Errors;

@summary("Replace the complete project draft")
@access("project", "project:manage")
@tag("projects")
@put @route("/projects/{project_id}/draft")
op replaceDraft(
  @path project_id: Uuid,
  ...IfMatch,
  @body body: RequirementSet
): Ok<ProjectDraft> | ConditionalWriteErrors;

@summary("Publish the draft as an immutable revision")
@access("project", "project:manage")
@tag("projects")
@post @route("/projects/{project_id}/publish")
op publishProject(
  @path project_id: Uuid,
  ...IdempotencyKey,
  ...IfMatch
): Created<ProjectRevision> | ConditionalWriteErrors;

@summary("Read immutable published requirements")
@access("public_or_project", "project:read")
@useAuth(NoAuth | bearerAuth)
@tag("projects")
@get @route("/projects/{project_id}/revisions/{revision}")
op getProjectRevision(
  @path project_id: Uuid,
  @path @minValue(1) revision: integer,
  ...ConditionalRead
): Ok<ProjectRevision> | NotModified | Errors;

@summary("Read targets from an exact published revision")
@access("public_or_project", "project:read")
@useAuth(NoAuth | bearerAuth)
@tag("projects")
@get @route("/projects/{project_id}/targets")
op listTargets(
  @path project_id: Uuid,
  ...RevisionQuery,
  ...Paging
): Ok<TargetPage> | Errors;

@summary("Read objectives from a published revision")
@access("public_or_project", "project:read")
@useAuth(NoAuth | bearerAuth)
@tag("projects")
@get @route("/projects/{project_id}/objectives")
op listObjectives(
  @path project_id: Uuid,
  ...RevisionQuery,
  ...Paging
): Ok<ObjectivePage> | Errors;

@summary("Volunteer and accept current project terms")
@access("account", "participation:manage")
@tag("projects")
@post @route("/projects/{project_id}/participations")
op joinProject(
  @path project_id: Uuid,
  ...IdempotencyKey,
  @body body: JoinRequest
): Created<Participation> | WriteErrors;

@summary("List project membership requests for review")
@access("project", "participation:review")
@tag("projects")
@get @route("/projects/{project_id}/participations")
op listProjectParticipations(
  @path project_id: Uuid,
  ...Paging
): Ok<ParticipationPage> | Errors;

@summary("Register an immutable manifest and resumable uploads")
@access("project", "submission:write")
@tag("projects")
@post @route("/projects/{project_id}/submissions")
op createSubmission(
  @path project_id: Uuid,
  ...IdempotencyKey,
  @body body: SubmissionCreate
): Created<Submission> | WriteErrors;

@summary("List submissions for maintainer assessment")
@access("project", "submission:read-all")
@tag("projects")
@get @route("/projects/{project_id}/submissions")
op listProjectSubmissions(
  @path project_id: Uuid,
  ...Paging
): Ok<SubmissionPage> | Errors;
