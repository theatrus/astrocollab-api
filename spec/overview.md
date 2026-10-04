# How the API works

A project owner publishes the frames a project needs. Participants capture
frames with their own equipment and upload them. The server checks each frame
and credits the ones that meet the requirements.

The server never controls equipment and never reserves a target. Two people can
image the same field at once; the server credits useful data from both.

This page follows one participant from first contact to credited data. Each step
shows the request and the important part of the response. Full payloads are in
[`examples/`](../examples), and the [reference server](../reference/README.md)
runs every step on your machine.

## The flow

| Step | Request | Credential |
| --- | --- | --- |
| 1. Discover the server | `GET /capabilities` | None |
| 2. Pair the client | `POST /pair` with a code from the account page | None |
| 3. Find a project | `GET /projects`, `GET /projects/{id}` | None for public projects |
| 4. Join it | `POST /projects/{id}/participations` | API key |
| 5. Describe your equipment (optional) | `PUT .../equipment/{id}`, `PUT .../capacity`, `POST .../checkins` | API key |
| 6. Upload frames | `POST /projects/{id}/submissions`, `PUT /uploads/{id}/parts/{n}`, `POST /submissions/{id}/finalize` | API key |
| 7. Read the result | `GET /jobs/{id}`, `GET /projects/{id}/progress` | API key |

The examples below use two shell variables:

```sh
API=https://collab.example/v1   # the server's API root
KEY=...                          # the API key from step 2
INSTALLATION=$(uuidgen)          # made once per installation, then kept
```

## 1. Discover the server

```sh
curl $API/capabilities
```

```json
{
  "server_id": "00000000-0000-4000-8000-000000000099",
  "api_root": "https://collab.example/v1",
  "features": ["recommendations"],
  "account_url": "https://collab.example/account",
  "limits": { "max_chunk_bytes": 8388608, "idempotency_retention_seconds": 86400 }
}
```

`features` lists optional parts the server supports. `account_url` is where the
user signs up and issues pairing codes. [Full response](../examples/getCapabilities.response.json).

## 2. Pair the client

The user opens `account_url`, issues a pairing code, and enters it in the
client. The client trades the code for its own API key:

```sh
curl -X POST $API/pair \
  -H "Content-Type: application/json" \
  -d '{"pairing_code": "acpc_...", "installation_id": "'$INSTALLATION'", "client_name": "Roof rig 2"}'
```

```json
{ "key_id": "00000000-0000-4000-8000-000000000018", "api_key": "acpk_...", "client_name": "Roof rig 2" }
```

The key appears only in this response. Store it, then send it on every private
request:

```sh
curl -H "Authorization: Bearer $KEY" $API/me/participations
```

A `200` means the key works. The response lists the projects the user has
joined. See [Authentication](authentication.md) for scopes and key handling.

## 3. Find a project

```sh
curl $API/projects
curl $API/projects/$PROJECT
```

The project's current revision lists its targets and objectives. An objective
says what counts: the filter band, exposure range, sampling, calibration and the
number of frames or seconds of accepted integration it needs.
[Example revision](../examples/publishProject.response.json).

## 4. Join the project

Accept the project's current terms by echoing them back:

```sh
curl -X POST $API/projects/$PROJECT/participations \
  -H "Authorization: Bearer $KEY" \
  -H "Idempotency-Key: $(uuidgen)" \
  -H "Content-Type: application/json" \
  -d @examples/joinProject.request.json
```

```json
{ "id": "00000000-0000-4000-8000-000000000003", "role": "contributor", "state": "active" }
```

Keep the participation `id`; later routes use it. Projects with approval-based
enrollment return `"state": "requested"` until a maintainer approves.

## 5. Describe your equipment (optional)

This step lets the server suggest framing. Skip it if you plan on your own.

