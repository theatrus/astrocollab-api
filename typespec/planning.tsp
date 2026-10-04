using Http;
using OpenAPI;

namespace AstroCollab;

model Filter {
  id: Uuid;
  bandpass: Bandpass;
}

model Site {
  @minValue(-90) @maxValue(90) latitude_degrees: numeric;
  @minValue(-180) @maxValue(180) longitude_degrees: numeric;
  @minValue(0) precision_km: numeric;
  timezone: Text;
}

model EquipmentInput {
  name: Text;
  @minValue(1) @maxValue(100000) sensor_width_pixels: integer;
  @minValue(1) @maxValue(100000) sensor_height_pixels: integer;
  @maxValue(1000) @minValueExclusive(0) pixel_size_um: numeric;
  @maxValue(100000) @minValueExclusive(0) focal_length_mm: numeric;
  @minValue(1) @maxValue(16) binning_x: integer;
  @minValue(1) @maxValue(16) binning_y: integer;
  color_state: "mono" | "cfa" | "debayered_rgb";
  @minItems(1) @maxItems(100) filters: Filter[];
  rotation: "fixed" | "manual" | "motorized";
  @minValue(0) @maxValueExclusive(360) confirmed_position_angle_degrees: numeric;
  site?: Site;
  @extension("propertyNames", #{pattern: "^[a-z0-9]+(?:[.-][a-z0-9]+)+$"}) @extension("maxProperties", 32) extensions?: Record<unknown>;
}

model Equipment {
  id: Uuid;
  participation_id: Uuid;
  @minValue(1) revision: integer;
  observed_at: Timestamp;
  configuration: EquipmentInput;
}

model EquipmentRef {
  equipment_id: Uuid;
  @minValue(1) revision: integer;
}

model Availability {
  start: Timestamp;
  end: Timestamp;
}

model CapacityInput {
  @pattern("^[0-9]{4}-(0[1-9]|1[0-2])$") month: string;
  timezone: Text;
  @minValue(0) offered_rig_seconds: numeric;
  @extension("uniqueItems", true) @minItems(1) @maxItems(100) equipment_ids: Uuid[];
  @minItems(0) @maxItems(100) availability: Availability[];
  mode: "soft" | "local_hard";
  @minValue(0) reported_attempted_rig_seconds: numeric;
  usage_observed_at: Timestamp;
}

model Capacity {
  participation_id: Uuid;
  @minValue(1) revision: integer;
  offer: CapacityInput;
}

model RotationPolicy {
  mode: "preserve_confirmed" | "motorized_allowed";
  @minValue(0) @maxValue(5) tolerance_degrees: numeric;
}

/** Approved bounds for panel centers. A target ID alone does not authorize a new position. */
model AllowedRegion {
  target_id: Uuid;
  center_region: Footprint;
}

model PlanningPolicyInput {
  mode: "suggest_only" | "automatic";
  @minItems(1) @maxItems(100) equipment: EquipmentRef[];
  @minItems(1) @maxItems(100) allowed_regions: AllowedRegion[];
  @extension("uniqueItems", true) @minItems(1) @maxItems(100) allowed_filter_ids: Uuid[];
  exposure: ExposureRange;
  @minValue(0) @maxValue(0.9) minimum_overlap_fraction: numeric;
  @minValue(1) @maxValue(100) max_panels: integer;
  rotation: RotationPolicy;
  accepted_terms: Terms;
  @minValue(60) @maxValue(604800) max_cached_plan_seconds: integer;
}

model PlanningPolicy {
  participation_id: Uuid;
  @minValue(1) revision: integer;
  policy: PlanningPolicyInput;
}

@extension("allOf", #[
  #{
    `if`: #{properties: #{action: #{`const`: "recommendation_pending"}}},
    then: #{required: #["job_id"]}
  }
])
model Advice {
  action: "recommendation_pending" | "keep_current" | "wait" | "review_required" | "unavailable";
  @minValue(0) demand_sequence: integer;
  @minValue(30) @maxValue(86400) next_checkin_seconds: integer;
  @minItems(0) @maxItems(32) reason_codes: Code[];
  job_id?: Uuid;
}

