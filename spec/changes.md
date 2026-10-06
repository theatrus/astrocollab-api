# Draft changes

## 0.2.0-draft.1 — unreleased

Adopt the collaboration protocol Starfront already speaks, with its paths, as the
API. Starfront's capture app and server are the first implementation.

- **Projects** are a region of sky, `single` or `mosaic`, with depth goals in hours
  per filter at every point, and rules in units that mean the same on every rig:
  focal length or scale, colour cameras, star size and guiding in arcseconds,
  sub length, filters with bandpass limits, Moon limits, lowest altitude and the
  fewest frames per visit.
- **Each telescope tiles a mosaic with its own camera.** Shared grids and
  per-panel goals are gone; depth is integration time at a point on the sky,
  built from the solved footprints of accepted nights.
- **Tonight's list** is dealt once per night the rig names, and held: one filter a
  night on a mosaic, chosen by the rig's Moon and what is thinnest; panels by
  where others are not tonight, where this rig has been least, and where the
  field is thinnest; never fewer than the project's frames per visit.
- **Credit comes from night reports,** judged on what the rig measured. Verdicts
  are advisory and list what could not be checked. Reporting again keeps the
  larger figure. Image files become an optional extension, still to be written.
- **Tokens:** a person signs in with the device flow and enrols telescopes, or a
  telescope pairs with a code. Person and telescope tokens never stand in for each
  other.
- **Joining** may carry the night and the Moon, as Starfront now sends, so a
  share's first list is tonight's.
- **Rigs** describe their sub length per filter (what their darks are built for),
  hours per night and time window, and may share where they point.
- **Filter names** fold to one letter: L, R, G, B, H, O, S. A rig may join with
  only some of a project's filters and is dealt only those.
- **Errors** are `{"detail": ...}`. Objects are open: unknown fields are kept or
  ignored, never refused.
- Coordinator routes, the 0.1 contributor routes and API keys, the code
  catalog, uploads, stacked masters and shared folders leave this draft.


## 0.1 revisions, superseded by 0.2

These changed draft 0.1 after its first release. Draft 0.2 replaces the 0.1
contributor API that they describe.

- List every error code, check-in `wait` reason and file rejection reason in
  `spec/codes.md`. Servers keep assigning work after goals are met; more data is
  welcome. How long a capture report counts is up to each server.
- Generate standalone JSON Schemas (`schemas/`) and a Markdown REST reference
  (`spec/api.md`) from the contract, so clients need no OpenAPI tools.
- Check-ins report everything captured but not yet submitted, as per-panel
  totals in `unsubmitted_captures`. Progress shows them as `reported_frames`;
  they steer assignments but earn no credit.
- Cut the API to what a contributor's client calls: 15 operations. Project
  setup, membership changes, review, manual assessment, retrieval, capacity,
  planning policies, activity and snapshot sync move to the server's own tools.
  Users join projects on the web; `GET /me/projects` lists them.
- Check-ins return assignments: `image`, `continue` or `wait`. The server decides
  what each rig images; there is no proposal, review or automatic-eligibility
  step. `Recommendation` becomes `Assignment`.
- Remove ETags, `If-Match`, `If-None-Match` and `304`. Remove scopes: a key acts
  for its account, optionally limited to some projects.
- Rename calibration `masters` to `calibration_frames`.
- Replace OAuth and participation tokens with API keys. Users create keys on the
  server's account pages; one key works on account and project routes, limited
  by the participant's role. Remove `POST`, `GET /participations/{id}/tokens`,
  `DELETE /participations/{id}/tokens/{token_id}`, their schemas, and the
  `oauth_issuer`, `oauth_metadata_url` and `account_resource` capability fields.
- Define passband matching, and make every panel of a target need the
  objective's full depth. Check-ins report captured frames per panel.
  Assessments of masters omit capture IDs.
- Give each filter a list of passbands, with optional width, kind, maker and
  model, so dual-narrowband filters on color cameras describe both bands.
  Objectives and manifests also list passbands.
- Make every rig field except `name` optional, since users may enter details on
  the web. Add `PATCH /me/equipment/{id}` (JSON merge patch). Pairing codes
  issued for a rig return its `equipment_id`. Hand-out uses every advertised rig
  capability.
- Let an external location point at a shared folder, with `path` naming the
  file, for subs and masters alike.
- Make rigs belong to the account. Register once with `PUT /me/equipment/{id}`;
  ask for work with `POST /me/checkins`, and the server picks the project and
  panel. Rigs get single panels of the shared picture, not whole mosaics;
  `Panel.layout` places a panel in the target's grid.
- Add `deliverable` to requirements: `calibrated_subs` or `stacked_masters`.
  Masters carry `stack` provenance listing every sub; credit counts subs either
  way.
- Remove jobs: finalize returns the submission, and check-ins return the plan.
  Remove `POST /projects/{id}/recommendations`, `GET /jobs/{id}`, intents and
  live status; adopted plans and shared check-ins replace them.
- Collapse scopes to `read`, `contribute` and `manage`. Remove `x-scope-rules`.
- Remove the target and objective list routes and the revision reads for rigs,
  capacity and planning policy.
- Make snapshot sync an optional feature, `sync`.
- Allow `http://` loopback URLs for `api_root` and `account_url`. Make
  `coverage_fraction` optional.
- Remove `Idempotency-Key`. Every mutation is safe to retry by design:
  client-chosen IDs, one participation per project, target states, `If-Match`
  on shared resources, and same-job finalization. Drop
  `idempotency_retention_seconds`.
- Drop preconditions on a participant's own equipment, capacity, planning policy
  and intents; `PUT` creates or replaces them.
- Remove `POST /uploads/{id}/renew`. Each part write extends the session.
- Make framing recommendations core. A check-in now needs only equipment and
  returns the plan directly with `recommendation_ready`; `unavailable` advice is
  gone. Capacity and planning policy are optional inputs. A capacity offer only
  informs the project team.
- Let users join projects on the server's web pages; clients find them with
  `GET /me/participations`.
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
