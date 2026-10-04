# AstroCollab protocol

Version: 0.1.0-draft.1. Draft specification.

MUST and MUST NOT mark requirements. SHOULD marks a recommendation; MAY marks
an option. The [OpenAPI contract](../openapi/astrocollab.yaml) defines payload
structure. This document defines the behavior that schemas cannot check.

## 1. What the API covers

A project describes one picture that many contributors build together. The API
is what a contributor's client calls: pair a rig, describe it, ask what to
image, and send the results. It has 15 operations.

Everything else belongs to the server and its own tools: signup, joining
projects, setting requirements, reviewing members, assessing data by hand,
moderation and billing. This protocol says how the server must behave toward
contributors, not how owners run it.

The server never controls equipment and never reserves a target. The client's
own software decides when and whether to point a rig.

## 2. Conventions

Clients start from an API root and call public `GET /capabilities`. It gives the
server ID, API root, account pages (`account_url`), supported versions, optional
features and limits. Routes are relative to the API root. Servers use HTTPS;
`http://` is allowed only on loopback addresses, for local development.

Resources have opaque UUIDs. A capture is identified by `(origin_id,
capture_id)`: the producer picks a persistent origin UUID, and the client keeps
both IDs when it submits, recalibrates or stacks the capture. Hashes detect
identical bytes; they do not prove who made them.

| Quantity | Convention |
| --- | --- |
| Coordinates | ICRS; RA in degrees [0, 360), Dec in degrees [-90, 90]. |
| Footprint | Tangent-plane rectangle centered on the stated coordinates; width and height in degrees. At position angle zero, width runs east-west and height north-south. |
| Position angle | Degrees east of celestial north, measured along the height axis. |
| Sampling | Arcseconds per pixel. |
| Wavelength | Nanometers. |
| Focal length / pixel size | Millimeters / micrometers. |
| Duration / size | Seconds / bytes. |
| Timestamp | RFC 3339 UTC with `Z`. |

Bodies are JSON, except upload parts, which are raw bytes. Omit unknown values
rather than sending zero. Servers reject unknown request fields; clients ignore
unknown response fields. Lists take an opaque `cursor` and `limit` (1–100,
default 50) and return `next_cursor`, `null` on the last page.

