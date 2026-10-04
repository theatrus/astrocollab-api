# AstroCollab protocol

Version: 0.1.0-draft.1. Status: draft, no implementation claimed.

The words MUST, MUST NOT, SHOULD and MAY express requirements of this proposed
contract. The [OpenAPI document](../openapi/astrocollab.yaml) defines structural
constraints; this document defines authorization and stateful semantics. Both
must agree. An implementation is not conformant merely because JSON validates.

## 1. Authority and discovery

Each project has one authoritative server. Its owner publishes required data;
participants offer capability and effort and submit calibrated subs. Servers
MUST NOT issue equipment commands, exclusive objective reservations or target
leases through this protocol. Intents are advisory. Local acquisition software
retains all safety checks, operator overrides and scheduling authority.

Users select an HTTPS API root. `GET /capabilities` is public and returns a
stable server UUID, canonical API root, account UI, OAuth issuer/metadata URL,
account resource URI, supported versions/features, and service limits. Verify
TLS and issuer metadata before authorizing; do not follow an issuer change
silently. Example domains in this repository are placeholders, not services.
The root may use a different path from `/v1`; all paths are relative to it.

The base profile includes projects, enrollment, offers, intent, sync and
authenticated uploads. `recommendations` is optional: unadvertised recommendation
routes return `404` with `unsupported_feature`. A client can plan locally.
Open signup, approval rules, moderation, recovery and billing are server UI
policies, not standardized password endpoints. A public API does not imply
anonymous access to private projects or images.

## 2. Identity, units and compatibility

Resource identity is `(server_id, resource_id)`. UUIDs are opaque and never
derived from names, coordinates, files or a local database row. A changed server
UUID requires explicit relinking. Equipment IDs belong to a participation.
Capture identity is `(origin_id, capture_id)`: an origin is a persistent producer
UUID retained when another client submits or recalibrates that same capture.
Digests detect identical bytes under different identities but do not prove
physical capture identity or ownership.

Coordinates are ICRS, with RA in degrees [0, 360), Dec in degrees [-90, 90].
Footprints are tangent-plane rectangles centered on those coordinates: width
follows east-west at position angle zero, height north-south; position angle
rotates the height axis east of celestial north. Angles are degrees, sampling
arcseconds/pixel, wavelength nanometers, focal length/pixel size explicitly
millimeters/micrometers, durations seconds and byte counts octets. Timestamps
are RFC 3339 UTC with `Z`; calendar budget months use an IANA timezone. Unknown
values are absent unless a schema explicitly permits null. They are never zero.

Major incompatibilities require a new API version. Clients MUST ignore unknown
response fields but MUST NOT ignore unsupported `required_features` or new
enum values in executable planning requirements. Requests reject unknown fields;
use the `extensions` object for namespaced, optional metadata. Extensions never
carry mandatory semantics, code, commands or filesystem paths. Schema validation
does not verify unique IDs within arrays, foreign keys, time ordering or physical
feasibility; implementations MUST check those before accepting a resource.

## 3. Account authorization and project tokens

Use OAuth authorization code with PKCE S256 and an external browser for desktop
clients. Servers MUST support that flow and SHOULD advertise device authorization
for headless clients. Public clients have no embedded secret. Servers document
client registration; dynamic registration is optional. Standard OAuth endpoints
are described by issuer metadata, not redefined in OpenAPI. The `resource`
parameter identifies the capability document's account resource. Validate the
returned token audience at the resource server. Refresh-token rotation with
reuse detection or equivalent sender constraints is required for public clients.

Account tokens authorize discovery, membership management, sync of the account's
selected projects and creation of projects. They do not authorize artifact
uploads. `POST /participations/{id}/tokens` uses an account token to issue a
short-lived participation bearer token after checking an active membership.
It is a resource-server delegation endpoint, not a new OAuth grant type. It
cannot request another client identity or expand the parent authorization.

