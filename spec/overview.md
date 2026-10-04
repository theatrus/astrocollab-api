# How the API works

A project describes one picture that many people build together: its targets,
filters and how deep each part must go. Contributors work on their own, on their
own nights, with their own rigs. Each rig asks the server what to image, and the
server hands out the part of the picture that most needs data and suits that
rig. Contributors upload what they capture; the server checks it and credits the
accepted data toward the shared goal.

The server never controls equipment and never reserves a target. Two people can
image the same field at once; the server credits useful data from both.

This page follows one contributor from first contact to credited data. Each step
shows the request and the important part of the response. Full payloads are in
[`examples/`](../examples), and the [reference server](../reference/README.md)
runs every step on your machine, with sample projects.

## The flow

| Step | Where | Request |
| --- | --- | --- |
| 1. Join a project and get a pairing code | Server's web pages | None |
| 2. Pair the client | Client | `POST /pair` |
| 3. Describe the rig | Client | `PUT /me/equipment/{rig}` |
| 4. Ask what to image | Client | `POST /me/checkins` |
| 5. Capture | Your own software | None |
| 6. Upload | Client | `POST /projects/{id}/submissions`, `PUT /uploads/{id}/parts/{n}`, `POST /submissions/{id}/finalize` |
| 7. Read the result | Client | `GET /submissions/{id}`, `GET /projects/{id}/progress` |

Steps 1 to 3 happen once per rig. Steps 4 to 7 repeat each night. The examples
use these shell variables:

```sh
API=https://collab.example/v1   # the server's API root, from GET /capabilities
KEY=...                          # the API key from step 2
INSTALLATION=$(uuidgen)          # made once per installation, then kept
RIG=$(uuidgen)                   # made once per rig, then kept
```

## 1. Join a project and get a pairing code

The user does this in a browser. The server's `account_url` from
`GET /capabilities` leads to its pages. There the user picks a project, accepts
its terms, and issues a pairing code for the rig. Servers should let the user do
both in one step on the project page.

A project says what it wants back:

- **Calibrated subs:** each calibrated exposure as its own file. The project
  stacks everything.
- **Stacked masters:** you stack your own subs and send the master, with a list
  of the subs inside it.

The client finds the projects you joined with `GET /me/projects`.

## 2. Pair the client

The user enters the code in the client. The client trades it for its own API
key. This request needs no `Authorization` header:

```sh
curl -X POST $API/pair \
  -H "Content-Type: application/json" \
  -d '{"pairing_code": "acpc_...", "installation_id": "'$INSTALLATION'", "client_name": "Roof rig 2"}'
```

```json
{ "key_id": "00000000-0000-4000-8000-000000000018", "api_key": "acpk_...", "client_name": "Roof rig 2" }
```

The key appears only in this response. Store it in the system credential store
and send it on every other request. See [Authentication](authentication.md).

## 3. Describe the rig

The server hands out work by what each rig can do: its field of view, sampling,
filters, color or mono sensor, and, if given, where it is. The user may enter
some of this on the server's web pages when setting the rig up; a pairing code
issued for that rig returns its `equipment_id`. The client can fill in the rest.

To register a new rig, or replace its description:

```sh
curl -X PUT $API/me/equipment/$RIG \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d @examples/registerEquipment.request.json
```

The example describes a 6248×4176 mono camera with 3.76 µm pixels on a 400 mm
lens, with an H-alpha filter: about 3.4°×2.2° of sky. To change only some fields
and keep the rest, such as filters entered on the web, send a merge patch:

```sh
curl -X PATCH $API/me/equipment/$RIG \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/merge-patch+json" \
  -d '{"focal_length_mm": 402.5}'
```

Each filter lists its passbands. A dual-narrowband filter on a color camera
lists two, H-alpha and OIII, so one night can serve two objectives.
[Color rig example](../examples/color-rig-equipment.json).

## 4. Ask what to image

```sh
curl -X POST $API/me/checkins \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"equipment_id": "'$RIG'", "observed_at": "2026-10-04T04:00:00Z"}'
```

The server looks at every project you have joined, picks where this rig helps
most, and assigns it work:

```json
{
  "action": "image",
  "next_checkin_seconds": 600,
  "assignment": {
    "id": "00000000-0000-4000-8000-000000000014",
    "project_id": "00000000-0000-4000-8000-000000000001",
    "expires_at": "2026-10-05T04:00:00Z",
    "panels": [
      { "id": "00000000-0000-4000-8000-000000000015",
        "target_name": "M31 outer disk",
        "footprint": {
          "center": { "ra_degrees": 10.6847, "dec_degrees": 41.269 },
          "width_degrees": 3.36, "height_degrees": 2.24, "position_angle_degrees": 0 },
        "filter_id": "00000000-0000-4000-8000-000000000008",
        "objective_ids": ["00000000-0000-4000-8000-000000000005"],
        "exposure_seconds": 300,
        "suggested_frames": 96 }
    ]
  }
}
```

Each panel says where to point, which filter to use, how long each exposure
should be and how many to take. [Full response](../examples/checkIn.response.json).

You will rarely get a whole mosaic. When a target is bigger than your field, the
server splits it into a grid and gives you the panel that most needs data;
`layout` says which column and row it is. Other rigs get other panels. An
assignment may list a few panels in order: move to the next when the current one
has its suggested frames. Later check-ins may send you to another panel or target
as the picture fills in.

| `action` | What to do |
| --- | --- |
| `image` | Work through the panels in `assignment`, in order. |
| `continue` | Keep working on your current assignment. |
| `wait` | Nothing suits this rig now. `reason_codes` say why, such as `rig_incomplete`. |

