# AstroCollab protocol

Version: 0.1.0-draft.1. Draft specification; no implementation yet.

MUST and MUST NOT mark requirements. SHOULD marks a recommendation; MAY marks
an option. [OpenAPI](../openapi/astrocollab.yaml) defines payload structure.
This document defines behavior that schema validation cannot check.

## 1. Authority and discovery

Each project has one server. The owner publishes frame requirements.
Participants register equipment, offer time and submit calibrated exposures.
The local acquisition system controls equipment, scheduling and safety.
Servers MUST NOT issue equipment commands or reserve targets through this API.

Clients start with an HTTPS API root and call public `GET /capabilities`. The response
provides the server UUID, API root, account UI, OAuth metadata, account resource
URI, supported versions, features and limits. Clients verify TLS and the OAuth
issuer before authorization. An issuer change requires user review. All routes
are relative to the API root, which need not use `/v1`.

Servers support projects, enrollment, equipment and capacity offers, intent,
sync and authenticated uploads. Recommendations are optional. An unsupported
recommendation route returns `404 unsupported_feature`; clients can plan locally.
Servers manage signup, approval, moderation, recovery and billing through their
own UI. Private resources require authentication. Example domains are not live
services.

## 2. Identity, units and compatibility

Identify resources by `(server_id, resource_id)`. Use opaque UUIDs, not names,
coordinates, paths or database row IDs. A server UUID change requires explicit
relinking. Equipment IDs belong to a participation.

Identify captures by `(origin_id, capture_id)`. The producer assigns a persistent
origin UUID. Clients retain both IDs when submitting or recalibrating a capture.
Hashes detect identical bytes; they do not establish ownership or capture identity.

| Quantity | Convention |
| --- | --- |
| Coordinates | ICRS; RA in degrees [0, 360), Dec in degrees [-90, 90]. |
| Footprint | Tangent-plane rectangle centered on the stated coordinates; width and height in degrees. At position angle zero, width runs east-west and height north-south. |
| Position angle | Degrees east of celestial north, measured along the height axis. |
| Sampling | Arcseconds per pixel. |
| Wavelength | Nanometers. |
| Focal length / pixel size | Millimeters / micrometers. |
| Duration / size | Seconds / bytes. |
| Timestamp | RFC 3339 UTC with `Z`. Budget months use an IANA timezone. |

Omit unknown values unless the schema permits null. Do not substitute zero.
Clients MUST ignore unknown response fields but MUST reject unsupported
`required_features` and unknown enum values in acquisition requirements.
Servers reject unknown request fields. Use `extensions` for optional namespaced
metadata; extensions cannot define required behavior, code, commands or paths.
Incompatible changes require a new API version.

Servers MUST check ID uniqueness, foreign keys, time ordering and physical
feasibility where JSON Schema cannot enforce them.

## 3. Account authorization and project tokens

Servers MUST support OAuth authorization code with PKCE S256. Desktop clients
use an external browser. Servers SHOULD also support device authorization for
headless clients. Public clients must not embed a secret. Servers document client
registration; dynamic registration is optional.

Issuer metadata defines the OAuth endpoints. The OAuth `resource` parameter
names the account resource from `/capabilities`. Servers validate token audiences.
Public clients require refresh-token rotation with reuse detection or equivalent
sender constraints.

Account tokens permit discovery, membership management, selected-project sync
and project creation. Uploads require a participation token. To issue one, the
client calls `POST /participations/{id}/tokens` with its account token. The server
checks active membership and cannot grant another client identity or permissions
beyond the account's authorization. This endpoint delegates access; it is not
an OAuth grant type.

Each participation token identifies the server audience, account, client,
participation, project and scopes. It expires within 900 seconds and has no
refresh token. Clients request a replacement through the same endpoint.
Servers return the secret only at issuance, with its expiry and nonsecret token
ID. Listing and revocation use that ID. Applications use separate tokens. Servers MAY sign tokens;
clients MUST treat them as opaque. Servers MUST check current membership and
revocation on every protected request.

