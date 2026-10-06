// Shared types and responses.
using Http;
using OpenAPI;

namespace AstroCollab;

/** A server-made identifier: 12 lowercase hexadecimal characters. */
@pattern("^[0-9a-f]{12}$") scalar Id extends string;

/** Seconds since 1970-01-01 UTC, as a number. */
@minValue(0) scalar UnixTime extends float64;

/** A night as the rig names it, such as `2026-10-05`. */
@maxLength(16) scalar NightName extends string;

/** Success. */
model Ok<T> {
  @statusCode _: 200;
  @body body: T;
}

/** An error. `detail` says what went wrong, in words to show the operator. */
model ErrorBody {
  detail: string;
}

/** A request body that does not match its type. Each entry names a field and the problem. */
model ValidationErrorBody {
  detail: {
    loc: (string | int32)[];
    msg: string;
    type: string;
  }[];
}

/** The request cannot be served as asked yet, such as joining before describing the rig. */
@error
model BadRequest {
  @statusCode _: 400;
  @body body: ErrorBody;
}

/** Missing, unknown or revoked token. */
@error
model Unauthorized {
  @statusCode _: 401;
  @body body: ErrorBody;
}

/** The caller may not do this. */
@error
model Forbidden {
  @statusCode _: 403;
  @body body: ErrorBody;
}

/** No such thing, or not this caller's. */
@error
model NotFound {
  @statusCode _: 404;
  @body body: ErrorBody;
}

/** Conflicts with how things stand, such as a rig that cannot meet a project. */
@error
model Conflict {
  @statusCode _: 409;
  @body body: ErrorBody;
}

/** The body does not match its type. */
@error
model Invalid {
  @statusCode _: 422;
  @body body: ValidationErrorBody;
}

/** Not available on this server, such as sign-in when it is not set up. */
@error
model Unavailable {
  @statusCode _: 503;
  @body body: ErrorBody;
}

/** Too many tries, such as repeated bad pairing codes. */
@error
model TooManyRequests {
  @statusCode _: 429;
  @body body: ErrorBody;
}

alias Errors = Unauthorized | Invalid;