Check in again after `next_checkin_seconds`. Send the assignment you are working
on as `assignment_id`, and report what you have captured but not yet submitted
as `unsubmitted_captures`: totals per panel, replacing your last report. You
don't have to upload to show progress. The server counts reported frames when it
hands out work, which matters most in a masters project, where subs wait until
there are enough to stack. There is nothing to accept or decline: your own software still
decides when and whether to point the rig, within its own safety limits.

## 5. Capture

Capture and calibrate with your usual software. Keep the recommendation and
panel IDs with each frame; the manifest refers to them. For a masters project,
stack your subs once you have enough for the project's `master_rules`.

## 6. Upload

Upload happens in three requests: describe, send bytes, finish.

**Describe.** Send a manifest with an `id` you choose. It lists each file with
its hash, size, exposure and calibration history.
[Example with subs](../examples/createSubmission.request.json);
[example with a master](../examples/stacked-master-submission.json).

```sh
curl -X POST $API/projects/$PROJECT/submissions \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d @manifest.json
```

A master adds a `stack` block: every sub in it by capture ID, time and hash, and
how they were registered, normalized, rejected and weighted.

The server answers with one upload session per file. It chooses the part size:

```json
{
  "id": "00000000-0000-4000-8000-000000000012",
  "state": "uploading",
  "uploads": [
    { "id": "00000000-0000-4000-8000-000000000013", "size_bytes": 104371200,
      "part_size_bytes": 8388608, "part_count": 13, "received_parts": [] }
  ]
}
```

**Send bytes.** Split the file into `part_count` parts. Every part has exactly
`part_size_bytes` bytes except the last. Send each part with its SHA-256:

```sh
curl -X PUT $API/uploads/$UPLOAD/parts/1 \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/octet-stream" \
  -H "X-Part-SHA256: $(sha256sum part1 | cut -d' ' -f1)" \
  --data-binary @part1
```

Parts may arrive in any order. Sending the same part again is safe. If the
connection drops, `GET /uploads/{id}` shows which parts arrived.

**Finish.**

```sh
curl -X POST $API/submissions/$SUBMISSION/finalize \
  -H "Authorization: Bearer $KEY"
```

The server answers `202` with the submission in `processing`.

## 7. Read the result

Read the submission until its `state` is `complete`. Each artifact then shows
`accepted` or `rejected`, with reasons:

```sh
curl -H "Authorization: Bearer $KEY" $API/submissions/$SUBMISSION
```

Accepted data counts toward the shared picture:

```sh
curl -H "Authorization: Bearer $KEY" $API/projects/$PROJECT/progress
```

```json
{
  "objectives": [
    { "goal": { "accepted_frames": 120, "accepted_integration_seconds": 36000 },
      "accepted_frames": 1, "accepted_integration_seconds": 300, "complete": false }
  ]
}
```

Progress counts subs and seconds, whether they arrived alone or inside masters.
It keeps accepted data apart from assigned frames, frames reported but not yet
submitted, and uploads still in review. Only accepted data counts toward a goal.

## Sharing files outside the API

Some projects accept files shared through a service such as Google Drive. The
project's requirements then include `external_delivery`, with instructions.

Share the file as told, then send the usual manifest with two extra fields on
each artifact:

```json
{
  "delivery": "external",
  "external": {
    "provider": "google_drive",
    "url": "https://drive.example/file/d/EXAMPLE_FILE_ID/view",
    "shared_at": "2026-10-04T05:00:00Z"
  }
}
```

There is nothing to upload. Finalize as usual. A maintainer downloads the file,
checks it against your hash and records the result. Assessment then proceeds as
for uploaded files. [Example manifest](../examples/external-submission.json).

## Rules for every request

| Rule | What to do |
| --- | --- |
| Base URL | Join every path to `api_root` from `/capabilities`. |
| Credentials | Send `Authorization: Bearer <key>`. Never put a key in a URL or log. |
| Bodies | Send and expect JSON, except upload parts, which are raw bytes. Servers reject unknown fields. |
| Retries | Every request is safe to repeat as is. Creates use an `id` you choose, so a retry finds the record. |
| `202 Accepted` | Work continues on the server. Read the resource again later. |
| Lists | Pass `next_cursor` back as `cursor` until it is `null`. |

Errors use [`application/problem+json`](https://www.rfc-editor.org/rfc/rfc9457.html).
Act on `code`, not on the wording of `detail`:

```json
{
  "type": "https://collab.example/problems/terms-consent-required",
  "title": "Terms consent required",
  "status": 409,
  "code": "terms_consent_required",
  "detail": "Review the current project terms before finalizing.",
  "request_id": "00000000-0000-4000-8000-000000000090"
}
```

| Status | Meaning | What to do |
| --- | --- | --- |
| `401` | The key is wrong, expired or revoked. | Ask the user for a new key. |
| `403` | The account may not do this, for example it is not an active member. | Show the error; do not retry. |
| `404` | Not found, or you may not see it. | Check the ID. |
| `409` | State conflict, such as `upload_incomplete` or `id_conflict`. | Read the resource, then decide. |
| `422` | The body is invalid. `errors` points at the bad fields. | Fix the data. |
| `429` | Too many requests. | Wait `Retry-After` seconds. |
| `503` | The server is busy or down. | Retry the same request later. |

## Next

- [Authentication](authentication.md): pairing, API keys and what servers must build.
- [Walkthrough](walkthrough.md): every example payload, in order.
- [Protocol](protocol.md): the rules servers and clients must follow.
- [Reference server](../reference/README.md) and
  [conformance tester](../conformance/README.md): run and test the API locally.
