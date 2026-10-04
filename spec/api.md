# REST reference

<!-- Generated from openapi/astrocollab.yaml by tools/build_openapi.py. Do not edit. -->

Version 0.1.0-draft.1. Paths are relative to the API root from `GET /capabilities`, for example `https://collab.example/v1`.

Send `Authorization: Bearer <api key>` where a key is required. Bodies are JSON
unless stated. Errors use `application/problem+json`; act on `code`. Every type
links to a standalone [JSON Schema](../schemas/index.json), so you can validate
payloads without OpenAPI tools. The [protocol](protocol.md) gives the rules
behind each route, and [codes](codes.md) lists every error code and reason.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | [`/capabilities`](#getcapabilities) | Read server identity, limits and features |
| `POST` | [`/pair`](#pairclient) | Pair a client and receive its API key |
| `GET` | [`/me/projects`](#listmyprojects) | List the projects you have joined |
| `GET` | [`/projects/{project_id}`](#getproject) | Read a project's requirements |
| `GET` | [`/projects/{project_id}/progress`](#getprogress) | Read a project's progress |
| `PUT` | [`/me/equipment/{equipment_id}`](#registerequipment) | Register or update a rig |
| `PATCH` | [`/me/equipment/{equipment_id}`](#updateequipment) | Update part of a rig |
| `GET` | [`/me/equipment/{equipment_id}`](#getequipment) | Read a rig |
| `GET` | [`/me/equipment`](#listequipment) | List the account's rigs |
| `POST` | [`/me/checkins`](#checkin) | Ask what this rig should image |
| `POST` | [`/projects/{project_id}/submissions`](#createsubmission) | Describe files to submit and get upload sessions |
| `PUT` | [`/uploads/{upload_id}/parts/{part_number}`](#putuploadpart) | Send one part of a file |
| `GET` | [`/uploads/{upload_id}`](#getupload) | See which parts of an upload arrived |
| `POST` | [`/submissions/{submission_id}/finalize`](#finalizesubmission) | Finish a submission and start assessment |
| `GET` | [`/submissions/{submission_id}`](#getsubmission) | Read a submission and each file's outcome |

## Discovery

<a id="getcapabilities"></a>

### Read server identity, limits and features

`GET /capabilities`

API key: None.

| Status | Body | Meaning |
| --- | --- | --- |
| `200` | [`Capabilities`](../schemas/Capabilities.schema.json) (`application/json`) | Success. |
| `429` | [`Problem`](../schemas/Problem.schema.json) (`application/problem+json`) | Too many requests. Retry after the stated delay. |
| `default` | [`Problem`](../schemas/Problem.schema.json) (`application/problem+json`) | Error; see `code`. |

Response `200` ([file](../examples/getCapabilities.response.json)):

```json
{
  "server_id": "00000000-0000-4000-8000-000000000099",
  "api_root": "https://collab.example/v1",
  "protocol_versions": [
    "v1"
  ],
  "spec_version": "0.1.0-draft.1",
  "features": [],
  "account_url": "https://collab.example/account",
  "artifact_formats": [
    "fits",
    "xisf"
  ],
  "limits": {
    "max_json_bytes": 1048576,
    "max_artifacts_per_submission": 100,
    "max_artifact_bytes": 2147483648,
    "max_chunk_bytes": 8388608,
    "upload_staging_seconds": 604800,
    "max_decoded_pixels": 200000000
  }
}
```

## Pairing

<a id="pairclient"></a>

### Pair a client and receive its API key

`POST /pair`

API key: None.

Takes no Authorization header. A code works once and expires within an hour. An unknown, used or expired code returns 401 `invalid_pairing_code`. Do not retry automatically; if the response is lost, issue a new code.

Request body (`application/json`): [`PairRequest`](../schemas/PairRequest.schema.json)

| Status | Body | Meaning |
| --- | --- | --- |
| `201` | [`PairedKey`](../schemas/PairedKey.schema.json) (`application/json`) | Sent once; never cache. |
| `429` | [`Problem`](../schemas/Problem.schema.json) (`application/problem+json`) | Too many requests. Retry after the stated delay. |
| `default` | [`Problem`](../schemas/Problem.schema.json) (`application/problem+json`) | Error; see `code`. |

Error codes: [`invalid_pairing_code`](codes.md), [`invalid_request`](codes.md).

Request ([file](../examples/pairClient.request.json)):

```json
{
  "pairing_code": "acpc_EXAMPLE_ONLY_NOT_A_REAL_CODE",
  "installation_id": "00000000-0000-4000-8000-000000000020",
  "client_name": "Roof rig 2"
}
```

Response `201` ([file](../examples/pairClient.response.json)):

```json
{
  "key_id": "00000000-0000-4000-8000-000000000018",
  "api_key": "acpk_EXAMPLE_ONLY_NOT_A_REAL_CREDENTIAL",
  "account_id": "00000000-0000-4000-8000-000000000002",
  "client_name": "Roof rig 2",
  "installation_id": "00000000-0000-4000-8000-000000000020",
  "project_ids": [
    "00000000-0000-4000-8000-000000000001"
  ]
}
```

## Projects

<a id="listmyprojects"></a>

### List the projects you have joined

`GET /me/projects`

API key: Required.

Join projects on the server's web pages.

| Name | In | Type | Notes |
| --- | --- | --- | --- |
| `cursor` | query | `string`, optional |  |
| `limit` | query | `integer`, optional | minimum `1`, maximum `100`, default `50` |

| Status | Body | Meaning |
| --- | --- | --- |
| `200` | [`MyProjectPage`](../schemas/MyProjectPage.schema.json) (`application/json`) | Success. |
| `429` | [`Problem`](../schemas/Problem.schema.json) (`application/problem+json`) | Too many requests. Retry after the stated delay. |
| `default` | [`Problem`](../schemas/Problem.schema.json) (`application/problem+json`) | Error; see `code`. |

Error codes: [`invalid_cursor`](codes.md), [`invalid_limit`](codes.md).

Response `200` ([file](../examples/listMyProjects.response.json)):

```json
{
  "items": [
    {
      "project_id": "00000000-0000-4000-8000-000000000001",
      "title": "M31 deep H-alpha",
      "state": "open",
      "membership": "active"
    }
  ],
  "next_cursor": null
}
```

<a id="getproject"></a>

### Read a project's requirements

`GET /projects/{project_id}`

API key: Optional. Public projects can be read without a key.

| Name | In | Type | Notes |
| --- | --- | --- | --- |
| `project_id` | path | [`Uuid`](../schemas/Uuid.schema.json), required |  |

| Status | Body | Meaning |
| --- | --- | --- |
| `200` | [`Project`](../schemas/Project.schema.json) (`application/json`) | Success. |
| `429` | [`Problem`](../schemas/Problem.schema.json) (`application/problem+json`) | Too many requests. Retry after the stated delay. |
| `default` | [`Problem`](../schemas/Problem.schema.json) (`application/problem+json`) | Error; see `code`. |

Error codes: [`not_found`](codes.md).

Example: [Response `200`](../examples/getProject.response.json).

<a id="getprogress"></a>

### Read a project's progress

`GET /projects/{project_id}/progress`

API key: Optional. Public projects can be read without a key.

| Name | In | Type | Notes |
| --- | --- | --- | --- |
| `project_id` | path | [`Uuid`](../schemas/Uuid.schema.json), required |  |

| Status | Body | Meaning |
| --- | --- | --- |
| `200` | [`Progress`](../schemas/Progress.schema.json) (`application/json`) | Success. |
| `429` | [`Problem`](../schemas/Problem.schema.json) (`application/problem+json`) | Too many requests. Retry after the stated delay. |
| `default` | [`Problem`](../schemas/Problem.schema.json) (`application/problem+json`) | Error; see `code`. |

Error codes: [`not_found`](codes.md).

Response `200` ([file](../examples/getProgress.response.json)):

```json
{
  "project_id": "00000000-0000-4000-8000-000000000001",
  "revision": 1,
  "as_of": "2026-10-04T05:10:00Z",
  "objectives": [
    {
      "objective_id": "00000000-0000-4000-8000-000000000005",
      "goal": {
        "accepted_frames": 120,
        "accepted_integration_seconds": 36000
      },
      "assigned_frames": 96,
      "reported_frames": 12,
      "pending_frames": 0,
      "accepted_frames": 1,
      "accepted_integration_seconds": 300,
      "rejected_frames": 0,
      "surplus_frames": 0,
      "complete": false
    }
  ],
  "accepted_frames": 1,
  "accepted_integration_seconds": 300
}
```

## Rigs and assignments

<a id="registerequipment"></a>

### Register or update a rig

`PUT /me/equipment/{equipment_id}`

API key: Required.

Create the rig, or replace its description. An identical body keeps the revision.

| Name | In | Type | Notes |
| --- | --- | --- | --- |
| `equipment_id` | path | [`Uuid`](../schemas/Uuid.schema.json), required |  |

Request body (`application/json`): [`EquipmentInput`](../schemas/EquipmentInput.schema.json)

| Status | Body | Meaning |
| --- | --- | --- |
| `200` | [`Equipment`](../schemas/Equipment.schema.json) (`application/json`) | Success. |
| `429` | [`Problem`](../schemas/Problem.schema.json) (`application/problem+json`) | Too many requests. Retry after the stated delay. |
| `default` | [`Problem`](../schemas/Problem.schema.json) (`application/problem+json`) | Error; see `code`. |

Error codes: [`invalid_request`](codes.md), [`invalid_range`](codes.md).

Example: [Response `200`](../examples/registerEquipment.response.json).

Example: [Request](../examples/registerEquipment.request.json).

<a id="updateequipment"></a>

### Update part of a rig

`PATCH /me/equipment/{equipment_id}`

API key: Required.

Change only the fields sent, using JSON merge patch (RFC 7396): `null` removes a field. Lets a rig report what it knows without erasing what the user entered on the web.

| Name | In | Type | Notes |
| --- | --- | --- | --- |
| `equipment_id` | path | [`Uuid`](../schemas/Uuid.schema.json), required |  |

Request body (`application/merge-patch+json`): [`EquipmentInputMergePatchUpdate`](../schemas/EquipmentInputMergePatchUpdate.schema.json)

| Status | Body | Meaning |
| --- | --- | --- |
| `200` | [`Equipment`](../schemas/Equipment.schema.json) (`application/json`) | Success. |
| `429` | [`Problem`](../schemas/Problem.schema.json) (`application/problem+json`) | Too many requests. Retry after the stated delay. |
| `default` | [`Problem`](../schemas/Problem.schema.json) (`application/problem+json`) | Error; see `code`. |

Error codes: [`not_found`](codes.md), [`invalid_request`](codes.md), [`invalid_range`](codes.md).

Request ([file](../examples/updateEquipment.request.json)):

```json
{
  "focal_length_mm": 402.5,
  "confirmed_position_angle_degrees": 1.5
}
```

Example: [Response `200`](../examples/updateEquipment.response.json).

<a id="getequipment"></a>

### Read a rig

`GET /me/equipment/{equipment_id}`

API key: Required.

| Name | In | Type | Notes |
| --- | --- | --- | --- |
| `equipment_id` | path | [`Uuid`](../schemas/Uuid.schema.json), required |  |

| Status | Body | Meaning |
| --- | --- | --- |
| `200` | [`Equipment`](../schemas/Equipment.schema.json) (`application/json`) | Success. |
| `429` | [`Problem`](../schemas/Problem.schema.json) (`application/problem+json`) | Too many requests. Retry after the stated delay. |
| `default` | [`Problem`](../schemas/Problem.schema.json) (`application/problem+json`) | Error; see `code`. |

Error codes: [`not_found`](codes.md).

Example: [Response `200`](../examples/getEquipment.response.json).

<a id="listequipment"></a>

### List the account's rigs

`GET /me/equipment`

API key: Required.

| Name | In | Type | Notes |
| --- | --- | --- | --- |
| `cursor` | query | `string`, optional |  |
| `limit` | query | `integer`, optional | minimum `1`, maximum `100`, default `50` |

| Status | Body | Meaning |
| --- | --- | --- |
| `200` | [`EquipmentPage`](../schemas/EquipmentPage.schema.json) (`application/json`) | Success. |
| `429` | [`Problem`](../schemas/Problem.schema.json) (`application/problem+json`) | Too many requests. Retry after the stated delay. |
| `default` | [`Problem`](../schemas/Problem.schema.json) (`application/problem+json`) | Error; see `code`. |

Error codes: [`invalid_cursor`](codes.md), [`invalid_limit`](codes.md).

Example: [Response `200`](../examples/listEquipment.response.json).

<a id="checkin"></a>

### Ask what this rig should image

`POST /me/checkins`

API key: Required.

The server picks the project and panel where this rig adds most, using everything the rig advertises, and assigns it. Repeating a check-in is safe and creates nothing new.

Request body (`application/json`): [`Checkin`](../schemas/Checkin.schema.json)

| Status | Body | Meaning |
| --- | --- | --- |
| `200` | [`CheckinResult`](../schemas/CheckinResult.schema.json) (`application/json`) | Success. |
| `429` | [`Problem`](../schemas/Problem.schema.json) (`application/problem+json`) | Too many requests. Retry after the stated delay. |
| `default` | [`Problem`](../schemas/Problem.schema.json) (`application/problem+json`) | Error; see `code`. |

Error codes: [`not_found`](codes.md), [`invalid_reference`](codes.md), [`membership_inactive`](codes.md).

Example: [Response `200`](../examples/checkIn.response.json).

Request ([file](../examples/checkIn.request.json)):

```json
{
  "equipment_id": "00000000-0000-4000-8000-000000000007",
  "assignment_id": "00000000-0000-4000-8000-000000000014",
  "unsubmitted_captures": [
    {
      "panel_id": "00000000-0000-4000-8000-000000000015",
      "frames": 12,
      "integration_seconds": 3600,
      "last_captured_at": "2026-10-04T04:55:00Z"
    }
  ],
  "observed_at": "2026-10-04T05:00:00Z"
}
```

## Submissions and uploads

<a id="createsubmission"></a>

### Describe files to submit and get upload sessions

`POST /projects/{project_id}/submissions`

API key: Required.

| Name | In | Type | Notes |
| --- | --- | --- | --- |
| `project_id` | path | [`Uuid`](../schemas/Uuid.schema.json), required |  |

Request body (`application/json`): [`SubmissionCreate`](../schemas/SubmissionCreate.schema.json)

| Status | Body | Meaning |
| --- | --- | --- |
| `200` | [`Submission`](../schemas/Submission.schema.json) (`application/json`) | Already created with this `id` and body; returned unchanged. |
| `201` | [`Submission`](../schemas/Submission.schema.json) (`application/json`) | Created |
| `409` | [`Problem`](../schemas/Problem.schema.json) (`application/problem+json`) | Conflict with the resource's state, such as `upload_incomplete` or `id_conflict`. |
| `429` | [`Problem`](../schemas/Problem.schema.json) (`application/problem+json`) | Too many requests. Retry after the stated delay. |
| `default` | [`Problem`](../schemas/Problem.schema.json) (`application/problem+json`) | Error; see `code`. |

Error codes: [`membership_inactive`](codes.md), [`id_conflict`](codes.md), [`submission_deadline_passed`](codes.md), [`payload_too_large`](codes.md), [`too_many_artifacts`](codes.md), [`artifact_too_large`](codes.md), [`invalid_request`](codes.md), [`invalid_reference`](codes.md), [`invalid_revision`](codes.md), [`duplicate_id`](codes.md), [`invalid_supersede`](codes.md), [`capture_deadline_passed`](codes.md), [`deliverable_mismatch`](codes.md), [`too_few_subs`](codes.md), [`invalid_stack`](codes.md), [`drizzle_not_allowed`](codes.md), [`external_delivery_not_accepted`](codes.md).

Example: [Response `201`](../examples/createSubmission.response.json).

Example: [Request](../examples/createSubmission.request.json).

<a id="putuploadpart"></a>

### Send one part of a file

`PUT /uploads/{upload_id}/parts/{part_number}`

API key: Required.

Sending the same bytes again returns the same receipt.

| Name | In | Type | Notes |
| --- | --- | --- | --- |
| `upload_id` | path | [`Uuid`](../schemas/Uuid.schema.json), required |  |
| `part_number` | path | `integer`, required | minimum `1`, maximum `10000` |
| `Content-Length` | header | `integer`, required | minimum `1` |
| `X-Part-SHA256` | header | [`Sha256`](../schemas/Sha256.schema.json), required |  |

Request body (`application/octet-stream`): `value`

| Status | Body | Meaning |
| --- | --- | --- |
| `200` | [`PartReceipt`](../schemas/PartReceipt.schema.json) (`application/json`) | Success. |
| `409` | [`Problem`](../schemas/Problem.schema.json) (`application/problem+json`) | Conflict with the resource's state, such as `upload_incomplete` or `id_conflict`. |
| `429` | [`Problem`](../schemas/Problem.schema.json) (`application/problem+json`) | Too many requests. Retry after the stated delay. |
| `default` | [`Problem`](../schemas/Problem.schema.json) (`application/problem+json`) | Error; see `code`. |

Error codes: [`not_found`](codes.md), [`part_conflict`](codes.md), [`upload_expired`](codes.md), [`upload_finalized`](codes.md), [`invalid_part_number`](codes.md), [`part_size_mismatch`](codes.md), [`digest_mismatch`](codes.md).

Response `200` ([file](../examples/putUploadPart.response.json)):

```json
{
  "part_number": 1,
  "size_bytes": 8388608,
  "sha256": "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
}
```

<a id="getupload"></a>

### See which parts of an upload arrived

`GET /uploads/{upload_id}`

API key: Required.

| Name | In | Type | Notes |
| --- | --- | --- | --- |
| `upload_id` | path | [`Uuid`](../schemas/Uuid.schema.json), required |  |

| Status | Body | Meaning |
| --- | --- | --- |
| `200` | [`UploadSession`](../schemas/UploadSession.schema.json) (`application/json`) | Success. |
| `429` | [`Problem`](../schemas/Problem.schema.json) (`application/problem+json`) | Too many requests. Retry after the stated delay. |
| `default` | [`Problem`](../schemas/Problem.schema.json) (`application/problem+json`) | Error; see `code`. |

Error codes: [`not_found`](codes.md).

Response `200` ([file](../examples/getUpload.response.json)):

```json
{
  "id": "00000000-0000-4000-8000-000000000013",
  "submission_id": "00000000-0000-4000-8000-000000000012",
  "artifact_id": "00000000-0000-4000-8000-000000000011",
  "size_bytes": 104371200,
  "part_size_bytes": 8388608,
  "part_count": 13,
  "expires_at": "2026-10-05T05:00:00Z",
  "received_parts": []
}
```

<a id="finalizesubmission"></a>

### Finish a submission and start assessment

`POST /submissions/{submission_id}/finalize`

API key: Required.

Returns the submission in `processing`. Read it again until it is `complete`.

| Name | In | Type | Notes |
| --- | --- | --- | --- |
| `submission_id` | path | [`Uuid`](../schemas/Uuid.schema.json), required |  |

| Status | Body | Meaning |
| --- | --- | --- |
| `202` | [`Submission`](../schemas/Submission.schema.json) (`application/json`) | Accepted; read the resource again later. |
| `409` | [`Problem`](../schemas/Problem.schema.json) (`application/problem+json`) | Conflict with the resource's state, such as `upload_incomplete` or `id_conflict`. |
| `429` | [`Problem`](../schemas/Problem.schema.json) (`application/problem+json`) | Too many requests. Retry after the stated delay. |
| `default` | [`Problem`](../schemas/Problem.schema.json) (`application/problem+json`) | Error; see `code`. |

Error codes: [`not_found`](codes.md), [`upload_incomplete`](codes.md), [`terms_consent_required`](codes.md), [`submission_deadline_passed`](codes.md).

Example: [Response `202`](../examples/finalizeSubmission.response.json).

<a id="getsubmission"></a>

### Read a submission and each file's outcome

`GET /submissions/{submission_id}`

API key: Required.

| Name | In | Type | Notes |
| --- | --- | --- | --- |
| `submission_id` | path | [`Uuid`](../schemas/Uuid.schema.json), required |  |

| Status | Body | Meaning |
| --- | --- | --- |
| `200` | [`Submission`](../schemas/Submission.schema.json) (`application/json`) | Success. |
| `429` | [`Problem`](../schemas/Problem.schema.json) (`application/problem+json`) | Too many requests. Retry after the stated delay. |
| `default` | [`Problem`](../schemas/Problem.schema.json) (`application/problem+json`) | Error; see `code`. |

Error codes: [`not_found`](codes.md).

Example: [Response `200`](../examples/getSubmission.response.json).
