# Reference server and client

This is an example for testing and for implementers to read, not a server to
run for real projects. The protocol is meant to be built into existing capture
software, such as N.I.N.A., and into servers that host projects.

A small in-memory server and a walkthrough client for the AstroCollab
contributor API. Read them to see how the rules in
[the protocol](../spec/protocol.md) fit together. Do not deploy the server: it
keeps all state in memory, serves plain HTTP and has no web pages.

The server loads [the contract](../openapi/astrocollab.yaml) at start. It uses
it to route requests and validate request bodies, so the Python code holds only
the rules a schema cannot express. For each route's fields and errors, see the
[REST reference](../spec/api.md); for payload shapes, the JSON Schemas in
[schemas/](../schemas).

## Run it

Use Python 3.12 or later and install `requirements-dev.txt` (PyYAML and
jsonschema). From the repository root:

```sh
python -m reference.server --port 8080
```

With no options, the server creates the accounts `alice` and `bob`, prints an
API key and a pairing code for each, loads the [sample projects](#sample-projects)
and makes both accounts active members. To choose the secrets:

```sh
python -m reference.server --port 8080 \
    --key alice=ALICE_SECRET --pairing-code bob=acpc_CHOOSE_A_LONG_CODE
```

`--key` creates an account with an API key. `--pairing-code` issues a
single-use code that a client trades for a key at `POST /pair`; codes expire
after an hour. Every account named either way joins the sample projects.
`--join ACCOUNT=PROJECT` adds a membership. `--check-responses` validates every
response against the contract, `--verbose` logs requests and `--no-sample`
skips the sample projects.

In another terminal, run the walkthrough:

```sh
python -m reference.client --api-root http://127.0.0.1:8080/v1 --key ALICE_SECRET
```

Use `--pairing-code` instead of `--key` to pair first. The client lists your
projects, describes a rig, checks in, submits one exposure for the panel it was
given, finalizes, polls the submission, reports frames not yet submitted and
reads progress:

```text
# Ask what to image
POST /me/checkins -> 200  Check in; the server assigns a panel.
  Rig 400 mm: image North America and Pelican nebulae, 1 panel 3.4°×2.2°, H-alpha, 180 × 300 s
…
# Report frames not yet submitted, and keep going
POST /me/checkins -> 200  Check in with 5 frames waiting.
  action: continue
GET /projects/…/progress -> 200  Read the totals.
  accepted 1 frames (300 s), 5 reported, goal 144 frames on each panel
```

The manifest comes from [examples](../examples); the client fills in the IDs
the server handed out.

## Sample projects

At start the server creates three projects from `reference/sample_project.json`.
Each entry is a project ID and a RequirementSet.

"Sample sky survey" takes calibrated subs. Its targets differ in size so rigs
with different focal lengths get different work:

| Target | Size | Bands | Sampling |
| --- | --- | --- | --- |
| North America and Pelican nebulae | 3°×2° | H-alpha, OIII (mono and one-shot color) | 1.4–4″/px |
| Veil Nebula complex | 3°×3° | OIII, H-alpha | 1.4–4″/px |
| M31 | 3°×1°, angle 35° | Luminance | 1.4–4″/px |
| M42 | 1°×1° | H-alpha, short luminance | 0.3–1″/px |
| M51 | 0.2°×0.15° | Luminance | 0.3–1″/px |
| NGC 7662 | 0.05° | OIII | 0.3–1″/px |

"Sample masters" takes stacked masters of the Heart Nebula, the Rosette
Nebula and M33: at least 10 subs per master, no drizzle.

"Sample shared files" takes calibrated luminance subs of the Pleiades shared
through Google Drive or an HTTPS link (external delivery) instead of uploads.

To see three rigs get work on a fresh server:

```sh
python -m reference.client --sample --key ALICE_SECRET
```

```text
Rig 400 mm: image North America and Pelican nebulae, 1 panel 3.4°×2.2°, H-alpha, 180 × 300 s
Rig 2000 mm A: image M42 Orion Nebula, panel 1 of 6 (column 1, row 1), H-alpha, 300 × 120 s
Rig 2000 mm B: image M42 Orion Nebula, panel 2 of 6 (column 2, row 1), H-alpha, 300 × 120 s
```

Each rig uses a 6248×4176 mono camera with 3.76 µm pixels and H-alpha, OIII
and luminance filters: one on a 400 mm f/5 refractor, two on 2000 mm SCTs. The
two long rigs get different panels of the same mosaic.

## How the server assigns work

`POST /me/checkins` names one rig. The server considers the account's active
projects (narrowed by the key's projects and the request's `project_ids`),
plans for each, and assigns the work with the largest remaining deficit. The
rules live in [planning.py](planning.py):

1. The rig must state sensor size, pixel size, focal length, color state and
   at least one filter. Otherwise the answer is `wait` with `rig_incomplete`.
   Binning defaults to 1, rotation to fixed at 0°.
2. Field of view per axis is 2·atan(pixels × pixel size ÷ (2 × focal length)),
   from unbinned pixels. Sampling is 206.265 × pixel µm × binning ÷ focal mm.
3. A filter serves an objective when one of its passbands matches one the
   objective accepts: the center lies inside the accepted band and, when both
   widths are known, the filter's width is between a quarter of the accepted
   width and the full accepted width. With no accepted width, centers must be
   within 5 nm. So narrowband filters do not serve luminance goals.
4. The objective must also match the rig's color state and sampling, and,
   when the rig gives a site latitude, its target must rise above 30°
   (90° − |latitude − declination|).
5. The project owns each mosaic. A target can have several grids of panels
   with 15% overlap, one per panel size. A rig works on the grid with the
   largest panels that still fit its field; if none fits, the server lays a
   new grid sized to that rig. No rig gets a panel larger than its field. A
   target that fits a field with 10% to spare gets one panel. Fixed cameras
   keep their angle: the grid covers the target as that camera sees it.
6. Each panel needs the objective's full goal. The objective is complete when
   every panel of one grid is. Frames that name no panel count toward the
   whole target only until a grid exists.
7. The rig gets the panel that needs the most frames, after subtracting other
   rigs' live assignments and their reported, unsubmitted frames. One panel is
   the usual assignment; the next is added only if the first would finish
   within a 6-hour night (at most 3).
8. More data is always good. While a project is open, a rig whose only options
   have met their goals, or whose panels other rigs already hold, still gets
   work: the least-deep panel, for one night. Frames past a goal are surplus,
   credited to the contributor.
9. Exposure is the objective's minimum. Suggested frames close the panel's
   deficit, assuming 80% pass. Estimates add 20% overhead.
10. A dual-band filter serves several objectives at once: the panel lists every
    objective on the same target and processing group that the filter covers,
    and an accepted frame is credited to each.
11. When nothing fits, the answer is `wait` with a reason from
    [spec/codes.md](../spec/codes.md): `rig_incomplete`, `no_active_projects`,
    `no_matching_filter`, `sampling_out_of_range`, `color_state_mismatch` or
    `target_too_low`. `goals_met` means the project has closed after meeting
    its goals.

Check-ins are safe to repeat. Unchanged work comes back as the same
assignment; once the rig names it in `assignment_id`, the answer is
`continue`. `unsubmitted_captures` is a total per panel that replaces the
rig's last report, so a repeat changes nothing. Reported frames show in
progress as `reported_frames` and steer other rigs away from that panel; they
earn no credit. Frames leave the report when a submission names their panel,
and reports stop counting after the submission deadline.

## Submissions and credit

- **Uploads:** fixed-size parts, part hashes, out-of-order parts,
  `part_conflict` and `upload_incomplete`. Each accepted part extends the
  upload's expiry. Finalizing returns the submission in `processing`; poll it
  until `complete`. A repeated create with the same ID and body returns 200
  with the submission; a different body returns `409 id_conflict`.
- **Codes:** errors, `wait` reasons and rejection reasons follow
  [spec/codes.md](../spec/codes.md). A key limited to other projects, or an
  inactive membership, gets `403 membership_inactive`.
- **Automatic assessment:** the server checks the file hash, passbands,
  exposure range, the rig's sampling against the processing group,
  fresh-solve evidence and the manifest's measurements. When
  the frame names a panel and carries a solve, the solved center must cover
  the panel by at least the objective's `minimum_coverage_fraction`
  (`coverage_too_low`).
- **Credit:** accepted frames count toward the panel they name. Once a panel
  has the full goal, more frames there are surplus: accepted, but with no
  `credited_frames`.
- **Deliverables:** a project takes calibrated subs or stacked masters, not
  both (`422 deliverable_mismatch`). A master needs `sub_count` equal to its
  subs and at least the minimum (`422 too_few_subs`), integration equal to
  subs × exposure (±1 s) and first and last times that match its subs
  (`422 invalid_stack`), and no drizzle unless allowed
  (`422 drizzle_not_allowed`). An accepted master credits `sub_count` frames.
- **Unique captures:** each capture, alone or inside a master, earns credit
  once per project. A later artifact with a credited capture is rejected with
  reason `duplicate_capture` in its result; the `assess_artifact` tool
  refuses it with `409 duplicate_capture`.
- **External delivery:** when a project revision lists providers, an artifact
  may point to a shared file (`url`, optional `path`). It gets no upload
  session and waits in `awaiting_retrieval` until a maintainer records the
  fetch with `record_retrieval`.

## Server tools

A real server manages projects, members and reviews in its own web pages. This
one exposes them as methods on `httpd.api`:

```python
import threading
from reference.server import serve

httpd = serve(port=0, keys={"alice": "ALICE_SECRET"}, check_responses=True)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
api = httpd.api

project_id = api.create_project(requirements)        # Publish revision 1; state open.
api.publish(project_id, new_requirements)              # Publish the next revision.
api.join("bob", project_id)                            # Active member; consents to current terms.
code = api.issue_pairing_code("bob", equipment_id=rig)  # As the account pages would.
api.assess_artifact(artifact_id, "rejected", ["quality_limit"])
api.record_retrieval(artifact_id, "verified", sha256_hex, size_bytes)
```

`issue_pairing_code(account, code=None, project_ids=None, key_ttl=None,
equipment_id=None)` takes the choices a user makes when issuing a code: a
project list, a key lifetime and the rig the code is for. `serve()` joins every
account in `keys` to the sample projects; `httpd.sample_project_ids` lists them.
When a project's terms change, call `join` again to record consent; until then
submissions get `409 terms_consent_required`.

## What it leaves out

- No image decoding or quality measurement: the server trusts the manifest's
  measurements and solve, so it never returns `413 image_too_large`.
- No fetching: it never opens external URLs and publishes no
  `external_retrieval_hosts`.
- No web pages, signup or key list. Use `--key`, `--pairing-code`, `--join` or
  the server tools.
- No HTTPS. The contract allows `http://` only on loopback hosts; real servers
  must use HTTPS.
- No visibility windows beyond the latitude check: no time of night, moon or
  horizon. No expiry for stale `unsubmitted_captures` reports.
- No quotas, rate limits or upload staging cleanup, and no persistence.

## Tests

```sh
python -m unittest discover -s tests -p 'test_reference.py'
```

The tests start the server on a free port, run the walkthrough, check in rigs
against the sample projects, and probe keys, rigs, uploads, coverage, masters,
external delivery and the server tools. The server checks every response
against the contract during the tests.
