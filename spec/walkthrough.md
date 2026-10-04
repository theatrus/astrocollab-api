# Payload walkthrough

This page walks through every example payload in order. For a shorter tour
with requests, read [How the API works](overview.md). The examples use synthetic
data and placeholder keys and hashes. The [example index](../examples/manifest.json)
maps each file to its schema, and `python -m reference.client` runs the same flow
against the [reference server](../reference/README.md).

## Publish required frames

Create a project with [initial requirements](../examples/createProject.request.json).
The server returns the [draft and owner membership](../examples/createProject.response.json).
With the owner's API key, read the draft ETag, then publish with `If-Match` and
a new `Idempotency-Key`.

The [published revision](../examples/publishProject.response.json) requires
120 accepted H-alpha frames at 300–600 seconds each, totaling at least 36,000
seconds. It specifies the M31 footprint, sampling, calibration, linear mono
processing, fresh pixel solves, an optional FWHM limit and submission deadlines.
Accepted data must meet both count and integration goals.

## Join the project

Join with [current terms consent](../examples/joinProject.request.json).
Open enrollment returns an [active participation](../examples/joinProject.response.json);
approval-based enrollment returns `requested` until a maintainer approves it.
The participant's API key now works on this project's routes. Participation
and capture IDs are not credentials.

## Configure automatic framing

[Register the equipment](../examples/registerEquipment.request.json) using
`If-None-Match: *`. This example specifies a 6248×4176 mono sensor, 3.76 µm pixels,
400 mm focal length, H-alpha filter and fixed camera angle: about 3.36°×2.24°.
The [response](../examples/registerEquipment.response.json) requests policy review
before automatic operation.

[Offer 10 rig-hours for October](../examples/setCapacity.request.json), or 36,000
rig-seconds. After user review, [save an automatic planning policy](../examples/setPlanningPolicy.request.json)
with equipment revisions, sky regions, filters, exposure limits, overlap, panel
count, angle and offline lifetime. Upload consent remains separate.

[Check in](../examples/checkIn.request.json) with equipment, capacity and policy
revisions and progress. The [response](../examples/checkIn.response.json) returns
a recommendation job and check-in interval. Poll the job for
[framing and exposure settings](../examples/recommendation.json):

- One panel with 96 exposures of 300 seconds.
- 9.6 estimated rig-hours, including acquisition overhead.
- 6.4 estimated hours of accepted integration after quality assessment.

The client checks `automatic_eligible: true` against local policy and equipment,
then applies the framing at a safe boundary. It can publish an
[intent](../examples/setIntent.request.json), which does not reserve the field.
Later check-ins may recommend another center or panel. A change that needs
manual rotation [requires review](../examples/automatic-framing-review.json).

The [800 mm equipment example](../examples/mosaic-equipment.json) uses
[two target regions](../examples/mosaic-project-revision.json). Its
[reviewed policy](../examples/mosaic-planning-policy.json) allows a
[two-panel mosaic](../examples/mosaic-recommendation.json): 48 exposures per panel
with about 23% overlap. Changing from 400 mm to 800 mm requires equipment and
policy review before automatic operation resumes.

## Upload calibrated exposures

Submit a [manifest](../examples/createSubmission.request.json) after calibration
and local review. It identifies a 300-second exposure, capture ID, dark/flat
provenance, pixel unit, file hashes and fresh solve. Keep raw frames and masters
locally.

The [response](../examples/createSubmission.response.json) allocates an upload
session for 104,371,200 bytes: twelve parts of 8,388,608 bytes and a final part
of 3,707,904 bytes. PUT raw binary bytes with `Content-Length` and `X-Part-SHA256`.
The server verifies each part and returns a [receipt](../examples/putUploadPart.response.json).

After an interruption, read the received-part list and resume. Renew an expired
upload session with `POST /uploads/{id}/renew`.

Once the server acknowledges all parts, finalize the submission. It returns an
[assessment job](../examples/finalizeSubmission.response.json) that verifies the
files and evaluates them. Retrying finalization returns the same job. If terms
changed, the server returns [terms_consent_required](../examples/terms-consent-required.json)
until the user accepts the current version.

The [assessment](../examples/appendAssessment.response.json) credits one frame
and 300 seconds. [Progress](../examples/getProgress.response.json) lists accepted
integration separately from intent, reported captures and pending data.
[Recalibration](../examples/recalibrated-submission.json) uses a new artifact ID
and `supersedes_artifact_id` but retains capture identity. Acceptance replaces
existing credit rather than counting the exposure twice.

Projects that accept [external delivery](../examples/external-delivery-requirements.json)
let contributors share files through a service such as Google Drive. The
[manifest](../examples/external-submission.json) registers the link and hash in
place of an upload. A maintainer downloads the file and
[records the retrieval](../examples/recordRetrieval.request.json); the
[submission](../examples/recordRetrieval.response.json) then moves to validation.

## Sync project revisions

Request a snapshot for [selected project IDs](../examples/createSnapshot.request.json).
Store all pages with the cursor from the [final page](../examples/createSnapshot.response.json)
in one transaction, then apply [ordered changes](../examples/getChanges.response.json).
This example starts with zero accepted captures; the feed adds an assessment
and updates progress.

On an [access-removal record](../examples/access-revoked.json), stop using private
remote content and stop new collaboration work at a safe boundary. Keep local
captures and provenance. Apply project revisions to future plans, not exposures
already in progress.
