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

/** A calibration frame used on a sub: its role and hash. The frame itself is never sent. */
model CalibrationFrame {
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
  /** SHA-256 of the raw frame. Required for calibrated subs; omitted for stacked masters. */
  raw_sha256?: Sha256;

  @minItems(0) @maxItems(16) calibration_frames: CalibrationFrame[];
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
  },
  #{
    `if`: #{properties: #{kind: #{`const`: "stacked_master"}}, required: #["kind"]},
    `then`: #{
      required: #["stack"],
      properties: #{calibration: #{properties: #{registration: #{`const`: "registered"}}}}
    },
    `else`: #{
      required: #["origin_id", "capture_id", "captured_at"],
      not: #{required: #["stack"]},
      properties: #{calibration: #{required: #["raw_sha256"]}}
    }
  }
])
model ArtifactManifest {
  id: Uuid;

  /** `calibrated_sub` is one calibrated exposure. `stacked_master` combines many; see `stack`. */
  kind?: "calibrated_sub" | "stacked_master" = "calibrated_sub";

  /** Capture identity of a sub. Masters carry it for each sub in `stack.subs`. */
  origin_id?: Uuid;

  capture_id?: Uuid;

  @extension("uniqueItems", true) @minItems(1) @maxItems(100) objective_ids: Uuid[];
  processing_group_id: Uuid;
  equipment: EquipmentRef;
  /** Start of a sub's exposure. Masters use `stack` times instead. */
  captured_at?: Timestamp;

  /** Exposure of a sub, or of each sub in a master. A master combines subs of one length. */
  @maxValue(86400) @minValueExclusive(0) exposure_seconds: numeric;
  filter_id: Uuid;

  /** Passbands of the filter used. A dual-band frame can count toward two objectives. */
  @minItems(1) @maxItems(8) bandpasses: Bandpass[];

  format: "fits" | "xisf";
  @minValue(1) size_bytes: integer;
  sha256: Sha256;
  calibration: Calibration;
  @minItems(0) @maxItems(100) measurements: Measurement[];
  solve?: SolveEvidence;
  supersedes_artifact_id?: Uuid;
  /** The assignment and panel the frame was taken for. */
  assignment_id?: Uuid;
  panel_id?: Uuid;

  /** How the file arrives, for subs and masters alike: `upload` through an upload session, or `external` as a shared file. */
  delivery?: "upload" | "external" = "upload";

  /** Where the file is shared. Required for, and only allowed with, external delivery. */
  external?: ExternalLocation;

  /** How a master was stacked, and from which subs. Required for, and only allowed with, masters. */
  stack?: Stack;

  @extension("propertyNames", #{pattern: "^[a-z0-9]+(?:[.-][a-z0-9]+)+$"}) @extension("maxProperties", 32) extensions?: Record<unknown>;
}

/** One sub that went into a master. */
model StackedSub {
  origin_id: Uuid;
  capture_id: Uuid;
  captured_at: Timestamp;

  /** SHA-256 of the calibrated sub as it entered the stack. */
  sha256: Sha256;
}

/** Provenance of a stacked master. */
model Stack {
  @minValue(1) @maxValue(5000) sub_count: integer;

  /** Total exposure of the subs: `sub_count` × `exposure_seconds`. */
  @minValueExclusive(0) integration_seconds: numeric;

  first_captured_at: Timestamp;
  last_captured_at: Timestamp;

  /** Every sub in the master. A sub may appear in only one credited master or sub. */
  @minItems(1) @maxItems(5000) subs: StackedSub[];

  /** For example "star alignment, Lanczos-3". */
  registration: Text;

  /** For example "additive with scaling". */
  normalization: Text;

  /** For example "winsorized sigma clip, 3.0/3.0". */
  rejection: Text;

  /** For example "PSF signal weight". */
  weighting: Text;

  /** Present when the master was drizzled. */
  @minValue(1) @maxValue(4) drizzle_scale?: numeric;

  software: Text;
  version: Text;
}

/**
 * A file shared outside the API: a calibrated sub or a stacked master. Visible only to the
 * submitter and reviewing maintainers. Many subs can share one folder link, each naming
 * its file with `path`.
 */
model ExternalLocation {
  provider: ExternalProvider;

  /** Link to the file, or to a shared folder that holds it. It may grant access; treat it as private. */
  url: HttpsUrl;

  /** The file's path inside the shared folder at `url`, using `/` between folders. */
  @minLength(1) @maxLength(1024) @pattern("^(?!/)(?!.*(^|/)\\.\\.(/|$))[^\\\\]+$") path?: string;

  /** Provider's file ID, when the link and path do not identify the file. */
  @minLength(1) @maxLength(1024) reference?: string;

  shared_at: Timestamp;
}

/** A batch of files for one project. The client picks the `id`; repeating it returns the existing submission. */
model SubmissionCreate {
  id: Uuid;
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

/** What happened to one file. */
model ArtifactResult {
  artifact_id: Uuid;
  state: "uploading" | "awaiting_retrieval" | "received" | "validating" | "accepted" | "rejected" | "superseded";

  /** Why a file was rejected, such as `digest_mismatch` or `quality_limit`. */
  @minItems(0) @maxItems(32) reason_codes: Text[];

  /** Subs credited toward the goal: 1 for a sub, the accepted count for a master. */
  @minValue(0) credited_frames?: integer;

  @minValue(0) credited_integration_seconds?: numeric;
}

model Submission {
  id: Uuid;
  project_id: Uuid;
  @minValue(1) project_revision: integer;
  state: "uploading" | "processing" | "complete";
  created_at: Timestamp;
  manifest: SubmissionCreate;
  @minItems(0) @maxItems(100) uploads: UploadSession[];
  @minItems(1) @maxItems(100) artifacts: ArtifactResult[];
}

@summary("Describe files to submit and get upload sessions")
@tag("submissions")
@post @route("/projects/{project_id}/submissions")
op createSubmission(
  @path project_id: Uuid,
  @body body: SubmissionCreate
): Created<Submission> | Existing<Submission> | WriteErrors;

@summary("Read a submission and each file's outcome")
@tag("submissions")
@get @route("/submissions/{submission_id}")
op getSubmission(@path submission_id: Uuid): Ok<Submission> | Errors;

@summary("See which parts of an upload arrived")
@tag("submissions")
@get @route("/uploads/{upload_id}")
op getUpload(@path upload_id: Uuid): Ok<UploadSession> | Errors;

/** Sending the same bytes again returns the same receipt. */
@summary("Send one part of a file")
@tag("submissions")
@put @route("/uploads/{upload_id}/parts/{part_number}")
op putUploadPart(
  @path upload_id: Uuid,
  @path @minValue(1) @maxValue(10000) part_number: integer,
  @header("Content-Length") @minValue(1) contentLength: integer,
  @header("X-Part-SHA256") partSha256: Sha256,
  @header contentType: "application/octet-stream",
  @body body: bytes
): Ok<PartReceipt> | WriteErrors;

/** Returns the submission in `processing`. Read it again until it is `complete`. */
@summary("Finish a submission and start assessment")
@tag("submissions")
@post @route("/submissions/{submission_id}/finalize")
op finalizeSubmission(@path submission_id: Uuid): Accepted<Submission> | WriteErrors;
