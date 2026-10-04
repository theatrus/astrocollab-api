# Payload walkthrough

This page walks through every example payload in order. For a shorter tour with
requests, read [How the API works](overview.md). The examples use synthetic data
and placeholder keys and hashes. The [example index](../examples/manifest.json)
maps each file to its schema, and `python -m reference.client` runs the same flow
against the [reference server](../reference/README.md).

## Discover the server and pair

[Capabilities](../examples/getCapabilities.response.json) give the API root, the
account pages and the limits, such as the 8 MiB largest upload part.

The user joins a project and issues a pairing code on the server's web pages.
The client [trades the code](../examples/pairClient.request.json) for its
[API key](../examples/pairClient.response.json), shown only once. This key is
limited to one project.

## Read the project

[`GET /me/projects`](../examples/listMyProjects.response.json) lists the projects
the account has joined. The [project](../examples/getProject.response.json) holds
its current requirements: 120 accepted H-alpha frames of 300–600 seconds each,
at least 36,000 seconds in all, over the M31 footprint, with sampling,
calibration, a fresh pixel solve, an optional FWHM limit and deadlines. Accepted
data must meet both the frame and the integration goal, on every panel.

## Describe the rig

[Register the rig](../examples/registerEquipment.request.json) once with
`PUT /me/equipment/{id}`: a 6248×4176 mono sensor with 3.76 µm pixels, a 400 mm
focal length and a 3 nm H-alpha filter, about 3.36°×2.25° of sky. The
[response](../examples/registerEquipment.response.json) is the saved rig. A
[merge patch](../examples/updateEquipment.request.json) changes only the fields
it names; the [result](../examples/updateEquipment.response.json) has a new
revision and keeps everything else. A [color rig](../examples/color-rig-equipment.json)
lists two passbands, H-alpha and OIII, on its dual-narrowband filter.

## Ask what to image

[Check in](../examples/checkIn.request.json) with the rig, the assignment it is
working on and what it has captured but not yet submitted: 12 frames, one hour,
on its panel. The
[response](../examples/checkIn.response.json) carries an
[assignment](../examples/assignment.json):

- one panel covering the whole target, H-alpha, 96 exposures of 300 seconds;
- 9.6 estimated rig-hours, including acquisition overhead;
- 6.4 estimated hours of accepted integration after quality assessment.

The [800 mm rig](../examples/mosaic-equipment.json) sees half that field. In this
[project](../examples/mosaic-project.json) the target needs two panels at that
scale, and other 800 mm rigs share the same grid. This
[assignment](../examples/mosaic-assignment.json) gives the rig both panels, in
order, because both need data: 48 exposures each, with about 23% overlap.
`layout` places each in the grid. A rig with too little described gets
[`wait`](../examples/checkin-wait.json) with `rig_incomplete`.

## Upload calibrated subs

Submit a [manifest](../examples/createSubmission.request.json) after calibration.
It lists a 300-second exposure with its capture ID, dark and flat hashes, pixel
unit, file hashes and fresh solve, and names the assignment and panel. Raw
frames and calibration frames stay local.

The [response](../examples/createSubmission.response.json) opens an upload
session for 104,371,200 bytes: twelve parts of 8,388,608 bytes and a last part
of 3,707,904 bytes. Send raw bytes with `Content-Length` and `X-Part-SHA256`; the
server checks each part and returns a [receipt](../examples/putUploadPart.response.json).
After an interruption, [read the received parts](../examples/getUpload.response.json)
and resume.

When every part has arrived, finalize. The server returns the
[submission](../examples/finalizeSubmission.response.json) in `processing` while
it assesses the files; read it again until it is
[complete](../examples/getSubmission.response.json). Here the frame is accepted
and credits one frame and 300 seconds. If the terms changed, finalization
returns [terms_consent_required](../examples/terms-consent-required.json) until
the user accepts them on the web.

[Progress](../examples/getProgress.response.json) keeps accepted data apart from
assigned, reported and pending frames. A [recalibrated file](../examples/recalibrated-submission.json)
uses a new artifact ID and `supersedes_artifact_id` but keeps its capture
identity, so acceptance replaces the earlier credit rather than adding to it.

## Send stacked masters

A project that wants [stacked masters](../examples/stacked-masters-requirements.json)
gets one file per stack. The [manifest](../examples/stacked-master-submission.json)
lists the 24 subs in the master and how they were stacked. Acceptance credits
24 subs and 7,200 seconds.

## Share files outside the API

A project that accepts [external delivery](../examples/external-delivery-requirements.json)
lets contributors share files through a service such as Google Drive. The
[manifest](../examples/external-submission.json) points at a shared folder and
names each file with `path`, in place of an upload. The project fetches the file,
checks its hash and then assesses it as usual.

## Errors

Errors share one shape. Examples: an [invalid body](../examples/invalid-request.json)
with field pointers, a reused submission ID with a different body
([`id_conflict`](../examples/id-conflict.json)), different bytes for a received
part ([`part_conflict`](../examples/part-conflict.json)) and a
[rate limit](../examples/rate-limited.json).
