using Http;
using OpenAPI;

namespace AstroCollab;

model Participation {
  id: Uuid;
  project_id: Uuid;
  account_id: Uuid;
  role: "owner" | "maintainer" | "contributor";
  state: "requested" | "active" | "paused" | "withdrawn" | "revoked";
  @minValue(1) revision: integer;
  accepted_terms?: Terms;
  updated_at: Timestamp;
}

model JoinRequest {
  accepted_terms: Terms;
}

@extension("allOf", #[
  #{
    `if`: #{properties: #{action: #{`enum`: #["accept_terms", "rerequest"]}}},
    then: #{required: #["accepted_terms"]}
  }
])
model ParticipationAction {
  action: "pause" | "resume" | "withdraw" | "rerequest" | "accept_terms";
  accepted_terms?: Terms;
}

model ParticipationReview {
  action: "approve" | "revoke" | "restore_request";
  reason: Text;
}

model ParticipationPage {
  @minItems(0) @maxItems(100) items: Participation[];
  @maxLength(4096) next_cursor: string | null;
}

@summary("List own memberships, including revoked memberships")
@access("account", "account:read")
@tag("me")
@get @route("/me/participations")
op listMyParticipations(
  ...Paging
): Ok<ParticipationPage> | Errors;

/**
 * A key with account:read may read its own participation. A maintainer's key with
 * participation:review may read memberships of its project, including the ETag for review.
 */
@summary("Read own participation or a project membership for review")
@access("account_or_project", "account:read")
@extension("x-scope-rules", #{account: #[#["account:read"]], project: #[#["participation:review"]]})
@tag("participations")
@get @route("/participations/{participation_id}")
op getParticipation(
  @path participation_id: Uuid,
  ...ConditionalRead
): Ok<Participation> | NotModified | Errors;

@summary("Change own state or renew terms consent")
@access("account", "participation:manage")
@tag("participations")
@post @route("/participations/{participation_id}/actions")
op changeParticipation(
  @path participation_id: Uuid,
  ...IdempotencyKey,
  ...IfMatch,
  @body body: ParticipationAction
): Ok<Participation> | ConditionalWriteErrors;

@summary("Approve, revoke or restore requested membership")
@access("project", "participation:review")
@tag("participations")
@post @route("/participations/{participation_id}/review")
op reviewParticipation(
  @path participation_id: Uuid,
  ...IdempotencyKey,
  ...IfMatch,
  @body body: ParticipationReview
): Ok<Participation> | ConditionalWriteErrors;
