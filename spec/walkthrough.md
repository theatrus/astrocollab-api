# Connected payload walkthrough

All IDs, domains, instruments, locations and data are synthetic. Digests are
illustrative hex strings, not hashes of supplied FITS files. Tokens are obvious
placeholders. The [example manifest](../examples/manifest.json) binds each file
to a schema and, where applicable, its OpenAPI operation. This is a conversation
between hypothetical clients and a service, not a working endpoint tutorial.

## Publish required frames

An owner signs in on the service, authorizes an account client, and creates a
project with [initial requirements](../examples/createProject.request.json).
The response contains the [draft project and owner membership](../examples/createProject.response.json).
Use that membership to obtain a project token with `project:manage`, fetch the
draft ETag, and publish it with `If-Match` and a fresh `Idempotency-Key`.

The [published revision](../examples/publishProject.response.json) requests
120 accepted 300–600-second H-alpha frames and at least 36,000 seconds of accepted
integration. It defines the M31 footprint, sampling range, calibration steps,
linear mono processing, fresh pixel solves, an optional FWHM ceiling and a late
submission window. Both count and integration goals apply. Titles and coordinates
do not serve as identities.

## Volunteer and authorize a client

Another user signs up at the account UI and volunteers with
[current terms consent](../examples/joinProject.request.json). Open enrollment
returns an [active participation](../examples/joinProject.response.json).
Approval-based enrollment would return `requested` until a maintainer reviews it.

The client requests [specific project scopes](../examples/issueParticipationToken.request.json).
The [token response](../examples/issueParticipationToken.response.json) is bound
to this participation and client for 900 seconds. Renew it through the account
authorization when needed; neither origin capture IDs nor participation IDs are
credentials. A token for this project cannot submit to another project.

## Register equipment, set a budget, then leave routine framing automatic

[Register equipment](../examples/registerEquipment.request.json) with
`If-None-Match: *`: a 6248×4176 mono sensor, 3.76 µm pixels, 400 mm focal length,
H-alpha filter and confirmed fixed angle. The approximate field is 3.36°×2.24°.
The [registration response](../examples/registerEquipment.response.json) includes
planning advice; initial setup requires a reviewed policy before automatic use.
Subsequent registration can trigger a coverage recommendation when inputs suffice.

[Offer 10 rig-hours for October](../examples/setCapacity.request.json), encoded
as 36,000 rig-seconds, and [enable an automatic planning policy](../examples/setPlanningPolicy.request.json)
after user review. The policy bounds equipment revisions, allowed center regions,
filters, exposure range, overlap, panel count, fixed-camera angle and offline
validity. It does not authorize automatic uploads.

Routine [check-in](../examples/checkIn.request.json) reports equipment, capacity
and policy revisions plus coarse progress. The [reply](../examples/checkIn.response.json)
queues a recommendation and says when to check in again. Poll the job to obtain
[a specific framing and recipe](../examples/recommendation.json): one panel,
96 exposures of 300 seconds, about 9.6 attempted rig-hours with overhead, and an
estimated 6.4 hours of accepted integration. These numbers express different
quantities and are not a promise of accepted yield.

The recommendation is `automatic_eligible: true`. The client rechecks its local
policy and equipment, then adopts it at a safe boundary with no per-panel prompt.
It can post an [advisory intent](../examples/setIntent.request.json). Another
volunteer remains free to shoot that same field. A later check-in may suggest a
new center or panel as remaining coverage changes. If physical rotation becomes
necessary, [advice requires review](../examples/automatic-framing-review.json).
No server reply bypasses the local acquisition system's authority.

For a longer instrument, the [mosaic equipment](../examples/mosaic-equipment.json)
has a reviewed 800 mm configuration. The [second project revision](../examples/mosaic-project-revision.json)
defines two canonical coverage units. A [matching reviewed policy](../examples/mosaic-planning-policy.json)
allows an [automatic two-panel recommendation](../examples/mosaic-recommendation.json),
48 exposures per panel at the same fixed angle. The panels overlap about 23%.
This alternative illustrates the equipment-aware payloads; changing from the
400 mm setup to 800 mm still requires the initial equipment/policy review.

## Upload calibrated exposures

After capture, calibration and local review, submit
[an immutable manifest](../examples/createSubmission.request.json). The example
identifies one 300-second calibrated sub, its origin capture, dark/flat provenance,
physical pixel unit, artifact/raw hashes and fresh solve. Uploading raw frames
or masters is not implied. Keep those originals locally.

The [response](../examples/createSubmission.response.json) contains an upload
session for 104,371,200 bytes in thirteen parts. The first twelve parts are
8,388,608 bytes; the last is 3,707,904 bytes. PUT each raw binary part with its
actual `Content-Length` and lowercase `X-Part-SHA256`. No base64 encoding or
storage-specific SDK is required. Acknowledged bytes produce a
[part receipt](../examples/putUploadPart.response.json).

After a lost connection, read the upload's received parts and resume. Renew an
expired upload session under current participation authority. Obtain a fresh
participation token if the original 15-minute credential has expired; session
expiry and token expiry are independent.

Finalize after all parts are acknowledged. The server verifies whole-artifact
integrity and returns an [assessment job](../examples/finalizeSubmission.response.json).
Repeating finalization returns that job without duplicate processing or credit.
Changed terms return a [consent problem](../examples/terms-consent-required.json)
until the user reviews them. Bytes received are not yet accepted science data.

An [assessment](../examples/appendAssessment.response.json) records measured
evidence and credits one frame and 300 seconds. The [progress projection](../examples/getProgress.response.json)
shows accepted integration separately from intents, reported capture and pending
data. [Recalibration](../examples/recalibrated-submission.json) uses a new artifact ID with `supersedes_artifact_id`, while
retaining origin/capture identity. Acceptance replaces existing credit; it cannot
add a second 300 seconds for the same physical capture.

## Sync without losing revisions

Create a snapshot for [explicit project IDs](../examples/createSnapshot.request.json).
Stage all pages before installing them. The [final page](../examples/createSnapshot.response.json)
contains the cursor for subsequent [ordered changes](../examples/getChanges.response.json).
The final-page example is a small one-page snapshot before any accepted captures,
including the zero-credit progress projection. The subsequent feed delivers an
assessment and updated progress without losing the change between them.

On a [revocation tombstone](../examples/access-revoked.json), stop using private
remote content and stop new collaboration work at a safe local boundary. Keep
local captures and provenance. A changed requirement revision stages a future
plan update; it never rewrites an exposure already in flight.