A participation token is bound to the issuing server audience, account, client,
participation, project and approved scopes. Maximum lifetime is 900 seconds;
it has no refresh token. The caller renews via the account-authorized delegation
endpoint. Return its nonsecret token ID and expiry; list/revoke those IDs without
ever returning an existing secret. Distinct applications obtain distinct tokens.
Servers MAY use opaque tokens or signed tokens; clients MUST treat them as opaque.
Current membership and revocation MUST be checked on every protected request.

| Context | Scopes |
| --- | --- |
| Account | `account:read`, `participation:manage`, `project:create` |
| Participant project token | `project:read`, `offer:write`, `intent:write`, `status:write`, `submission:write`, `submission:read-own` |
| Maintainer project token | Participant scopes plus `project:manage`, `participation:review`, `assessment:write`, `submission:read-all` |

OpenAPI's `x-token-context` and `x-required-scopes` are normative annotations
beside its HTTP bearer scheme. A scope never substitutes for ownership checks.
When present, `x-scope-rules` specifies alternatives by token context or job kind
instead of the default `x-required-scopes`: each inner list is an AND, alternative
lists are an OR. In particular assessment-job reads require submission-read
permission, not merely project-read permission.
`submission:read-all` permits metadata review, not an artifact download grant;
public downloads and distributing project datasets are outside this draft.
Maintainers cannot manufacture a participation token for someone else's account.

Equipment, capacity, planning policy, intent and check-in resources are private
to the authenticated participation; a project-read scope alone does not expose
other volunteers' offers or precise sites. Maintainers can review membership
records and submissions only through their explicit review/read-all scopes.
Project summaries, requirements and aggregate progress may be public when the
project is public; activity is member-only and individually opted in. Server
planning may use private offers without publishing those inputs to other users.

Tokens belong in credential stores and authorization headers, never URLs,
manifests, logs or status. HTTPS is required. Cross-origin redirects MUST NOT
forward bearer tokens. Browser services must apply origin/CSRF controls to their
own cookie-authenticated UI; this API uses bearer authorization.

