# PSF Guard integration

This is a proposed adapter, not implemented behavior or a protocol dependency.
AstroCollab's canonical specification is independent of PSF Guard storage and
Director execution. This document holds the original client's integration notes.

- Map `(server_id, project_id)` to the existing global Library project and
  retain remote published revisions separately from local reviewed intent.
- Bind offered setups to existing registered rigs/catalogs; do not create a
  second local rig identity. Convert TS RA hours to ICRS degrees at the boundary.
- Add collaboration enrollment, monthly capacity and recommendation review to
  the existing project framing/planning flow. Keep project/database URL scope.
- Use the existing shared Rust planning core locally. Remote recommendations
  inform a draft; they never replace commissioned policy or launch authority.
- Support reviewed automatic framing: equipment registration and public project
  check-ins may update future panels without repeated prompts when they stay
  inside the user's equipment, sky-region, rotation and capacity policy. Recheck
  locally at safe boundaries and retain framing provenance for every capture.
- Preserve local Director grants, launch ledgers, hardware ownership and safe
  execution boundaries. They do not reserve public project demand.
- Read FITS/XISF through `image_io`; preserve physical pixel units. Generate
  calibrated derivatives with source/master/algorithm fingerprints beneath the
  database slug and publish files atomically. Keep raw files unchanged.
- Prefer a durable PSF Guard submission queue. A Director background worker may
  submit through the same public API once calibration exists, using its own
  token and the same origin capture identity. Never upload on the exposure path.
- Show collaboration assessments beside local grades; do not overwrite local
  grade or reject reasons when a project rejects or no longer needs a frame.
- Keep catalog activation and database changes behind existing management gates.
  Public participation tokens never authorize local management or remote sync.

The native Director API and existing trusted-peer database transfer remain
separate. This adapter requires its own implementation request, regression tests
and user documentation in PSF Guard.
