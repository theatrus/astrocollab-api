using Http;
using OpenAPI;

namespace AstroCollab;

model Limits {
  @minValue(1024) max_json_bytes: integer;
  @minValue(1) @maxValue(100) max_artifacts_per_submission: integer;
  @minValue(1) max_artifact_bytes: integer;
  @minValue(1) max_chunk_bytes: integer;
  @minValue(86400) idempotency_retention_seconds: integer;
  @minValue(60) snapshot_ttl_seconds: integer;
  @minValue(86400) change_retention_seconds: integer;
  @minValue(3600) upload_staging_seconds: integer;
  @minValue(1) max_decoded_pixels: integer;
}

/** Public server description. */
model Capabilities {
  server_id: Uuid;
  api_root: HttpsUrl;
  @minItems(1) @maxItems(100) protocol_versions: "v1"[];
  spec_version: "0.1.0-draft.1";
  @minItems(0) @maxItems(32) features: FeatureName[];
  /** Account pages, where users sign up and create API keys. */
  account_url: HttpsUrl;
  @minItems(1) @maxItems(2) artifact_formats: ("fits" | "xisf")[];

  /**
   * Hosts this server fetches externally shared files from by itself. Absent means
   * maintainers retrieve them by hand.
   */
  @minItems(1) @maxItems(32) external_retrieval_hosts?: Hostname[];
  limits: Limits;
}

@summary("Read server identity, limits and features")
@access("public")
@useAuth(NoAuth)
@tag("capabilities")
@get @route("/capabilities")
op getCapabilities(
  ...ConditionalRead
): Ok<Capabilities> | NotModified | Errors;
