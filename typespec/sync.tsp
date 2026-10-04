using Http;
using OpenAPI;

namespace AstroCollab;

@oneOf
union SyncResource {
  SyncResourceProject,
  SyncResourceProjectRevision,
  SyncResourceParticipation,
  SyncResourceProgress,
  SyncResourceAssessment,
}

model SyncResourceProject {
  kind: "project";
  resource: Project;
}

model SyncResourceProjectRevision {
  kind: "project_revision";
  resource: ProjectRevision;
}

model SyncResourceParticipation {
  kind: "participation";
  resource: Participation;
}

model SyncResourceProgress {
  kind: "progress";
  resource: Progress;
}

model SyncResourceAssessment {
  kind: "assessment";
  resource: Assessment;
}

model SnapshotRequest {
  @extension("uniqueItems", true) @minItems(1) @maxItems(100) project_ids: Uuid[];
}

@extension("allOf", #[
  #{
    `if`: #{properties: #{next_page_cursor: #{type: "null"}}},
    then: #{required: #["changes_cursor"]},
    `else`: #{not: #{required: #["changes_cursor"]}}
  }
])
model SnapshotPage {
  snapshot_id: Uuid;
  expires_at: Timestamp;
  @minItems(0) @maxItems(100) items: SyncResource[];
  @maxLength(4096) next_page_cursor: string | null;
  @minLength(1) @maxLength(4096) changes_cursor?: string;
}

@oneOf
union Change {
  ChangeUpsert,
  ChangeRemove,
}

model ChangeUpsert {
  @minValue(1) sequence: integer;
  operation: "upsert";
  entry: SyncResource;
}

model ChangeRemove {
  @minValue(1) sequence: integer;
  operation: "remove";
  kind: "project" | "project_revision" | "participation" | "progress" | "assessment" | "project_access";
  project_id: Uuid;
  resource_id: Uuid;
}

model ChangePage {
  @minItems(0) @maxItems(100) items: Change[];
  @minLength(1) @maxLength(4096) next_cursor: string;
  @minValue(1) @maxValue(3600) poll_after_seconds: integer;
}

@summary("Create a consistent snapshot of selected projects")
@access("account", "account:read")
@tag("sync")
@post @route("/sync/snapshots")
op createSnapshot(
  ...IdempotencyKey,
  @body body: SnapshotRequest
): Created<SnapshotPage> | WriteErrors;

@summary("Read another page of a fixed snapshot")
@access("account", "account:read")
@tag("sync")
@get @route("/sync/snapshots/{snapshot_id}")
op getSnapshotPage(
  @path snapshot_id: Uuid,
  ...Paging
): Ok<SnapshotPage> | Errors;

@summary("Read ordered changes after a snapshot watermark")
@access("account", "account:read")
@tag("changes")
@get @route("/changes")
op getChanges(
  ...ChangesCursor,
  ...Limit
): Ok<ChangePage> | Errors;