| Context | Scopes |
| --- | --- |
| Account | `account:read`, `participation:manage`, `project:create` |
| Participant project token | `project:read`, `offer:write`, `intent:write`, `status:write`, `submission:write`, `submission:read-own` |
| Maintainer project token | Participant scopes plus `project:manage`, `participation:review`, `assessment:write`, `submission:read-all` |

OpenAPI's `x-token-context` and `x-required-scopes` define authorization for each
operation. `x-scope-rules`, when present, replaces the default scopes by token
context or job kind: inner lists use AND; alternative lists use OR. Servers also
check resource ownership. Assessment-job reads require submission-read permission.
`submission:read-all` permits metadata review, not file downloads. Maintainers
cannot issue tokens for another account.

Equipment, capacity, planning policy, intent and check-ins are private to their
participation. Maintainers need explicit review scopes to read memberships or
submissions. Public projects may expose summaries, requirements and aggregate
progress. Members see another participant's activity only if that participant
shares it. The server may use private offers to plan without disclosing them.

Clients store tokens in credential stores and send them in authorization headers.
Never put tokens in URLs, manifests, logs or status. Use HTTPS. Clients MUST NOT
forward bearer tokens across origins. Servers check origins and protect their
cookie-based UI against CSRF; API requests use bearer tokens.

