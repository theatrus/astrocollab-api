# Codes

The API reports three kinds of codes: error codes on failed requests, reasons a
check-in says `wait`, and reasons the server rejects a file. Clients act on the
code, not on the wording of `detail`.

Servers use these codes for these cases. They MAY add codes of their own. A
client that meets an unknown error code acts on the HTTP status; an unknown
`wait` or rejection reason is shown to the user as is.

## Error codes

Failed requests return [`application/problem+json`](https://www.rfc-editor.org/rfc/rfc9457.html)
with `status` and `code`. Invalid input also lists field `errors`, each with a
JSON pointer.

### 400 Bad request

| Code | Meaning | What to do |
| --- | --- | --- |
| `invalid_json` | The body is not valid JSON. | Fix the client. |
| `invalid_cursor` | The list cursor is unknown or expired. | Start the list again without a cursor. |
| `invalid_limit` | `limit` is outside 1–100. | Use a limit in range. |

### 401 Unauthorized

| Code | Meaning | What to do |
| --- | --- | --- |
| `authentication_required` | No API key was sent. | Send `Authorization: Bearer <api key>`. |
| `invalid_credentials` | The key is unknown, expired or revoked. | Stop and ask the user to pair again. |
| `invalid_pairing_code` | The pairing code is unknown, used or expired. | Ask the user for a new code. Do not retry on your own. |

### 403 Forbidden

| Code | Meaning | What to do |
| --- | --- | --- |
| `membership_inactive` | The account is not an active member of this project, or the key is limited to other projects. | Stop work for this project. The user can check membership on the web. |

### 404 Not found

| Code | Meaning | What to do |
| --- | --- | --- |
| `not_found` | No such resource, or this account may not see it. | Check the ID. |

### 405 Method not allowed

| Code | Meaning | What to do |
| --- | --- | --- |
| `method_not_allowed` | The route exists but not with this method. | Fix the client. |

### 409 Conflict

| Code | Meaning | What to do |
| --- | --- | --- |
| `id_conflict` | This submission `id` already exists with a different body. | Use a new ID for a new submission. |
| `part_conflict` | This part was already received with different bytes. | Check the file; it changed after upload began. |
| `upload_incomplete` | Finalize was called before every part arrived. | Read `GET /uploads/{id}` and send the missing parts. |
| `upload_expired` | The upload session expired while idle. | Submit again under a new ID. |
| `upload_finalized` | The submission is already finalized; parts cannot change. | Nothing to do; read the submission. |
| `terms_consent_required` | The project's terms changed. | Ask the user to accept them on the web, then finalize again. Staged bytes are kept. |
| `submission_deadline_passed` | The project no longer accepts submissions for this revision. | Stop submitting to this project. |

### 413 Content too large

| Code | Meaning | What to do |
| --- | --- | --- |
| `payload_too_large` | The JSON body exceeds `max_json_bytes`. | Split the manifest into smaller submissions. |
| `too_many_artifacts` | The manifest lists more than `max_artifacts_per_submission` files. | Split the manifest. |
| `artifact_too_large` | A file exceeds `max_artifact_bytes`. | The project cannot take this file. |
| `image_too_large` | A decoded image exceeds `max_decoded_pixels`. | The project cannot take this file. |

### 422 Invalid data

| Code | Meaning | What to do |
| --- | --- | --- |
| `invalid_request` | The body does not match the schema. `errors` points at each bad field. | Fix the data. |
| `invalid_reference` | An ID refers to something unknown: a rig, filter, objective, assignment or panel. | Check the IDs against the project and your rigs. |
| `invalid_range` | A minimum is greater than its maximum, or a time range runs backward. | Fix the range. |
| `invalid_revision` | The project revision is unknown. | Read the project and use its revision. |
| `duplicate_id` | Two artifacts in one manifest share an ID. | Give each artifact its own ID. |
| `invalid_supersede` | `supersedes_artifact_id` names a file that is not yours or has another capture identity. | Fix the reference. |
| `invalid_part_number` | The part number is outside 1 to `part_count`. | Fix the client. |
| `part_size_mismatch` | A part is not `part_size_bytes` long, or the last part has the wrong remainder. | Split the file as the session says. |
| `digest_mismatch` | A part's bytes do not match `X-Part-SHA256`. The part was not recorded. | Send the part again. |
| `capture_deadline_passed` | A frame started after the project's capture deadline. | Leave it out. |
| `deliverable_mismatch` | A sub sent to a masters project, or a master to a subs project. | Send what the project's `deliverable` asks for. |
| `too_few_subs` | A master has fewer subs than `master_rules.min_sub_count`. | Stack more subs first. |
| `invalid_stack` | A master's `sub_count`, sub list or integration do not agree. | Fix the stack block. |
| `drizzle_not_allowed` | A drizzled master went to a project that does not accept drizzle. | Send an undrizzled master. |
| `external_delivery_not_accepted` | The project does not accept shared files from this provider. | Upload the file instead. |

### 429 and 5xx

| Status | Code | Meaning | What to do |
| --- | --- | --- | --- |
| `429` | `rate_limited` | Too many requests. | Wait `Retry-After` seconds. |
| `500` | `internal_error` | The server failed. | Retry the same request later. |
| `503` | `unavailable` | The server is down for a while. | Retry the same request later. |

## Check-in `wait` reasons

A check-in that returns `wait` lists why in `reason_codes`.

| Reason | Meaning | What to do |
| --- | --- | --- |
| `rig_incomplete` | The rig lacks what planning needs: sensor size, pixel size, focal length, color state or a filter. | Fill in the rig, on the web or with `PATCH`. |
| `no_active_projects` | The account is not an active member of any project the key covers. | Join a project on the web. |
| `no_matching_filter` | No filter on the rig serves any objective. | Check the rig's filters and passbands. |
| `sampling_out_of_range` | The rig's sampling suits no objective. | Try other binning or optics. |
| `color_state_mismatch` | The projects want mono data and the rig is color, or the reverse. | Use another rig. |
| `target_too_low` | No target rises high enough at the rig's site. | Check again later in the season. |
| `goals_met` | The project has closed after meeting its goals. | Nothing to do here. |

While a project is open, servers SHOULD keep assigning useful work rather than
wait: more data improves the picture. A rig may get a panel other rigs also
hold, or a panel that has already met its goal; data past a goal is surplus,
credited to the contributor.

## File rejection reasons

When the server rejects a file, its result in `GET /submissions/{id}` lists why
in `reason_codes`.

| Reason | Meaning |
| --- | --- |
| `digest_mismatch` | The file's bytes do not match its manifest hash. |
| `bandpass_mismatch` | The file's passbands serve none of the objectives it names. |
| `exposure_out_of_range` | The exposure is outside the objective's range. |
| `sampling_out_of_range` | The image's sampling is outside the processing group's range. |
| `coverage_too_low` | The frame covers too little of the panel it names. |
| `fresh_solve_missing` | The objective needs a fresh solve of the submitted pixels and none was given. |
| `required_measurement_missing` | A required quality measurement is missing. |
| `quality_limit` | A quality measurement is outside the objective's limits. |
| `duplicate_capture` | A sub in this file was already credited, alone or in another master. |
| `external_unavailable` | A shared file could not be fetched. |

A replaced file shows the state `superseded`, not a rejection.