model EquipmentRegistered {
  equipment: Equipment;
  advice: Advice;
}

model Checkin {
  @minItems(1) @maxItems(100) equipment: EquipmentRef[];
  @minValue(1) capacity_revision: integer;
  @minValue(1) planning_policy_revision: integer;
  adopted_recommendation_id?: Uuid;
  @minValue(0) captured_reported_frames: integer;
  @minValue(0) reported_attempted_rig_seconds: numeric;
  observed_at: Timestamp;
}

model RecommendationRequest {
  participation_id: Uuid;
  @minValue(1) project_revision: integer;
  @minItems(1) @maxItems(100) equipment: EquipmentRef[];
  @minValue(1) capacity_revision: integer;
  @minValue(1) planning_policy_revision: integer;
}

model Panel {
  id: Uuid;
  target_id: Uuid;
  @minItems(1) @maxItems(100) objective_ids: Uuid[];
  footprint: Footprint;
  @minValue(0) @maxValue(0.9) overlap_fraction: numeric;
  equipment: EquipmentRef;
  filter_id: Uuid;
  @maxValue(86400) @minValueExclusive(0) exposure_seconds: numeric;
  @minValue(1) suggested_frames: integer;
}

@extension("allOf", #[
  #{
    `if`: #{properties: #{automatic_eligible: #{`const`: true}}},
    then: #{properties: #{review_reasons: #{maxItems: 0}}}
  }
])
model Recommendation {
  id: Uuid;
  project_id: Uuid;
  @minValue(1) project_revision: integer;
  participation_id: Uuid;
  @minValue(1) planning_policy_revision: integer;
  @minValue(1) capacity_revision: integer;
  @minValue(0) demand_sequence: integer;
  created_at: Timestamp;
  expires_at: Timestamp;
  automatic_eligible: boolean;
  @minItems(0) @maxItems(32) review_reasons: Text[];
  @minItems(1) @maxItems(100) panels: Panel[];
  @minValue(0) estimated_rig_seconds: numeric;
  @minValue(0) estimated_accepted_integration_seconds: numeric;
  @minItems(0) @maxItems(32) assumptions: Text[];
  @minItems(0) @maxItems(100) unmet_objective_ids: Uuid[];
}

model IntentInput {
  @minValue(1) project_revision: integer;
  recommendation_id?: Uuid;
  @minValue(1) planning_policy_revision?: integer;
  @minItems(1) @maxItems(100) panels: Panel[];
  @minValue(0) estimated_rig_seconds: numeric;
  expires_at: Timestamp;
  state: "active" | "cancelled";
}

model Intent {
  id: Uuid;
  participation_id: Uuid;
  @minValue(1) revision: integer;
  intent: IntentInput;
}

model StatusInput {
  @minValue(1) sequence: integer;
  observed_at: Timestamp;
  expires_at: Timestamp;
  phase: "idle" | "planning" | "acquiring" | "calibrating" | "uploading" | "waiting" | "stopped";
  objective_id?: Uuid;
  @minValue(0) captured_reported_frames: integer;
  visibility: "private" | "project";
}

model StatusReceipt {
  writer_id: Uuid;
  applied: boolean;
  @minValue(1) last_sequence: integer;
  received_at: Timestamp;
}

model Activity {
  participation_id: Uuid;
  writer_id: Uuid;
  status: StatusInput;
  received_at: Timestamp;
  stale: boolean;
}

model ActivityPage {
  @minItems(0) @maxItems(100) items: Activity[];
  @maxLength(4096) next_cursor: string | null;
}

model ProgressObjective {
  objective_id: Uuid;
  goal: Goal;
  @minValue(0) intended_frames: integer;
  @minValue(0) captured_reported_frames: integer;
  @minValue(0) received_pending_frames: integer;
  @minValue(0) accepted_frames: integer;
  @minValue(0) accepted_integration_seconds: numeric;
  @minValue(0) rejected_frames: integer;
  @minValue(0) surplus_frames: integer;
  complete: boolean;
}

