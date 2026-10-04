# Draft changes

## Unreleased

- Replace OAuth and participation tokens with API keys. Users create keys on the
  server's account pages; one key works on account and project routes, limited
  by the participant's role. Remove `POST`, `GET /participations/{id}/tokens`,
  `DELETE /participations/{id}/tokens/{token_id}`, their schemas, and the
  `oauth_issuer`, `oauth_metadata_url` and `account_resource` capability fields.
- Add external delivery. Projects may accept files shared outside the API, such
  as in Google Drive. Contributors register the file's location and hash;
  maintainers record retrieval with
  `POST /submissions/{id}/artifacts/{artifact_id}/retrieval` before assessment.
- Add `POST /pair`. Users issue a single-use pairing code on the account pages;
  each client installation trades one for its own API key.
- Write the contract in TypeSpec (`typespec/`). `openapi/astrocollab.yaml` is now
  generated, with examples taken from `examples/`. The wire contract is otherwise
  unchanged.
- Add a reference server, an example client and a conformance tester.

## 0.1.0-draft.1 — 2026-10-03

Extract the collaboration design from PSF Guard into an independent protocol.
Define project publication, participation credentials, equipment and capacity
offers, optional recommendations, nonexclusive intent, snapshot/change sync,
authenticated resumable uploads, versioned assessments and capture credit.

The earlier PSF Guard design's `/api/collab/v1` route sketches were not an
implemented interface. This draft uses a discovered API root, illustrated as
`https://collab.example/v1`. It specifies authenticated chunk uploads rather
than requiring a storage provider's signed-URL protocol.
