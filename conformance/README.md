# Conformance tester

These tools check an AstroCollab server or client against the
[OpenAPI contract](../openapi/astrocollab.yaml) and the
[conformance scenarios](../spec/conformance.md). They need Python 3.12 or later
and the packages in `requirements-dev.txt`.

## Test a server

The API serves contributors only, so the suite cannot create projects. Before
you run it, prepare on a test server:

- an open project that takes calibrated subs;
- optionally, an open project that takes stacked masters, and one that accepts
  external delivery;
- one or two accounts that are active members of those projects, each with an
  API key.

The suite registers rigs and uploads synthetic frames. Do not run it against a
live service.

```sh
python -m conformance.server_suite \
  --api-root https://collab.example/v1 \
  --participant-key ALICE_KEY \
  --project-id SUBS_PROJECT_ID \
  --second-participant-key BOB_KEY \
  --masters-project-id MASTERS_PROJECT_ID \
  --external-project-id EXTERNAL_PROJECT_ID
```

The suite reads each project's requirements and builds rigs and manifests to
match its first objective. Checks that need an optional flag are skipped
without it. To test pairing, add `--pairing-code CODE` with an unused code from
the server's account pages, and `--second-pairing-code CODE` for the same
account. Add `--allow-http-loopback` for a local server on `http://127.0.0.1`.
Add `--schemas schemas` to validate bodies against the standalone JSON Schemas
instead of the OpenAPI components; the results should be the same. The proxy
takes the same option.

The suite prints one line per check:

```text
PASS repeated_put_keeps_revision [Rigs]
FAIL changed_part_conflicts [Multipart]: PUT /uploads/.../parts/1 returned 200, expected 409 part_conflict
SKIP submissions_hidden_from_others [Privacy]: needs --second-participant-key
```

The bracket names the row in `spec/conformance.md`. A check fails if the server
returns the wrong status or code, or if any response breaks the contract. The
suite exits with status 1 if any check fails.

### What the checks cover

- **Credentials.** `/capabilities` is public. The key is an active
  member of every project named. Missing and unknown keys get `401`. A
  submission to a project the key does not cover fails.
- **Pairing.** A code yields a working key, with `Cache-Control: no-store`, and
  then fails with `invalid_pairing_code`. Pairing the same installation again
  revokes the old key.
- **Rigs.** An identical `PUT` keeps the revision; a change makes a new one. A
  `PATCH` changes one field and keeps the rest. A rig with only a name gets
  `wait` with reason `rig_incomplete`.
- **Privacy.** Another account cannot see the rig or read the submission.
- **Asking for work.** A new rig checks in with only its ID and the time, and
  gets `image` or `wait`. Every panel must fit the rig's field of view (2%
  tolerance, in either orientation), name the rig and one of its filters, keep
  exposures within the objective's range and suit its sampling. The filter must
  serve each listed objective by the passband rule in protocol §6: a passband's
  center lies inside an accepted band, and its width is between ¼ and 1× the
  accepted width when both are known. Without an accepted width, centers must
  agree within 5 nm.
- **Sharing out the picture.** When the project's target is larger than a
  long rig's field, two identical rigs must get different panels. Each panel
  must still fit.
- **Reporting progress.** A rig reports 5 unsubmitted frames for its assigned
  panel; `reported_frames` rises by 5 for each objective the panel lists. The
  same report again changes nothing, and `[]` returns it to where it was.
  Reports never change accepted frames or integration.
- **Retry.** A repeated submission with the same ID and body returns the same
  submission with `200`; a changed body gets `409 id_conflict`. Finalizing
  twice returns the same submission.
- **Multipart.** Parts sent out of order are all listed. An identical retry
  returns the same receipt. Changed bytes get `part_conflict`, a wrong digest
  gets `digest_mismatch`, and finalizing early gets `upload_incomplete`.
- **Recalibration.** An accepted replacement for a capture leaves the project's
  credited captures and integration unchanged.
- **Stacked masters.** A sub sent to the masters project, or a master sent to
  the subs project, gets `deliverable_mismatch`. A master below `min_sub_count`
  gets `too_few_subs` or is rejected, and earns no credit. An accepted master
  adds one accepted frame per sub. A master that reuses a credited sub earns
  nothing new.
- **External delivery.** A project without `external_delivery` rejects
  external artifacts. On the external project, an external artifact gets no
  upload session and stays `awaiting_retrieval` after finalize, with no credit.
  Its link does not appear in public views or to another member.

## Test a client

Run the proxy between the client and a working server, such as the reference
server:

```sh
python -m conformance.proxy --listen 127.0.0.1:8081 \
  --upstream http://127.0.0.1:8080/v1 --allow-http-loopback --report report.json
```

Point the client at `http://127.0.0.1:8081/v1` and run its normal workflow.
Press Ctrl-C to stop the proxy. It prints client faults and server faults
separately and exits with status 1 if it found client faults.

The proxy reports a client fault when a request:

- uses an unknown route, method or query parameter;
- lacks `Authorization` or another required header;
- sends a body that breaks its schema, or a part whose length or SHA-256 is wrong;
- puts a credential in the URL.

It rewrites `api_root` in `/capabilities` so the client keeps using the proxy.

## What these tools do not check

Black-box tests cannot see everything. These rows of `spec/conformance.md`
need implementation tests of their own:

- Terms and deadlines: projects and memberships are managed outside the API.
- No locks, No useful work and Client duties: these depend on two clients
  uploading at once, changing demand and local client behavior.
- Geometry and Quality: these need real images and sky coverage.
- Partial batches and Attribution.

The suite covers parts of other rows. For Finalize races and Retry it repeats
requests in sequence, not at the same moment. For Asking for work and Sharing
out the picture it checks that panels fit, use valid settings and differ
between rigs, not that the server chose the best framing. For Stacked masters
it cannot check that a master's pixels combine the subs it lists. For External
delivery it stops at `awaiting_retrieval`; recording retrieval happens outside
the API. For Abuse it only checks that unknown fields are rejected.

The proxy sees one client's traffic. It cannot tell whether the client applies
framing at a safe boundary, keeps local captures after losing access, or stores
keys safely.
