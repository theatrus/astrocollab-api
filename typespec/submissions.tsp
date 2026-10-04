using Http;
using OpenAPI;

namespace AstroCollab;

model Measurement {
  metric: Text;
  method: Text;
  method_version: Text;
  unit: Text;
  value: numeric;
  measured_at: Timestamp;
}

model SolveEvidence {
  source: "fresh_pixel_solve" | "embedded_wcs";
  artifact_sha256: Sha256;
  center: Position;
  method: Text;
  method_version: Text;
  measured_at: Timestamp;
}

model Master {
  role: "bias" | "dark" | "flat" | "dark_flat";
  sha256: Sha256;
}

model CalibrationStep {
  operation: "bias_subtract" | "dark_subtract" | "flat_correct" | "dark_flat_correct" | "debayer" | "register";
  software: Text;
  version: Text;
  @extension("maxProperties", 32) parameters: Record<string | numeric | boolean>;
}

@extension("allOf", #[
  #{
    `if`: #{properties: #{pixel_unit: #{`const`: "normalized"}}},
    then: #{required: #["adu_scale"]}
  }
])
model Calibration {
  raw_sha256: Sha256;
  @minItems(0) @maxItems(16) masters: Master[];
  @minItems(1) @maxItems(32) steps: CalibrationStep[];
  pixel_unit: "adu" | "electrons" | "normalized";
  @minValueExclusive(0) adu_scale?: numeric;
  linear: boolean;
  color_state: "mono" | "cfa" | "debayered_rgb";
  registration: "unregistered" | "registered";
}

@extension("allOf", #[
  #{
    `if`: #{properties: #{delivery: #{`const`: "external"}}, required: #["delivery"]},
    `then`: #{required: #["external"]},
    `else`: #{not: #{required: #["external"]}}
  }
])
model ArtifactManifest {
  id: Uuid;
  origin_id: Uuid;
  capture_id: Uuid;
  @extension("uniqueItems", true) @minItems(1) @maxItems(100) objective_ids: Uuid[];
  processing_group_id: Uuid;
  equipment: EquipmentRef;
  captured_at: Timestamp;
  @maxValue(86400) @minValueExclusive(0) exposure_seconds: numeric;
  filter_id: Uuid;
  bandpass: Bandpass;
  format: "fits" | "xisf";
  @minValue(1) size_bytes: integer;
  sha256: Sha256;
  calibration: Calibration;
  @minItems(0) @maxItems(100) measurements: Measurement[];
  solve?: SolveEvidence;
  supersedes_artifact_id?: Uuid;
  recommendation_id?: Uuid;
  panel_id?: Uuid;

  /** `upload` sends bytes through an upload session. `external` registers a shared file. */
  delivery?: "upload" | "external" = "upload";

  /** Where the file is shared. Required for, and only allowed with, external delivery. */
  external?: ExternalLocation;

  @extension("propertyNames", #{pattern: "^[a-z0-9]+(?:[.-][a-z0-9]+)+$"}) @extension("maxProperties", 32) extensions?: Record<unknown>;
}

/** A file shared outside the API. Visible only to the submitter and reviewing maintainers. */
model ExternalLocation {
  provider: ExternalProvider;

  /** Link the project can open. It may grant access; treat it as private. */
  url: HttpsUrl;

  /** Provider's file ID, when the link alone does not identify the file. */
  @minLength(1) @maxLength(1024) reference?: string;

  shared_at: Timestamp;
}

/** Result of fetching an externally shared file. */
@extension("allOf", #[
  #{
    `if`: #{properties: #{outcome: #{`enum`: #["verified", "digest_mismatch"]}}},
    `then`: #{required: #["sha256", "size_bytes"]}
  }
])
model RetrievalInput {
  /** `verified` means the bytes match the manifest's hash and size. */
  outcome: "verified" | "unavailable" | "digest_mismatch";

  /** SHA-256 of the bytes retrieved. */
  sha256?: Sha256;

  @minValue(1) size_bytes?: integer;
  retrieved_at: Timestamp;
  note?: Text;
}

