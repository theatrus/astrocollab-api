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
@pattern("^[0-9a-f]{64}$") scalar Sha256 extends string;
@pattern("^[a-z][a-z0-9_.-]{0,63}$") scalar FeatureName extends string;
@pattern("^[a-z][a-z0-9_]+$") scalar Code extends string;
@pattern("^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$") scalar Hostname extends string;

// Request headers and query parameters.

model IfMatch {
  /** Exactly one strong ETag for the mutable resource; publication uses the draft ETag. */
  @header("If-Match") @minLength(3) @maxLength(200) ifMatch: string;
}

/** Create with `If-None-Match: *`, or replace with `If-Match`. Send exactly one. */
model CreateOrReplace {
  /** Supply exactly one of If-Match or If-None-Match. */
  @header("If-Match") @minLength(3) @maxLength(200) ifMatch?: string;

  /** Create only. Supply exactly one of If-Match or If-None-Match. */
  @header("If-None-Match") ifNoneMatch?: "*";
}

model ConditionalRead {
  @header("If-None-Match") @minLength(3) @maxLength(200) ifNoneMatch?: string;
}

model IdempotencyKey {
  @header("Idempotency-Key") idempotencyKey: Uuid;
}

model Paging {
  @query @minLength(1) @maxLength(4096) cursor?: string;
  ...Limit;
}

model Limit {
  @query @minValue(1) @maxValue(100) limit?: integer = 50;
}

model RevisionQuery {
  @query @minValue(1) revision: integer;
}

model ChangesCursor {
  @query @minLength(1) @maxLength(4096) cursor: string;
}

// Responses. Each success carries the representation's strong ETag.

/** Success */
model Ok<T> {
  @statusCode _: 200;
  @header("ETag") etag?: string;
  @body body: T;
}

/** Success */
model Created<T> {
  @statusCode _: 201;
  @header("ETag") etag?: string;
  @body body: T;
}

/** Accepted; poll the returned job. */
model Accepted<T> {
  @statusCode _: 202;
  @header("ETag") etag?: string;
  @body body: T;
}

/** Unchanged; no response body. */
model NotModified {
  @statusCode _: 304;
  @header("ETag") etag?: string;
}

/** Protocol error; see status, stable code and request_id. */
@error
model ProblemResponse {
  @header contentType: "application/problem+json";
  @body body: Problem;
}

/** Protocol error; see status, stable code and request_id. */
@error
model StatusProblem<Status> {
  @statusCode _: Status;
  @header contentType: "application/problem+json";
  @body body: Problem;
}

/** Quota or rate limit. Retry after the stated delay. */
@error
model RateLimited {
  @statusCode _: 429;
  @header("Retry-After") @minValue(1) retryAfter: integer;
  @header contentType: "application/problem+json";
  @body body: Problem;
}

alias Errors = ProblemResponse | RateLimited;

/** Mutations can fail on state (409). */
alias WriteErrors = Errors | StatusProblem<409>;

/** Conditional mutations can also fail on a stale (412) or missing (428) `If-Match`. */
alias ConditionalWriteErrors = WriteErrors | StatusProblem<412> | StatusProblem<428>;