References: [OAuth security BCP](https://www.rfc-editor.org/rfc/rfc9700.html),
[native applications](https://www.rfc-editor.org/rfc/rfc8252.html),
[device authorization](https://www.rfc-editor.org/rfc/rfc8628.html),
[resource indicators](https://www.rfc-editor.org/rfc/rfc8707.html).

## 4. Projects and published requirements

Creating a project atomically creates its draft and the caller's active owner
participation. The creation payload contains the initial complete requirements;
the owner accepts their terms through that creation. The owner can immediately
obtain a project token. A draft is a
complete document replaced with `PUT`; the draft ETag is required in `If-Match`.
Publish uses that draft ETag and creates the next immutable project revision in
one transaction. It never mutates an already published revision. The first
publication makes a project discoverable according to its visibility. Drafts
are visible only to maintainers. The project summary's `current_revision` is
null before first publication; zero is never a published revision.

Requirements name stable targets, objectives, processing groups and terms.
An objective includes geometry/coverage, bandpass, sampling, exposure purpose,
allowed duration, count and/or integration goal, calibration steps, color state
and quality rules. Both goals must be met when count and integration are present.
Measurements specify method/version/unit, limits and whether evidence is required.
Required missing evidence must prevent acceptance; optional missing evidence
must not be scored as a failure. A header or predicted coordinate is not pixel
evidence. `fresh_pixel_solve` requires a solve of the submitted pixels.

Processing groups define compatible sampling and color/registration/calibration
states. Different instruments do not automatically yield interchangeable depth.
An assessment can credit a capture to several explicitly covered objectives,
but project totals count its distinct identity and exposure duration only once.
Count/integration credit per objective is also unique by origin capture.

For mosaics, publish separate target footprints and objectives for the coverage
units whose depth must be achieved. Recommendations may cover several units in
one wide frame or use multiple overlapping panels. A narrow frame that cannot
meet an objective's minimum coverage cannot fulfill that objective by accumulating
unrelated hours. Recommendations never invent new acceptance units: changing
the project's coverage decomposition requires a published requirement revision.

Project lifecycle is `draft -> open -> paused|closed`; an owner may reopen a
paused or closed project by publishing a new open revision. Closing prevents new
enrollment and recommendations. A published revision carries a contribution
window: captures must start before `capture_deadline`, and finalization must
occur before `submission_deadline`; both are exclusive bounds. A superseding
revision does not retroactively erase that window or its acceptance policy.
Membership revocation and storage/security restrictions still apply. A server
must disclose those restrictions before enrollment.

`surplus_policy` is `retain_and_attribute` or `reject_excess`. In the latter
case excess receives reason `goal_already_met`, never a quality-failure label.
Order concurrent credit decisions by their transaction's server event sequence.
Participation and intent do not confer priority in that order. Recalibration
replacement is evaluated against the credit it replaces, not treated as a new
race for the same slot.

Terms have a stable ID/version, digest and immutable HTTPS content URL. Each
publication references the current terms. Joining or renewing consent records
the exact version. Finalization requires current terms consent even if the
capture references an older eligible revision. A terms change leaves bytes
staged and returns `terms_consent_required`; it cannot silently consent for the
user. Data ownership, license, attribution, deletion, retention and withdrawals
must be specified in the terms, not assumed by the protocol.

## 5. Participation, equipment and monthly capacity

One participation per account/project is active or historical. Repeated enrollment
returns that participation, never another credit identity. Open enrollment enters
`active`; approval-required enrollment enters `requested`. Only maintainers can
approve or revoke. A participant can pause, resume a self-paused participation,
withdraw or renew terms consent. A withdrawal can be re-requested and follows
current enrollment policy. A revoked participant cannot self-reactivate; a
maintainer may restore it to `requested`, then approve. Every transition checks
the participation ETag. The final active owner cannot withdraw, be revoked or
lose owner role until another owner exists. Role transfer is outside this draft.

Paused, withdrawn and revoked memberships cannot issue tokens or mutate project
resources. Account-level reads still expose their own membership state so clients
can disconnect. On observing inactive membership, local clients stop starting
new collaboration work at safe boundaries. Offline clients cannot promise
immediate revocation and must bound cached plans by reviewed local validity.

Equipment offers declare physical geometry, supported filters and rotation,
with optionally disclosed approximate site data. A new offer revision cannot
silently change an active local configuration. The server retains referenced
offer revisions so a submission's equipment provenance can be resolved later.
The current representation supports conditional reads/writes; immutable revisions
have a separate read route. Each filter has a local stable ID plus physical
bandpass; names such as `Ha` are not sufficient compatibility evidence.

Capacity is a versioned offer for a specific `YYYY-MM` and timezone. It states
rig-seconds available to this project, allowed equipment, UTC availability
intervals and `soft` or `local_hard` mode. The user interface may display hours.
Exposures plus acquisition overhead consume attempted rig-time; weather idle,
upload and processing time do not. Two rigs running for an hour use two rig-hours.
Split usage at local month boundaries; no automatic rollover. Accepted integration
is separate from attempted effort. Capacity usage is a reported estimate, not
server-certified observation or a remote hard-stop command.

A participant client MUST reconcile shares against its overall local budget
across projects and servers. The server only knows the share offered to it.
Availability timestamps must fall within the named local month; equipment IDs
must belong to this participation. `local_hard` means the local scheduler will
avoid new work beyond its budget and still complete safety actions. No server
can enforce a global hard cap across disconnected clients.

## 6. Recommendations, intent and live status

### Automatic framing after setup

Equipment registration and periodic check-in are planning triggers, not just
telemetry. `PUT /participations/{id}/equipment/{equipment_id}` returns the saved
offer plus planning advice. `POST /participations/{id}/checkins` reports current
equipment/capacity revisions, the last adopted recommendation and local progress;
it returns advice and a bounded next-check-in interval. A server may queue a new
recommendation when registration or check-in shows that useful coverage changed.
The same request retry must not queue duplicate work. Servers without the
recommendation feature return `unavailable` advice, while local planning works.

`PUT /participations/{id}/planning-policy` records the participant's reviewed
policy: `suggest_only` or `automatic`, allowed equipment revisions, sky regions,
filters, exposure range, minimum overlap, maximum panels and rotation behavior.
The policy also names the reviewed terms version and bounds cached plan validity.
Approved sky regions contain target IDs and explicit center bounds, so moving a
target under the same ID cannot silently authorize a new area of sky. Suggested
panel centers must lie inside those bounds; overlap and objective coverage still
need separate geometric validation.
The capacity offer supplies the effort budget. `offer:write` permits changes,
but clients MUST require local user consent to enable or expand automatic mode.
Remote policy storage records that consent; it cannot create local authority.

In automatic mode, compatible recommendations can replace future local intent
at safe boundaries without prompting on every panel or target. A client can
register equipment once, check in during normal operation, and keep collecting
the coverage the project currently needs. Check-ins can recommend keeping the
current plan, a different field center, another mosaic panel, waiting for useful
work, or review when a proposed change exceeds the policy. An outstanding intent
does not entitle that participant to the old deficit or suppress other volunteers.

Recommendations name the planning-policy revision, demand watermark and expiry,
and declare `automatic_eligible` with reasons. Clients MUST independently verify
all bounds against their saved consent and live setup; a server flag alone cannot
activate work. Fixed-camera policies preserve the confirmed angle within its
tolerance; a manual rotation always requires review. Changed equipment, terms,
unapproved regions/bandpasses or a larger time commitment also require review.
Automatically adopted framing MUST retain the recommended panel/objective IDs
and revision provenance. It never rewrites in-flight captures or credit history.

While offline, use only a still-valid cached plan under local policy. Demand
staleness and duplicate coverage are expected; there is no exclusive assignment
or requirement for an online server round trip before every exposure. Expiry
stops starting new collaboration work at a safe boundary. Reconnection can
refresh framing; meaningful review requirements are surfaced, routine updates
need no notification. Automatic framing does not imply automatic uploads;
upload consent is a separate local policy.

### Recommendation results and advisory progress

Recommendation jobs name exact project, equipment and capacity revisions.
Results include alternative panel geometries, objective mappings, exposure
recipes, expected rig-time/yield, assumptions, unmet objectives and expiration.
Wide-field coverage may use one panel; another rig may use several or contribute
only a compatible subregion. Minimum overlap is explicit. Geometry, fresh demand
and budget inform suggestions; local safety and feasibility always win.
Clients must validate physical feasibility before activating a proposal.

Intents identify planned objectives/panels and equipment, estimated effort and
expiry. They are revocable, nonexclusive statements, never reservations. Expiry
removes an intent from forecast demand, not historical provenance. Publishing
two overlapping intents must succeed if each otherwise validates. A stale intent
must never block another participant from working or submitting.

Status is optional and keyed by participation and a stable writer UUID bound
to the authenticated client. Sequence numbers increase across restarts; to
reset, create a new writer ID. A lower sequence is ignored with `applied: false`;
equal sequence/body is a retry, equal sequence/different body conflicts. Expiry
and observation time make stale status visible. Backfilled capture records do
not overwrite fresh live activity. Status, intents and pending uploads never
create accepted progress. Activity is private by default in client UI; sending
`visibility: project` explicitly permits project members to see that status.
`GET /projects/{id}/activity` returns the caller's own status and other members'
explicitly shared status. It never serves anonymous live activity. Revoke
membership to remove that member's shared live visibility.

## 7. HTTP concurrency, errors and limits

All requests/responses use JSON except upload chunks. Errors use
[`application/problem+json`](https://www.rfc-editor.org/rfc/rfc9457.html) with a
stable `code`, HTTP `status`, safe detail, request ID and optional field errors.
Do not leak resource existence across unauthorized projects; use `404` there.

Conditional reads use strong ETags. Existing mutable resources require exactly
one strong `If-Match` ETag; missing preconditions return `428`, mismatch `412`.
For create-or-replace offer/intent resources use exactly one of `If-Match` or
`If-None-Match: *`. Both supplied returns `400`; nonexistent resource plus
`If-Match`, or existing resource plus `If-None-Match: *`, returns `412`.
Draft publication matches the **draft's** ETag, not the project summary's.

POST mutations require a UUID `Idempotency-Key`. Retain results for at least
the advertised window, minimum 24 hours. Scope keys by server/account/client,
method and concrete path. Compare a canonical JSON body digest (RFC 8785) and
semantic preconditions. After authenticating and checking current permission,
return an identical replay's original response before evaluating now-stale
preconditions. Changed request under the key returns `409 idempotency_conflict`.
After retention, stable resource/capture IDs still prevent duplicate enrollment,
submission and credit. Clients should inspect resulting state before retrying
an expired key for other mutations. Retry-safe PUTs require the same precondition
rules; after a lost response, read and compare current content to reconcile.

Lists use opaque cursors and `limit` (1–100, default 50). Ordinary lists provide
no cross-page transaction snapshot; sync snapshots do. Invalid cursors return
`400`, expired cursors `410 cursor_expired`. Computation returns `202` and a job
with polling hint; completed jobs retain a typed result. Errors distinguish
`401` authentication, `403` permission, `409` conflict, `413` byte limits,
`422` semantic incompatibility, `429` quota/rate limit with `Retry-After` and
`503` temporary unavailability. A job failure contains a typed problem.

Servers bound JSON size, object count, chunk bytes, decoded pixels, storage and
compute. Enforce limits on actual decoded input, not only its declarations.
No arbitrary remote URL fetches, client paths or executable metadata are allowed.
Links supplied by the server must be HTTPS and clients must not forward secrets
across origins. Clients keep raw files and sanitize disclosed metadata in export
derivatives according to the user's consent.

## 8. Consistent sync

Account-authorized `POST /sync/snapshots` selects explicit project IDs and returns
a stable snapshot ID and first page. The server verifies membership and freezes
a point-in-time view with a change watermark. Snapshot entries are typed
resources: project summary, published project revision, own participation,
progress and own assessments. Local offers/intents remain locally owned and can
be reconciled through their resource reads. This is project sync, not catalog
replication or arbitrary account export.

`GET /sync/snapshots/{id}?cursor=...` pages that same view. Only the final page
returns a `changes_cursor` positioned immediately after the snapshot watermark.
Clients stage pages and atomically install the complete snapshot plus cursor.
Then `GET /changes?cursor=...` returns ordered upsert/remove envelopes for the
same selection; a batch and its `next_cursor` must be committed together.
An empty batch still returns a valid cursor and polling hint. Repeated events
must be harmless; never order by the client's wall clock.

Snapshot/cursor ownership is account/client/selection-bound. Selection changes
require a fresh snapshot. Expired cursors require rebootstrap, not guessing at
timestamps. Authorization is rechecked on every page. A membership lost during
bootstrap returns `409 snapshot_invalidated`; discard staged pages and rebuild
from current memberships. A live feed emits `remove project_access` for revoked
or withdrawn membership without disclosing further private content. The feed
also removes access for paused membership and may retain that tombstone while
membership is inactive. A resume requires a new
snapshot. Do not restore private cached content merely because a tombstone was
missed; any authorization failure freezes it pending reconciliation.

A project update stages a local plan revision; it never replaces an active
acquisition program automatically. Every submitted artifact names the immutable
requirements under which it was acquired. Removal of access preserves local
captures and provenance; remote disclosure permissions no longer apply.

## 9. Calibrated artifacts and resumable transfer

A submission is an immutable manifest with a client UUID, participation,
project revision, objective references and one or more artifacts. Each artifact
names the origin capture, SHA-256 digests of raw and calibrated bytes, equipment
revision, exposure/bandpass/time, exact processing state and calibration history.
Calibration includes masters by role/digest, operations/parameters and software
versions. A digest is not an instruction to upload a raw or master file.
Measured evidence includes method, version, unit, value and observation time.
Fresh solves carry the submitted artifact digest; embedded WCS stays advisory.

Baseline transfer is authenticated fixed-size parts through this API, allowing
independent clients to interoperate without a storage-vendor SDK. Creating a
submission returns one upload session per artifact with its total size, server
chosen part size and count, expiry and received-part inventory. All nonfinal
parts have exactly `part_size_bytes`; the last has the remaining bytes. Parts
are numbered from 1 and may arrive out of order.

`PUT /uploads/{id}/parts/{number}` sends binary bytes, `Content-Length` and
`X-Part-SHA256` (lowercase hex). Verify the actual byte count and digest before
acknowledging. Identical repeats return the same receipt; changed bytes for an
already acknowledged part return `409 part_conflict`. Part receipt is atomic.
`GET /uploads/{id}` allows resumption. Renewal extends the same session after
checking current authority and quotas; it does not change manifest or part size.
Expired sessions cannot accept parts until renewed. Uncommitted bytes are retained
for the advertised staging window; after cleanup, renewal returns an empty part
inventory for the same artifact. A finalizing or finalized manifest cannot be
modified or renewed.

Finalization validates part completeness and creates one integrity/assessment
job atomically. Whole-object hashing, decoding and assessment run asynchronously.
It returns `202` with that same job on retry, including
after completion; `GET /submissions/{id}` shows current results. Missing parts
return `409 upload_incomplete`; an assembled digest mismatch rejects that
artifact with reason `digest_mismatch` and never credit. Other valid artifacts
in the batch continue through assessment. A rejected corrupt artifact requires a
new artifact ID in a new manifest, retaining the origin capture identity.
Creating a duplicate submission UUID and identical manifest returns its existing
representation; different content conflicts regardless of idempotency retention.

The server checks current membership, terms consent, revision window and quotas
at creation, renewal and finalization. Revocation stops new part writes too;
this draft does not issue presigned storage URLs with independent lifetimes.
Already finalized work may finish assessment under its recorded consent; newly
revoked access cannot fetch its private results through a project token.

Artifact states are `uploading -> received -> validating -> accepted|rejected`.
Bad part digests return `422 digest_mismatch` without recording that part;
the client can retry correct bytes. Whole-object corruption fails in validation.
An accepted artifact becomes `superseded` only when a replacement is accepted.
Submission state is `uploading`, `processing` or `complete`; per-artifact state
reports partial outcomes. No bulk result may hide individual rejections.

Server-produced assessments and authorized human assessments are appended with
an ID, policy revision, evidence, decision, reasons and per-objective credit.
Manual assessment writes use the submission ETag to prevent concurrent lost
updates. They can decide only verified, validated artifacts. Reject decisions
have no credit. Accept decisions must pass required evidence and compatibility
checks; permission alone cannot bypass the published requirement semantics.

A recalibrated derivative declares `supersedes_artifact_id` and the same origin
capture. On acceptance, atomically replace old credit; rejecting a replacement
leaves prior accepted credit intact. Reassessment of an existing artifact appends
history and may reverse a prior acceptance; subtract credit once. The server
enforces uniqueness per project/objective/origin capture and rejects conflicting
duplicate submissions pending attribution review. Never reveal another user's
private matching manifest to resolve a digest collision.

Progress is a projection at a server event sequence. Requested goals, fresh
intent, self-reported capture, received-pending, accepted, rejected and surplus
are separate. Surplus may be accepted/attributed but is excluded from required
completion credit. Project distinct integration is a union of credited capture
identities, not the sum of overlapping objective totals. No claim of exactly-once
network delivery is needed; transactional deduplication supplies single credit.

## 10. Scope and unresolved deployment choices

This draft does not define hardware control, centralized scheduling, federation,
raw-image ingestion, dataset downloads, payments or a mandatory quality algorithm.
Services choose signup moderation, storage backends, quotas, licensed terms,
assessment methods and retention periods and advertise their applicable policy.
The protocol defines how clients discover those constraints and report evidence.

Before declaring v1 stable, exercise the conformance scenarios with two independent
clients and a server; review quality metric identifiers, spherical coverage edge
cases and device-flow interoperability; and decide whether artifact distribution
belongs in v1 or a separate optional profile. Schema tests in this repository
are necessary tooling, not that interoperability evidence.