model Progress {
  project_id: Uuid;
  @minValue(1) project_revision: integer;
  @minValue(0) event_sequence: integer;
  as_of: Timestamp;
  @minItems(0) @maxItems(100) objectives: ProgressObjective[];
  @minValue(0) distinct_credited_captures: integer;
  @minValue(0) distinct_credited_integration_seconds: numeric;
}

@extension("allOf", #[
  #{
    `if`: #{properties: #{state: #{`const`: "succeeded"}}},
    then: #{required: #["result"], not: #{required: #["error"]}}
  },
  #{
    `if`: #{properties: #{state: #{`const`: "failed"}}},
    then: #{required: #["error"], not: #{required: #["result"]}}
  },
  #{
    `if`: #{properties: #{state: #{`enum`: #["queued", "running"]}}},
    then: #{not: #{anyOf: #[#{required: #["result"]}, #{required: #["error"]}]}}
  },
  #{
    `if`: #{properties: #{kind: #{`const`: "recommendation"}}},
    then: #{properties: #{result: #{`$ref`: "#/components/schemas/Recommendation"}}}
  },
  #{
    `if`: #{properties: #{kind: #{`const`: "assessment"}}},
    then: #{properties: #{result: #{`$ref`: "#/components/schemas/Submission"}}}
  }
])
model Job {
  id: Uuid;
  kind: "recommendation" | "assessment";
  state: "queued" | "running" | "succeeded" | "failed";
  @minValue(1) @maxValue(3600) poll_after_seconds: integer;
  @oneOf result?: Recommendation | Submission;
  error?: Problem;
}

/**
 * ETag belongs to the equipment resource, not the advisory result. A recommendation job is
 * optional and never authorizes acquisition.
 */
@summary("Register equipment and request framing advice")
@access("project", "offer:write")
@tag("participations")
@put @route("/participations/{participation_id}/equipment/{equipment_id}")
op registerEquipment(
  @path participation_id: Uuid,
  @path equipment_id: Uuid,
  ...CreateOrReplace,
  @body body: EquipmentInput
): Ok<EquipmentRegistered> | ConditionalWriteErrors;

@summary("Read current offered equipment")
@access("project", "project:read")
@tag("participations")
@get @route("/participations/{participation_id}/equipment/{equipment_id}")
op getEquipment(
  @path participation_id: Uuid,
  @path equipment_id: Uuid,
  ...ConditionalRead
): Ok<Equipment> | NotModified | Errors;

@summary("Read an immutable equipment revision")
@access("project", "project:read")
@tag("participations")
@get @route("/participations/{participation_id}/equipment/{equipment_id}/revisions/{revision}")
op getEquipmentRevision(
  @path participation_id: Uuid,
  @path equipment_id: Uuid,
  @path @minValue(1) revision: integer,
  ...ConditionalRead
): Ok<Equipment> | NotModified | Errors;

@summary("Set this project's monthly effort share")
@access("project", "offer:write")
@tag("participations")
@put @route("/participations/{participation_id}/capacity")
op setCapacity(
  @path participation_id: Uuid,
  ...CreateOrReplace,
  @body body: CapacityInput
): Ok<Capacity> | ConditionalWriteErrors;

@summary("Read current offered monthly capacity")
@access("project", "project:read")
@tag("participations")
@get @route("/participations/{participation_id}/capacity")
op getCapacity(
  @path participation_id: Uuid,
  ...ConditionalRead
): Ok<Capacity> | NotModified | Errors;

@summary("Read an immutable monthly capacity revision")
@access("project", "project:read")
@tag("participations")
@get @route("/participations/{participation_id}/capacity/revisions/{revision}")
op getCapacityRevision(
  @path participation_id: Uuid,
  @path @minValue(1) revision: integer,
  ...ConditionalRead
): Ok<Capacity> | NotModified | Errors;