References: [OAuth security](https://www.rfc-editor.org/rfc/rfc9700.html),
[native clients](https://www.rfc-editor.org/rfc/rfc8252.html),
[device authorization](https://www.rfc-editor.org/rfc/rfc8628.html),
[resource indicators](https://www.rfc-editor.org/rfc/rfc8707.html).

## 4. Projects and published requirements

Project creation atomically creates the initial draft and an active owner
participation. The request supplies complete requirements and accepts their
terms. The owner can then obtain a project token.

`PUT` replaces the draft using its ETag in `If-Match`. Publication checks that
same ETag and creates the next immutable revision in one transaction. Only
maintainers can read drafts. The first publication applies the project's
visibility setting. Before publication, `current_revision` is null, not zero.

Requirements assign stable IDs to targets, objectives, processing groups and
terms. Objectives specify coverage, bandpass, sampling, exposure purpose and
duration, frame count or integration goals, calibration, color state and quality.
When both count and integration goals exist, accepted data must meet both.
Quality rules specify method, version, unit, limits and required evidence.
Missing required evidence prevents acceptance; missing optional evidence does
not count as failure. `fresh_pixel_solve` requires a solve of the submitted
pixels. Headers and predicted coordinates cannot satisfy it.

Processing groups define compatible sampling, color, registration and calibration.
An assessment may credit a capture to several covered objectives. Count it once
per objective and once in project-wide capture and integration totals.

For mosaics, publish a target footprint and objective for each region that needs
its own depth goal. A wide frame may cover several regions. Every credited frame
must meet the objective's minimum coverage. Changing these regions requires a
new project revision; recommendations cannot change acceptance requirements.

Projects move from `draft` to `open`, then `paused` or `closed`. Owners reopen a
project by publishing a new open revision. Closure stops enrollment and new
recommendations. Captures must start before `capture_deadline`; clients must
finalize before `submission_deadline`. Both bounds are exclusive. Later revisions
preserve earlier submission windows and acceptance policies. Membership and
storage/security restrictions still apply; disclose them before enrollment.

`surplus_policy` selects `retain_and_attribute` or `reject_excess`. Reject excess
with `goal_already_met`, not a quality-failure reason. Process concurrent credit
decisions in server event-sequence order. Intents grant no priority. Evaluate a
recalibrated replacement against its existing credit, without making it compete
for a new place in the goal.

Terms have an ID, version, hash and immutable HTTPS URL. Publication references
the current version; enrollment and renewed consent record the accepted version.
Finalization requires current consent, including submissions against older
project revisions. If terms changed, keep staged bytes and return
`terms_consent_required`. Terms must state data ownership, license, attribution,
deletion, retention and withdrawal policies.

## 5. Participation, equipment and monthly capacity

Keep one participation per account/project, including its history. Repeated
enrollment returns that record. Open enrollment sets `active`; approval-based
enrollment sets `requested`. Maintainers approve or revoke. Participants can
pause, resume a self-paused membership, withdraw, re-request enrollment under
current policy, or renew consent. Only a maintainer can restore a revoked
membership to `requested`. Check the participation ETag on each transition.
The last active owner cannot leave or lose ownership. This draft does not define
role transfer.

Inactive memberships cannot issue tokens or mutate project resources. Account
reads still return the user's membership state. When a client learns that its
membership is inactive, it stops new collaboration work at a safe boundary.
Offline clients limit cached-plan use to the validity period the user approved.

Equipment offers specify sensor and optical geometry, filters, rotation and
optional approximate site data. Servers retain referenced revisions. Clients
must not apply a new offer to active equipment without local approval. Use
conditional reads/writes for current offers and revision routes for historical
ones. Filters need a stable ID and physical bandpass; a name such as `Ha` is
insufficient.

Capacity specifies a `YYYY-MM`, IANA timezone, offered rig-seconds, equipment,
UTC availability intervals and `soft` or `local_hard` mode. Count exposure time
and acquisition overhead as attempted rig-time. Exclude weather idle, uploads
and processing. Two rigs running for an hour consume two rig-hours. Split usage
at local month boundaries; do not roll unused time forward automatically.
Reported usage estimates effort; accepted integration measures accepted data.

Clients MUST keep project shares within their total budget across rigs and
servers. Each server sees only its share. Availability must fall in the named
month, and equipment IDs must belong to the participation. With `local_hard`,
the local scheduler avoids new work beyond its budget but completes safety
actions. A server cannot enforce a global limit on disconnected clients.

## 6. Recommendations, intent and live status

### Automatic framing after setup

Equipment registration returns the saved offer and planning advice. Check-in
reports equipment/capacity revisions, the last adopted recommendation and local
progress. It returns advice and the next check-in interval. Either request can
queue a recommendation when coverage needs change. Retries must not queue
duplicate work. Servers without recommendations return `unavailable` advice.

`PUT /participations/{id}/planning-policy` stores `suggest_only` or `automatic`
mode, approved equipment revisions, sky regions, filters, exposure limits,
overlap, panel count, rotation, terms version and cached-plan lifetime. The
capacity offer supplies the time budget. Clients MUST obtain user consent to
enable or expand automatic mode, even with `offer:write` permission.

Approved regions include target IDs and explicit bounds on panel centers.
Retaining a target ID does not authorize a new position. Clients check those
bounds, overlap and objective coverage before accepting a recommendation.

In automatic mode, clients may change future framing at safe boundaries without
prompting for each panel. Check-in can recommend keeping the plan, changing the
center or mosaic panel, waiting, or requesting review. An intent does not reserve
coverage or block other participants.

Recommendations include the policy revision, demand sequence, expiry,
`automatic_eligible` and review reasons. Clients MUST check these against local
consent and current equipment before execution. Fixed cameras retain their
confirmed angle within tolerance. Manual rotation, changed equipment or terms,
unapproved regions or filters, and increased time commitments require review.
Clients MUST retain panel/objective IDs and revision history for each capture.
Do not change an exposure already in progress or rewrite its history.

Offline clients may use a valid cached plan without checking the server before
every exposure. Expiry stops new work at a safe boundary. Reconnection may update
framing. Notify the user when review is required; routine updates need no notice.
Automatic uploads require separate local consent.

### Recommendation results and advisory progress

Recommendation jobs reference exact project, equipment and capacity revisions.
Results specify panel geometry, objective mappings, exposure recipes, estimated
rig-time and accepted integration, assumptions, unmet objectives and expiry.
They may use one wide field, a mosaic or a compatible subregion. Clients verify
geometry, overlap, budget, feasibility and local safety before execution.

Intents state planned objectives, panels, equipment, effort and expiry. Clients
may cancel them. Expired intents stop affecting forecasts but retain their
history. Servers accept overlapping valid intents. Stale intents cannot prevent
another participant from acquiring or submitting data.

Status is optional. Key it by participation and a stable writer UUID bound to
the authenticated client. Sequence numbers increase across restarts; use a new
writer ID to reset the sequence.

| Incoming status | Server response |
| --- | --- |
| Lower sequence | Ignore; return `applied: false`. |
| Same sequence and body | Return the previous result. |
| Same sequence, different body | Return a conflict. |
| Higher sequence | Store it with observation time and expiry. |

Show expired status as stale. Backfilled captures must not overwrite current
activity. Status, intent and pending uploads never create accepted credit.
Clients default activity to private. `visibility: project` shares it with members.
`GET /projects/{id}/activity` returns the caller's activity and other members'
shared activity. Revocation removes shared visibility. Anonymous clients cannot
read activity.

## 7. HTTP concurrency, errors and limits

Use JSON except for binary upload parts. Return errors as
[`application/problem+json`](https://www.rfc-editor.org/rfc/rfc9457.html), with
stable `code`, HTTP `status`, non-sensitive detail, request ID and optional field
errors. Return `404` when a caller must not learn that another project's resource exists.

Use strong ETags. Updates require one `If-Match`; return `428` if missing and
`412` if stale. Create-or-replace offers and intents require exactly one of
`If-Match` or `If-None-Match: *`. Return `400` for both, and `412` for a missing
resource with `If-Match` or an existing one with `If-None-Match: *`. Publication
checks the draft ETag.

POST mutations require a UUID `Idempotency-Key`. Retain results for the advertised
period, at least 24 hours. Scope keys by server, account, client, method and path.
Compare canonical JSON hashes (RFC 8785) and semantic preconditions. Authenticate
and check current permission first. Then replay an identical request's original
response before checking preconditions that may now be stale. Changed content
returns `409 idempotency_conflict`.

Resource and capture IDs still prevent duplicate enrollment, submissions and
credit after key expiry. For other mutations, clients inspect state before
retrying an expired key. After a lost PUT response, read the resource and compare
its content; normal precondition rules still apply.

Lists use opaque cursors and `limit` (1–100; default 50). Only sync snapshots
provide a consistent view across pages. Return `400` for invalid cursors and
`410 cursor_expired` for expired ones. Queued work returns `202`, a job ID and
polling delay. Completed jobs retain their result; failed jobs retain a problem.

| Status | Meaning |
| --- | --- |
| `401` | Invalid authentication. |
| `403` | Insufficient permission. |
| `409` | State or idempotency conflict. |
| `413` | Input exceeds byte limits. |
| `422` | Invalid or incompatible data. |
| `429` | Quota or rate limit; include `Retry-After`. |
| `503` | Temporary unavailability. |

Servers limit JSON size, object count, part size, decoded pixels, storage and
compute. Check actual input, not just declared sizes. Reject client paths,
executable metadata and requests to fetch arbitrary URLs. Server links use HTTPS;
clients never forward secrets across origins. Keep raw files locally and remove
private metadata from exported copies according to user consent.

## 8. Consistent sync

`POST /sync/snapshots` uses account authorization and explicit project IDs.
The server checks membership and captures a consistent view with a change
sequence. It returns the snapshot ID and first page. Include project summaries,
published revisions, the caller's participation, progress and own assessments.
Read local offers and intents through their resource routes.

Page with `GET /sync/snapshots/{id}?cursor=...`. Only the final page returns
`changes_cursor`, positioned after the snapshot's change sequence. Clients store
all pages and the cursor atomically. Then read ordered upserts/removals from
`GET /changes?cursor=...`, committing each batch with its `next_cursor`. Empty
batches still return a cursor and polling delay. Handle repeated events without
duplicate effects; use server sequence order, not client timestamps.

Bind snapshots and cursors to the account, client and project selection.
Changed selections and expired cursors require a new snapshot. Check permission
on every page. If a client loses project access during pagination, return
`409 snapshot_invalidated`; clients discard staged pages and start again.

The change feed emits `remove project_access` when membership becomes paused,
withdrawn or revoked, without further private content. It may retain that removal
record while membership is inactive. Resuming requires a new snapshot. After an
authorization failure, clients suspend use of private cached content until they
reconcile access.

Project updates create proposed local plan revisions. They do not replace active
acquisition programs. Submissions identify the requirements used for each capture.
Access removal preserves local captures and provenance.

## 9. Calibrated artifacts and resumable transfer

A submission contains an immutable manifest: client-assigned UUID, participation,
project revision, objectives and artifacts. Each artifact identifies its capture,
raw/calibrated SHA-256 hashes, equipment revision, exposure, bandpass, time,
processing state and calibration history. Record master roles/hashes, operations,
parameters and software versions. Listing a hash does not request the raw or
master file. Measurements include method, version, unit, value and time. Fresh
solves identify the submitted artifact hash; embedded WCS remains advisory.

Submission creation returns an authenticated upload session per artifact: total
bytes, server-selected part size/count, expiry and received parts. Number parts
from 1. All parts except the last contain exactly `part_size_bytes`; the last contains the remainder.
Parts may arrive out of order.

Send binary parts to `PUT /uploads/{id}/parts/{number}` with `Content-Length` and
lowercase hexadecimal `X-Part-SHA256`. The server verifies size and hash, then
records the part atomically. Identical retries return the same receipt. Changed
bytes for an acknowledged part return `409 part_conflict`. Bad hashes return
`422 digest_mismatch` without recording the part; clients may retry correct bytes.

Read received parts with `GET /uploads/{id}`. Renewal checks permission and quota,
then extends the same session without changing its manifest or part size. Expired
sessions reject writes until renewal. Servers retain uncommitted bytes for the
advertised staging period. After cleanup, renewal returns an empty part inventory.
Finalizing or finalized uploads cannot change or renew.

Finalization checks completeness and atomically creates one verification and
assessment job. Return `409 upload_incomplete` for missing parts. Otherwise return
`202` and the same job on retry, even after completion. Clients read current
results from `GET /submissions/{id}`. Workers hash, decode and assess each artifact.
A whole-file hash mismatch rejects that artifact with `digest_mismatch`; other
artifacts continue. To replace a corrupt artifact, submit a new artifact ID and
manifest while retaining capture identity.

Creating the same submission UUID with the same manifest returns the existing
submission. Different content conflicts regardless of idempotency-key retention.
At creation, renewal and finalization, check membership, current terms consent,
revision deadlines and quota. Revocation also stops part writes. This draft uses
no presigned storage URLs. Servers may finish previously finalized assessments
under recorded consent; revoked tokens cannot read private results.

Artifacts progress through `uploading -> received -> validating -> accepted|rejected`.
Only acceptance of a replacement changes an accepted artifact to `superseded`.
Submissions use `uploading`, `processing` or `complete` and report each artifact's
result, including partial failures.

Servers append automated or authorized human assessments with an ID, policy
revision, evidence, decision, reasons and per-objective credit. Manual writes
require the submission ETag and can assess only validated artifacts. Rejection
grants no credit. Acceptance requires the published evidence and compatibility
checks, regardless of the assessor's role.

Recalibration uses a new artifact ID, `supersedes_artifact_id` and the same capture
identity. Acceptance atomically replaces old credit; rejection preserves it.
Reassessment appends history and may reverse acceptance, subtracting credit once.
Servers enforce unique credit per project/objective/capture and reject conflicting
duplicates pending attribution review. Do not reveal another user's private
manifest when resolving duplicate hashes.

Progress records a server event sequence and separate totals for goals, current
intent, reported captures, pending, accepted, rejected and surplus data. Surplus
may receive attribution but does not count toward required completion. Project
integration counts each credited capture once across overlapping objectives.

## 10. Scope and unresolved deployment choices

This draft excludes equipment control, centralized scheduling, federation,
raw-image ingestion, dataset downloads, payments and a required quality algorithm.
Servers publish their signup rules, quotas, terms, assessment methods and retention
policies, and choose their storage backend.

Before releasing v1, test the [conformance scenarios](conformance.md) with two
independent clients and a server. Review metric identifiers, spherical coverage
and device authorization. Decide whether dataset downloads belong in v1 or an
optional extension. Schema checks alone do not establish interoperability.