Errors use [`application/problem+json`](https://www.rfc-editor.org/rfc/rfc9457.html)
with a stable `code`, the HTTP `status`, a short `detail`, a request ID and, for
invalid input, field `errors`. Clients act on `code`.

| Status | Meaning |
| --- | --- |
| `401` | Missing, unknown, expired or revoked key. |
| `403` | The account may not do this, for example it is not an active member. |
| `404` | Not found, or not visible to this account. |
| `409` | Conflict with current state, such as `id_conflict` or `upload_incomplete`. |
| `413` | Input exceeds a size limit. |
| `422` | Invalid or incompatible data. |
| `429` | Too many requests; wait `Retry-After` seconds. |
| `503` | Temporarily unavailable; retry later. |

Servers limit JSON size, part size, decoded pixels, storage and compute, and
check actual input, not declared sizes. They reject client paths, executable
metadata and requests to fetch arbitrary URLs.

## 3. Pairing and API keys

Every private request carries `Authorization: Bearer <api key>`. Each client
installation gets its own key by pairing:

1. On the server's web pages, the user issues a pairing code. The user may limit
   the key to some projects, set an expiry, or issue the code for a rig already
   set up on the web.
2. The user enters the code in the client.
3. The client calls `POST /pair` with the code, a persistent `installation_id`
   and a `client_name`. It sends no `Authorization` header.
4. The server returns the key once, with `Cache-Control: no-store`, and the rig's
   `equipment_id` when the code was issued for a rig.

A code works once and expires within one hour. The server consumes the code and
creates the key in one transaction. Pairing again with the same
`installation_id` revokes that installation's old key. An unknown, used or
expired code returns `401 invalid_pairing_code`; repeated failures return `429`.
Clients MUST NOT retry pairing on their own. If the response is lost, the user
issues a new code and revokes the orphan key. Servers MAY also let users copy a
key from the web pages; every server MUST support pairing.

A key acts for its account, limited to its projects if the user chose some.
Servers store only hashes of codes and keys, each with at least 128 bits of
randomness, and let users list and revoke keys. They check the key and the
account's membership on every request.

Clients store keys in the system credential store and send them only in the
`Authorization` header, over HTTPS, to the server's own origin. Keys never go in
URLs, manifests or logs.

## 4. Projects and membership

Users join projects on the server's web pages, where they accept the project's
terms. `GET /me/projects` lists the projects the account has joined, with its
membership state. Only `active` members receive assignments and submit data.
When a client learns that membership is no longer active, it stops starting new
work for that project.

`GET /projects/{id}` returns the project's current requirements and their
`revision`, which rises whenever the owners publish changes. Submissions name the
revision their frames were taken for. A later revision MUST NOT change how data
taken for an earlier one is judged.

Requirements contain:

- **Targets:** each with a footprint on the sky.
- **Objectives:** for a target, the accepted passbands, exposure range,
  processing group, minimum coverage, quality rules and goal. The goal, in frames,
  seconds of integration or both, is the depth every part of the target needs.
- **Processing groups:** sampling range, color state and calibration steps.
- **Terms, deadlines and `deliverable`:** `calibrated_subs` (each calibrated
  exposure as its own file) or `stacked_masters` (masters the contributor stacks,
  with `master_rules`).
- **`external_delivery`**, when the project accepts files shared outside the API.

Captures must start before `capture_deadline`; submissions must be finalized
before `submission_deadline`. Both bounds are exclusive.

## 5. Rigs

A rig belongs to the account and serves every project it joins. The user may set
it up on the web, the client may register it with `PUT /me/equipment/{id}`, or
both. `PATCH` with a JSON merge patch changes only the fields sent, so a rig can
report its focal length without erasing filters entered on the web.

Only the name is required. To plan for a rig, the server needs the unbinned
sensor size, pixel size, focal length, color state and at least one filter.
Optional fields include binning, camera and telescope names, rotation and an
approximate site. Field of view depends on sensor size, pixel size and focal
length; sampling also depends on binning.

Each filter has a stable ID and a list of passbands, each with a name, center
wavelength and, when known, width. A narrowband or luminance filter has one
passband; a dual-narrowband filter on a color camera has two. Filters may also
give their kind, maker and model.

A changed description creates a new revision; an identical one does not. Servers
keep every revision that an assignment or submission cites.

## 6. Assignments

### Asking for work

A rig asks what to image with `POST /me/checkins`, naming its equipment ID. It
may limit the choice to some projects and name the assignment it is working on.
The server answers with one of three actions:

| Action | Meaning |
| --- | --- |
| `image` | `assignment` holds new work. Start it at a safe point. |
| `continue` | Keep working on the current assignment. |
| `wait` | Nothing suits this rig now. `reason_codes` say why, such as `rig_incomplete`, `no_matching_filter`, `sampling_out_of_range` or `goals_met`. |

Each answer gives `next_checkin_seconds`. The server decides; there is no
proposal for the client to accept or reject. A repeated check-in creates nothing
new.

### Reporting progress before upload

A rig may capture for days before it submits, and in a masters project subs wait
until there are enough to stack. A check-in reports this with
`unsubmitted_captures`: for each panel, the frames and integration captured but
not yet submitted, and when the last one was taken. Each report is a total that
replaces the rig's previous one, so repeating it changes nothing. A rig sends
`[]` once everything is submitted.

The server counts reported frames when it hands out panels, so it does not send
more rigs to a panel whose data is already on its way, and shows them in
progress as `reported_frames`. Reported frames earn no credit; only submitted,
accepted data does. Servers MAY stop counting a report that is not followed by
submissions, for example after the submission deadline or a period they
publish.

### How the server assigns work

The server picks the project and panel where the rig adds most, using what the
rig advertises:

| Rig capability | Used for |
| --- | --- |
| Field of view | Panel size: whole targets for wide rigs, single panels for long ones. |
| Sampling | Objectives whose sampling range the rig meets. |
| Filters and passbands | Objectives the rig can serve. A dual-band filter can serve two objectives in the same frames. |
| Color state | Objectives whose processing group accepts mono or color data. |
| Rotation and angle | Panel angle: fixed cameras keep their angle. |
| Site, when given | Targets that rise high enough there. |

A filter serves an objective when one of its passbands matches one of the
objective's accepted passbands: its center lies inside the accepted band and,
when both widths are known, its width is between a quarter of the accepted width
and the full accepted width. When the accepted band gives no width, centers must
agree within 5 nm. So a 3 nm H-alpha filter serves a 7 nm H-alpha objective, but
a narrowband filter does not serve a 300 nm luminance objective.

For a target larger than a rig's field, the server lays a grid of panels over it.
Each panel needs the objective's full goal, and the objective is complete when
every panel is. The server tracks depth per panel and hands each rig the panels
that most need data, so no rig has to build a whole mosaic. Rigs with similar
fields SHOULD share a grid, but a rig only gets panels that fit its own field.
`Panel.layout` gives a panel's column and row.

An assignment usually holds one panel. It may list a few, in order, when the
first will be done before the night ends; the rig moves on when the current
panel has its suggested frames. Each panel gives where to point, the filter, the
exposure, the suggested frame count and the objectives it serves. Every panel
MUST fit the rig's field, use one of its filters, and meet the objective's
sampling and exposure rules.

Assignments reserve nothing. Two rigs may get overlapping panels, and useful data
from both counts.

### Client duties

Clients check each assignment against their own safety limits before use. They
stop starting new frames for an assignment after its `expires_at`, never change
an exposure already in progress, and keep the assignment and panel IDs with each
capture.

## 7. Submissions

### Manifest

A submission is an immutable manifest for one project, with a client-chosen
`id`, the project revision and up to 100 artifacts. An artifact is one file:

- **Calibrated sub** (`kind: calibrated_sub`): one exposure, with its capture
  identity, raw and calibrated hashes, rig revision, filter and passbands,
  exposure, capture time, calibration history and measurements.
- **Stacked master** (`kind: stacked_master`): a registered, linear master with a
  `stack` block listing every sub in it by capture identity, time and hash, the
  sub count and total integration, and how the subs were registered, normalized,
  rejected and weighted. All subs share one exposure length.

Calibration history records each calibration frame's role and hash, the
operations, parameters and software versions. Darks and flats are never sent.
A `fresh_pixel_solve` must solve the submitted pixels; headers and predicted
coordinates do not count.

Servers reject an artifact whose kind does not match the project's
`deliverable` with `422 deliverable_mismatch`. A master needs at least
`master_rules.min_sub_count` subs (`422 too_few_subs`), a `sub_count` equal to
its list and an integration equal to `sub_count` × `exposure_seconds`
(`422 invalid_stack`), and `allow_drizzle` if drizzled
(`422 drizzle_not_allowed`).

### Upload

Each artifact arrives one of two ways, for subs and masters alike:

- **`upload`:** the server returns an upload session with a fixed part size. The
  client sends `PUT /uploads/{id}/parts/{n}` with the raw bytes,
  `Content-Length` and lowercase hex `X-Part-SHA256`. Parts are numbered from 1;
  all but the last have exactly `part_size_bytes`. Parts may arrive in any order.
  The server checks size and hash and records each part atomically: the same
  bytes return the same receipt, different bytes for a received part return
  `409 part_conflict`, and a bad hash returns `422 digest_mismatch` without
  recording the part. `GET /uploads/{id}` lists received parts. Each accepted part
  extends the session; an idle session expires after the staging period, and the
  client then submits again under a new ID.
- **`external`:** the file is shared outside the API, if the project's
  `external_delivery` lists the provider (otherwise
  `422 external_delivery_not_accepted`). The location gives the provider, an
  HTTPS link and the share time. The link may point at a shared folder, with
  `path` naming the file inside it. There is no upload session; the artifact
  waits in `awaiting_retrieval` until the project fetches the file and checks its
  hash. A server fetches files itself only over HTTPS from hosts it lists in
  `external_retrieval_hosts`. Links may grant access: only the submitter and the
  project's maintainers may see them.

`POST /submissions/{id}/finalize` checks that every upload is complete
(`409 upload_incomplete` otherwise) and starts assessment. It returns `202` with
the submission in `processing`. Clients read `GET /submissions/{id}` until it is
`complete`; each artifact then shows `accepted` or `rejected` with reason codes
and the credit it earned. One bad file does not hold up the rest.

At creation, part writes and finalization, servers check membership, current
terms consent, deadlines and quota. If the terms changed, finalization returns
`409 terms_consent_required` until the user accepts them on the web; staged
bytes are kept.

## 8. Assessment and credit

The server assesses each artifact against the requirements of the revision it
names: hashes, passbands, sampling, exposure, coverage of its panel, required
evidence and quality rules. Missing required evidence fails; missing optional
evidence does not. Only accepted artifacts earn credit. Maintainers may also
assess by hand with the server's own tools, under the same rules.

Credit counts subs and seconds of integration. An accepted sub credits one frame
and its exposure; an accepted master credits its sub count and integration. A
sub earns credit once per project, whether alone or inside a master: a master
that repeats a credited sub is rejected with `duplicate_capture`. Assessment
judges a master's pixels as a whole; the sub list is the contributor's claim.
One frame may credit several objectives when its filter serves them all.

To replace a file, submit a new artifact ID with `supersedes_artifact_id` and the
same capture identity; acceptance replaces the old credit, rejection keeps it.
Frames accepted after a panel's goal is met count as surplus: credited to the
contributor, not to the goal.

`GET /projects/{id}/progress` shows each objective's goal and its assigned,
reported, pending, accepted, rejected and surplus frames, and whether every panel has the
goal's depth. Only accepted data counts toward a goal.

## 9. Retries

Every request is safe to repeat as is. There are no idempotency keys and no
preconditions.

| Request | Why a repeat is safe |
| --- | --- |
| Create a submission | The client picks the `id`. The same `id` and body returns the existing submission with `200`; a different body returns `409 id_conflict`. |
| `PUT` and `PATCH` a rig | They set the same values again; no new revision. |
| Upload a part | The same bytes return the same receipt. |
| Finalize | Returns the same submission. |
| Check in | Creates nothing new; a capture report replaces the last one. |
| Pair | Never repeated automatically; see section 3. |

## 10. Out of scope

This draft leaves out equipment control, central scheduling, federation, raw
image upload, dataset downloads, payments, a required quality algorithm, and the
owner and maintainer tools for running projects. Servers publish their signup
rules, quotas, terms, assessment methods and retention policies.