@summary("Record reviewed bounds for automatic framing")
@access("project", "offer:write")
@tag("participations")
@put @route("/participations/{participation_id}/planning-policy")
op setPlanningPolicy(
  @path participation_id: Uuid,
  ...CreateOrReplace,
  @body body: PlanningPolicyInput
): Ok<PlanningPolicy> | ConditionalWriteErrors;

@summary("Read current automatic framing bounds")
@access("project", "project:read")
@tag("participations")
@get @route("/participations/{participation_id}/planning-policy")
op getPlanningPolicy(
  @path participation_id: Uuid,
  ...ConditionalRead
): Ok<PlanningPolicy> | NotModified | Errors;

@summary("Read immutable reviewed framing bounds")
@access("project", "project:read")
@tag("participations")
@get @route("/participations/{participation_id}/planning-policy/revisions/{revision}")
op getPlanningPolicyRevision(
  @path participation_id: Uuid,
  @path @minValue(1) revision: integer,
  ...ConditionalRead
): Ok<PlanningPolicy> | NotModified | Errors;

@summary("Report progress and request framing advice")
@access("project", "offer:write")
@tag("participations")
@post @route("/participations/{participation_id}/checkins")
op checkIn(
  @path participation_id: Uuid,
  ...IdempotencyKey,
  @body body: Checkin
): Ok<Advice> | WriteErrors;

@summary("Request framing or mosaic recommendations")
@access("project", "offer:write")
@tag("projects")
@post @route("/projects/{project_id}/recommendations")
op requestRecommendation(
  @path project_id: Uuid,
  ...IdempotencyKey,
  @body body: RecommendationRequest
): Accepted<Job> | WriteErrors;

@summary("Publish nonexclusive intended coverage")
@access("project", "intent:write")
@tag("participations")
@put @route("/participations/{participation_id}/intents/{intent_id}")
op setIntent(
  @path participation_id: Uuid,
  @path intent_id: Uuid,
  ...CreateOrReplace,
  @body body: IntentInput
): Ok<Intent> | ConditionalWriteErrors;

@summary("Read own contribution intent")
@access("project", "project:read")
@tag("participations")
@get @route("/participations/{participation_id}/intents/{intent_id}")
op getIntent(
  @path participation_id: Uuid,
  @path intent_id: Uuid,
  ...ConditionalRead
): Ok<Intent> | NotModified | Errors;

@summary("Report current activity")
@access("project", "status:write")
@tag("participations")
@put @route("/participations/{participation_id}/status/{writer_id}")
op reportStatus(
  @path participation_id: Uuid,
  @path writer_id: Uuid,
  @body body: StatusInput
): Ok<StatusReceipt> | WriteErrors;

@summary("Read own activity and explicitly shared project activity")
@access("project", "project:read")
@tag("projects")
@get @route("/projects/{project_id}/activity")
op listActivity(
  @path project_id: Uuid,
  ...Paging
): Ok<ActivityPage> | Errors;

@summary("Read goals and contribution progress")
@access("public_or_project", "project:read")
@useAuth(NoAuth | bearerAuth)
@tag("projects")
@get @route("/projects/{project_id}/progress")
op getProgress(
  @path project_id: Uuid,
  ...ConditionalRead
): Ok<Progress> | NotModified | Errors;

/**
 * Recommendation jobs require project:read and ownership. Assessment jobs require
 * submission:read-own plus ownership, or submission:read-all for a maintainer. The required
 * scope is conditional on job kind; see x-scope-rules.
 */
@summary("Poll an authorized project job")
@access("project", "project:read")
@extension("x-scope-rules", #{
  recommendation: #[#["project:read"]],
  assessment: #[#["submission:read-own"], #["submission:read-all"]]
})
@tag("jobs")
@get @route("/jobs/{job_id}")
op getJob(
  @path job_id: Uuid,
  ...ConditionalRead
): Ok<Job> | NotModified | Errors;
