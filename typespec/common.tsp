// Shared types, headers and responses. The build (tools/build_openapi.py)
// closes every model: servers reject unknown fields.
using Http;
using OpenAPI;

namespace AstroCollab;

@format("uuid") scalar Uuid extends string;

/** RFC 3339 UTC timestamp ending in `Z`. */
@format("date-time") @pattern("Z$") scalar Timestamp extends string;

@minLength(1) @maxLength(2000) scalar Text extends string;
@pattern("^https://") scalar HttpsUrl extends url;

/** HTTPS, or plain HTTP on a loopback address for local development. */
@pattern("^(https://|http://(127\\.0\\.0\\.1|localhost|\\[::1\\])(:[0-9]+)?(/|$))") scalar ServiceUrl extends url;
@pattern("^[0-9a-f]{64}$") scalar Sha256 extends string;
@pattern("^[a-z][a-z0-9_.-]{0,63}$") scalar FeatureName extends string;
@pattern("^[a-z][a-z0-9_]+$") scalar Code extends string;
@pattern("^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$") scalar Hostname extends string;

model Paging {
  @query @minLength(1) @maxLength(4096) cursor?: string;
  @query @minValue(1) @maxValue(100) limit?: integer = 50;
}

// Responses.

/** Success. */
model Ok<T> {
  @statusCode _: 200;
  @body body: T;
}

/** Already created with this `id` and body; returned unchanged. */
model Existing<T> {
  @statusCode _: 200;
  @body body: T;
}

/** Created */
model Created<T> {
  @statusCode _: 201;
  @body body: T;
}

/** Accepted; read the resource again later. */
model Accepted<T> {
  @statusCode _: 202;
  @body body: T;
}

/** Error; see `code`. */
@error
model ProblemResponse {
  @header contentType: "application/problem+json";
  @body body: Problem;
}

/** Conflict with the resource's state, such as `upload_incomplete` or `id_conflict`. */
@error
model ConflictProblem {
  @statusCode _: 409;
  @header contentType: "application/problem+json";
  @body body: Problem;
}

/** Too many requests. Retry after the stated delay. */
@error
model RateLimited {
  @statusCode _: 429;
  @header("Retry-After") @minValue(1) retryAfter: integer;
  @header contentType: "application/problem+json";
  @body body: Problem;
}

alias Errors = ProblemResponse | RateLimited;
alias WriteErrors = Errors | ConflictProblem;
