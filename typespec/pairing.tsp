using Http;
using OpenAPI;

namespace AstroCollab;

union Scope {
  "account:read",
  "participation:manage",
  "project:create",
  "project:read",
  "offer:write",
  "intent:write",
  "status:write",
  "submission:write",
  "submission:read-own",
  "project:manage",
  "participation:review",
  "assessment:write",
  "submission:read-all",
}

/** Trade a pairing code from the account pages for an API key. */
model PairRequest {
  /** Single-use code the user issued on the account pages. */
  @minLength(16) @maxLength(256) pairing_code: string;

  /** Persistent ID of this client installation. Pairing again replaces its old key. */
  installation_id: Uuid;

  /** Name shown on the account pages, such as "Roof rig 2". */
  @minLength(1) @maxLength(80) client_name: string;
}

/** A new API key. The secret appears only in this response. */
model PairedKey {
  key_id: Uuid;

  /** Send as `Authorization: Bearer <api_key>`. Store it in a credential store. */
  @minLength(32) @maxLength(512) api_key: string;

  account_id: Uuid;
  @minLength(1) @maxLength(80) client_name: string;
  installation_id: Uuid;
  @extension("uniqueItems", true) @minItems(1) @maxItems(13) scopes: Scope[];

  /** Present when the user limited the key to these projects. */
  @extension("uniqueItems", true) @minItems(1) @maxItems(100) project_ids?: Uuid[];

  /** Present when the user set an expiry. */
  expires_at?: Timestamp;
}

/** Sent once; never cache. */
model KeyCreated {
  @statusCode _: 201;
  @header("Cache-Control") cacheControl: "no-store";
  @body body: PairedKey;
}

/**
 * Takes no Authorization header and no Idempotency-Key. A code works once and expires
 * within an hour. An unknown, used or expired code returns 401 `invalid_pairing_code`.
 * Do not retry automatically; if the response is lost, issue a new code.
 */
@summary("Pair a client and receive its API key")
@access("public")
@useAuth(NoAuth)
@tag("pairing")
@post @route("/pair")
op pairClient(@body body: PairRequest): KeyCreated | Errors;
