# Conformance tester

These tools check an AstroCollab server or client against the
[OpenAPI contract](../openapi/astrocollab.yaml) and the
[conformance scenarios](../spec/conformance.md). They need Python 3.12 or later
and the packages in `requirements-dev.txt`.

## Test a server

Create three accounts on a test server, each with an API key. The suite creates
projects and uploads synthetic frames, so do not run it against a live service.

```sh
python -m conformance.server_suite \
  --api-root https://collab.example/v1 \
  --owner-key OWNER_KEY \
  --participant-key ALICE_KEY \
  --second-participant-key BOB_KEY
```

The owner key creates and publishes projects. The participant keys join them
and submit frames. Without the second participant key, the privacy and
no-lock checks are skipped.

To test pairing, add `--pairing-code CODE` with an unused code from the server's
account pages. The suite checks that the code yields a working key, that the
response has `Cache-Control: no-store`, and that the code fails a second time.
Add `--second-pairing-code CODE`, for the same account, to check that pairing
the same installation again revokes the first key. Without these flags the
pairing checks are skipped. Add `--allow-http-loopback` for a local server on
`http://127.0.0.1`.

The suite prints one line per check:

```text
PASS publish_needs_draft_etag [Publication]
FAIL changed_part_conflicts [Multipart]: PUT /uploads/.../parts/1 returned 200, expected 409 part_conflict
SKIP overlapping_intents_both_accepted [No locks]: needs --second-participant-key
```

The bracket names the row in `spec/conformance.md`. A check fails if the server
returns the wrong status or code, or if any response breaks the contract. The
suite exits with status 1 if any check fails.

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
- lacks `Authorization`, `Idempotency-Key`, `If-Match` or other required headers;
- sends both or neither of `If-Match` and `If-None-Match: *` on a create-or-replace;
- sends a body that breaks its schema, or a part whose length or SHA-256 is wrong;
- reuses an `Idempotency-Key` with a different body;
- puts a credential in the URL.

It rewrites `api_root` in `/capabilities` so the client keeps using the proxy.

## What these tools do not check

Black-box tests cannot see everything. These rows of `spec/conformance.md`
need implementation tests of their own:

- Automatic framing, Automatic bounds, Acquisition boundary and No useful work:
  these depend on recommendations and local client policy.
- Mosaic, Geometry and Quality: these need real images and sky coverage.
- Budgets: this covers time across rigs and servers.
- Partial batches, Attribution and Terms/closure.
- Tombstones and revoked access during snapshot paging.

The suite covers parts of other rows. For Enrollment it does not test
approval-based enrollment or revocation. For Finalize races it sends repeated
finalize requests in sequence, not at the same moment. For Abuse it only checks
that unknown fields are rejected. For External delivery it records retrieval
results itself; it cannot check that a maintainer fetched the shared file.

The proxy sees one client's traffic. It cannot tell whether the client applies
framing at a safe boundary, keeps local captures after losing access, or stores
keys safely.
