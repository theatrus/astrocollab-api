# Reference server and client

A small in-memory server and a walkthrough client for the AstroCollab draft
protocol. Read them to see how the rules in [the protocol](../spec/protocol.md)
fit together. Do not deploy the server: it keeps all state in memory, serves
plain HTTP and has no account pages.

The server loads [the contract](../openapi/astrocollab.yaml) at start. It uses
it to route requests, validate request bodies and look up each operation's
scopes. The Python code holds only the rules a schema cannot express.

## Run it

Use Python 3.12 or later and install `requirements-dev.txt` (PyYAML and
jsonschema). From the repository root:

```sh
python -m reference.server --port 8080
```

With no options, the server creates the accounts `owner`, `alice` and `bob`,
and prints an API key and a pairing code for each. To choose the secrets:

```sh
python -m reference.server --port 8080 \
    --key owner=OWNER_SECRET --key alice=ALICE_SECRET \
    --pairing-code bob=acpc_CHOOSE_A_LONG_CODE
```

`--key` creates an account with a key that holds every scope. `--pairing-code`
issues a single-use code that a client trades for a key at `POST /pair`. Codes
expire after one hour. Add `--check-responses` to validate every response
against the contract, and `--verbose` to log requests.

In another terminal, run the walkthrough:

```sh
python -m reference.client --api-root http://127.0.0.1:8080/v1 \
    --owner-key OWNER_SECRET --participant-key ALICE_SECRET
```

To pair instead of passing a key, use `--owner-pairing-code` or
`--participant-pairing-code` (alias `--pairing-code`).

The client prints each request and its status:

```text
# Owner: publish the project
POST /projects -> 201  Create a draft and owner membership.
GET /projects/…/draft -> 200  Read the draft ETag.
POST /projects/…/publish -> 201  Publish revision 1.
…
# Anyone: read progress
GET /projects/…/progress -> 200  Read accepted totals.
  accepted 1 of 120 frames, 300 s of integration
```

The request bodies come from [examples](../examples). The client swaps in new
IDs and current dates so you can run it more than once against one server.

## Use it from Python

```python
import threading
from reference.server import serve

httpd = serve(port=0, keys={"owner": "OWNER_SECRET"}, check_responses=True)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
code = httpd.issue_pairing_code("alice")  # What the account pages would do.
api_root = httpd.api.base_url + "/v1"
```

`issue_pairing_code(account, code=None, scopes=..., project_ids=None, key_ttl=None)`
also takes the choices a user makes on the account pages: scopes, a project
list and a key lifetime.

## What it implements

Every operation in the contract has a handler. The server enforces:

- API keys: account routes use the key's scopes; project routes use the
  account's participation, limited to scopes that both the key and the role
  allow, and to the key's project list.
- Pairing: single-use codes stored as hashes; pairing an installation again
  revokes its old key.
- `If-Match`, `If-None-Match: *`, `428` and `412` on drafts, participations,
  offers, intents and assessments.
- `Idempotency-Key` replay and `409 idempotency_conflict`, scoped to the key.
- Enrollment states and the last-owner rule.
- Status sequence rules and private or shared activity.
- Fixed-size parts, part hashes, out-of-order parts, `part_conflict`,
  `upload_incomplete`, renewal and one job per finalized submission.
- Automatic assessment, unique credit per capture, surplus policy and
  recalibrated replacements.
- Snapshots and an ordered change feed with access-removal records.
- External delivery: a project revision may accept shared files from listed
  providers. Such artifacts get no upload session and wait for a maintainer to
  record the retrieval result; `verified` runs the normal assessment.

## What it leaves out

- No recommendations: check-in and registration return `unavailable` advice,
  and `POST /projects/{id}/recommendations` returns `404 unsupported_feature`.
- No image checks: the server does not decode FITS or XISF, compute sky
  coverage, check sampling or bandpass, or measure quality. It checks the file
  hash, exposure range, fresh-solve evidence and the measurements in the
  manifest. It records a coverage fraction of 1.0.
- No fetching: the server never opens external URLs and does not publish
  `external_retrieval_hosts`. A maintainer fetches shared files and reports
  the result.
- No account pages, signup, approval UI or key list. Use `--key`,
  `--pairing-code` or `issue_pairing_code()`.
- No HTTPS. Capabilities report `http://` roots, which the contract does not
  allow; real servers must use HTTPS.
- No quotas, rate limits, idempotency expiry or upload staging cleanup.
- No checks that capacity availability falls within the named month.
- No persistence: stopping the server loses everything.

## Tests

```sh
python -m unittest discover -s tests -p 'test_reference.py'
```

The tests start the server on a free port, run the walkthrough and probe key,
precondition, retry and upload rules. The server checks every response against
the contract during the tests.