model SubmissionCreate {
  id: Uuid;
  participation_id: Uuid;
  @minValue(1) project_revision: integer;
  @minItems(1) @maxItems(100) artifacts: ArtifactManifest[];
}

model PartReceipt {
  @minValue(1) @maxValue(10000) part_number: integer;
  @minValue(1) size_bytes: integer;
  sha256: Sha256;
}

model UploadSession {
  id: Uuid;
  submission_id: Uuid;
  artifact_id: Uuid;
  @minValue(1) size_bytes: integer;
  @minValue(1) part_size_bytes: integer;
  @minValue(1) @maxValue(10000) part_count: integer;
  expires_at: Timestamp;
  @minItems(0) @maxItems(10000) received_parts: PartReceipt[];
}

model ArtifactResult {
  artifact_id: Uuid;
  state: "uploading" | "awaiting_retrieval" | "received" | "validating" | "accepted" | "rejected" | "superseded";
  assessment_id?: Uuid;
  @minItems(0) @maxItems(32) reason_codes: Text[];
}

model Submission {
  id: Uuid;
  project_id: Uuid;
  participation_id: Uuid;
  @minValue(1) project_revision: integer;
  state: "uploading" | "processing" | "complete";
  created_at: Timestamp;
  manifest: SubmissionCreate;
  @minItems(0) @maxItems(100) uploads: UploadSession[];
  @minItems(1) @maxItems(100) artifacts: ArtifactResult[];
  job_id?: Uuid;
}

/**
 * Per artifact/objective. Non-surplus accepted credit is one frame and its exposure; surplus
 * contributes zero required credit.
 */
@extension("allOf", #[
  #{
    `if`: #{properties: #{surplus: #{`const`: true}}},
    then: #{
      properties: #{accepted_frames: #{`const`: 0}, accepted_integration_seconds: #{`const`: 0}}
    },
    `else`: #{
      properties: #{accepted_frames: #{`const`: 1}, accepted_integration_seconds: #{exclusiveMinimum: 0}}
    }
  }
])
model Credit {
  objective_id: Uuid;
  @minValue(0) @maxValue(1) accepted_frames: integer;
  @minValue(0) accepted_integration_seconds: numeric;
  @minValue(0) @maxValue(1) coverage_fraction: numeric;
  surplus: boolean;
}

@extension("allOf", #[
  #{
    `if`: #{properties: #{decision: #{`const`: "rejected"}}},
    then: #{properties: #{credits: #{maxItems: 0}, reasons: #{minItems: 1}}}
  },
  #{
    `if`: #{properties: #{decision: #{`const`: "accepted"}}},
    then: #{properties: #{credits: #{minItems: 1}}}
  }
])
model AssessmentInput {
  id: Uuid;
  artifact_id: Uuid;
  @minValue(1) policy_revision: integer;
  decision: "accepted" | "rejected";
  @minItems(0) @maxItems(32) reasons: Text[];
  @minItems(0) @maxItems(100) measurements: Measurement[];
  @minItems(0) @maxItems(100) credits: Credit[];
}

@extension("allOf", #[
  #{
    `if`: #{properties: #{decision: #{`const`: "rejected"}}},
    then: #{properties: #{credits: #{maxItems: 0}, reasons: #{minItems: 1}}}
  },
  #{
    `if`: #{properties: #{decision: #{`const`: "accepted"}}},
    then: #{properties: #{credits: #{minItems: 1}}}
  }
])
model Assessment {
  id: Uuid;
  submission_id: Uuid;
  project_id: Uuid;
  participation_id: Uuid;
  origin_id: Uuid;
  capture_id: Uuid;
  artifact_id: Uuid;
  @minValue(1) policy_revision: integer;
  decision: "accepted" | "rejected";
  @minItems(0) @maxItems(32) reasons: Text[];
  @minItems(0) @maxItems(100) measurements: Measurement[];
  @minItems(0) @maxItems(100) credits: Credit[];
  assessed_at: Timestamp;
  assessor_id: Uuid;
  @minValue(1) event_sequence: integer;
}