```sh
curl -X PUT $API/participations/$PART/equipment/$RIG \
  -H "Authorization: Bearer $KEY" \
  -H "If-None-Match: *" \
  -H "Content-Type: application/json" \
  -d @examples/registerEquipment.request.json
```

Then offer time for the month with `PUT .../capacity` and check in now and then
with `POST .../checkins`. A check-in returns advice, such as keep the current
plan, wait, or a new framing to review. Advice never takes control of your
equipment. See the [walkthrough](walkthrough.md#configure-automatic-framing).

## 6. Upload frames

Upload happens in three requests: describe, send bytes, finish.

**Describe.** Send a manifest listing each calibrated frame with its hash, size,
exposure and calibration history.
[Example manifest](../examples/createSubmission.request.json).

```sh
curl -X POST $API/projects/$PROJECT/submissions \
  -H "Authorization: Bearer $KEY" \
  -H "Idempotency-Key: $(uuidgen)" \
  -H "Content-Type: application/json" \
  -d @manifest.json
```

The server answers with one upload session per frame. It chooses the part size:

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
  -H "Authorization: Bearer $KEY" \
  -H "Idempotency-Key: $(uuidgen)"
```

```json
{ "id": "00000000-0000-4000-8000-000000000081", "kind": "assessment", "state": "queued", "poll_after_seconds": 3 }
```

## 7. Read the result

Poll the job until `state` is `succeeded` or `failed`, waiting
`poll_after_seconds` between reads:

```sh
curl -H "Authorization: Bearer $KEY" $API/jobs/$JOB
```

A finished assessment job returns the submission with each frame's outcome.
Accepted frames count toward the project:

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

Progress keeps accepted data apart from plans, reported captures and uploads
still in review. Only accepted data counts toward a goal.

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
| `POST` | Send `Idempotency-Key` with a new UUID for each action. Reuse the same key and body when you retry. |
| `PUT` to update | Send `If-Match` with the ETag from your last read. |
| `PUT` to create | Send `If-None-Match: *`. |
| `202 Accepted` | The response is a job. Poll `GET /jobs/{id}`. |
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
| `403` | The key lacks a scope, or your role does not allow this. | Show the error; do not retry. |
| `404` | Not found, or you may not see it. | Check the ID. |
| `409` | State conflict, such as `upload_incomplete` or `idempotency_conflict`. | Read the resource, then decide. |
| `412` | Your ETag is stale. | Read the resource again and reapply your change. |
| `428` | You left out `If-Match`. | Read the resource to get its ETag. |
| `422` | The body is invalid. `errors` points at the bad fields. | Fix the data. |
| `429` | Too many requests. | Wait `Retry-After` seconds. |
| `503` | The server is busy or down. | Retry later with the same `Idempotency-Key`. |

## Running a project

Owners use the same API with a maintainer role:

1. `POST /projects` with the full requirements. The server creates a draft and
   makes you its owner. [Example](../examples/createProject.request.json).
2. `GET /projects/{id}/draft` to read the draft's ETag.
3. `POST /projects/{id}/publish` with `If-Match`. Each publication creates a new,
   unchangeable revision.

Maintainers approve members with `POST /participations/{id}/review` and may
append manual assessments with `POST /submissions/{id}/assessments`.

## Keeping a local copy

Clients that cache project data read a snapshot once, then follow a change feed:

1. `POST /sync/snapshots` with the project IDs you want. Page through it.
2. The last page returns `changes_cursor`. Store all pages and the cursor together.
3. Poll `GET /changes?cursor=...` and apply each batch with its `next_cursor`.

If the feed says `remove project_access`, stop using that project's private data
and start a new snapshot if access returns.

## Next

- [Authentication](authentication.md): API keys, scopes and what servers must build.
- [Walkthrough](walkthrough.md): every example payload, in order.
- [Protocol](protocol.md): the rules servers and clients must follow.
- [Reference server](../reference/README.md) and
  [conformance tester](../conformance/README.md): run and test the API locally.