model AssessmentPage {
  @minItems(0) @maxItems(100) items: Assessment[];
  @maxLength(4096) next_cursor: string | null;
}

model SubmissionPage {
  @minItems(0) @maxItems(100) items: Submission[];
  @maxLength(4096) next_cursor: string | null;
}

/**
 * The project must match the token. submission:read-all may replace submission:read-own for
 * maintainers; ordinary contributors can only inspect their own submission.
 */
@summary("Inspect manifest, transfer and per-artifact outcomes")
@access("project", "submission:read-own")
@extension("x-scope-rules", #{project: #[#["submission:read-own"], #["submission:read-all"]]})
@tag("submissions")
@get @route("/submissions/{submission_id}")
op getSubmission(
  @path submission_id: Uuid,
  ...ConditionalRead
): Ok<Submission> | NotModified | Errors;

@summary("Inspect received parts and session expiry")
@access("project", "submission:write")
@tag("uploads")
@get @route("/uploads/{upload_id}")
op getUpload(
  @path upload_id: Uuid,
  ...ConditionalRead
): Ok<UploadSession> | NotModified | Errors;

@summary("Renew an unchanged upload after authorization checks")
@access("project", "submission:write")
@tag("uploads")
@post @route("/uploads/{upload_id}/renew")
op renewUpload(
  @path upload_id: Uuid,
  ...IdempotencyKey
): Ok<UploadSession> | WriteErrors;

@summary("Write a verified fixed-size part idempotently")
@access("project", "submission:write")
@tag("uploads")
@put @route("/uploads/{upload_id}/parts/{part_number}")
op putUploadPart(
  @path upload_id: Uuid,
  @path @minValue(1) @maxValue(10000) part_number: integer,
  @header("Content-Length") @minValue(1) contentLength: integer,
  @header("X-Part-SHA256") partSha256: Sha256,
  @header contentType: "application/octet-stream",
  @body body: bytes
): Ok<PartReceipt> | WriteErrors;

@summary("Finalize uploads and queue assessment")
@access("project", "submission:write")
@tag("submissions")
@post @route("/submissions/{submission_id}/finalize")
op finalizeSubmission(
  @path submission_id: Uuid,
  ...IdempotencyKey
): Accepted<Job> | WriteErrors;

/** submission:read-all can replace submission:read-own for a maintainer of this project. */
@summary("Read artifact assessment history")
@access("project", "submission:read-own")
@extension("x-scope-rules", #{project: #[#["submission:read-own"], #["submission:read-all"]]})
@tag("submissions")
@get @route("/submissions/{submission_id}/assessments")
op listAssessments(
  @path submission_id: Uuid,
  ...Paging
): Ok<AssessmentPage> | Errors;

/**
 * For an artifact with external delivery, after the submission is finalized; earlier calls
 * return 409 `invalid_transition`. `verified` requires the manifest's hash and size, and
 * moves the artifact to validation. `unavailable` and `digest_mismatch` reject it.
 */
@summary("Record retrieval of an externally shared file")
@access("project", "assessment:write")
@tag("submissions")
@post @route("/submissions/{submission_id}/artifacts/{artifact_id}/retrieval")
op recordRetrieval(
  @path submission_id: Uuid,
  @path artifact_id: Uuid,
  ...IdempotencyKey,
  ...IfMatch,
  @body body: RetrievalInput
): Ok<Submission> | ConditionalWriteErrors;

@summary("Append an assessment and update credit atomically")
@access("project", "assessment:write")
@tag("submissions")
@post @route("/submissions/{submission_id}/assessments")
op appendAssessment(
  @path submission_id: Uuid,
  ...IdempotencyKey,
  ...IfMatch,
  @body body: AssessmentInput
): Created<Assessment> | ConditionalWriteErrors;
