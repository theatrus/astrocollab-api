# PSF Guard integration

Proposed PSF Guard adapter; not implemented.

- Map `(server_id, project_id)` to the existing global Library project and keep
  remote requirement revisions apart from local plans.
- Pair each registered rig with the server, so each has its own key. Bind it to
  the existing rig and catalog; do not create a second local rig identity.
  Convert TS RA hours to ICRS degrees at the boundary.
- Register each rig's sensor, optics and filters with `PUT /me/equipment/{id}`,
  and use `PATCH` when local settings change, keeping what the user entered on
  the server's web pages.
- Check in from the Director at safe boundaries. Treat an assignment as work the
  server wants, then let the existing planning core and local safety rules
  decide when and whether to run it. An assignment never grants launch authority
  and never overrides commissioned limits.
- Preserve local Director grants, launch ledgers, hardware ownership and safe
  execution boundaries. Assignments reserve nothing.
- Keep the assignment and panel IDs with every capture.
- Read FITS/XISF through `image_io`; preserve physical pixel units. Generate
  calibrated subs, or stacked masters when the project asks for them, with
  source, calibration-frame and algorithm fingerprints beneath the database
  slug. Publish files atomically and keep raw files unchanged.
- Prefer a durable PSF Guard submission queue. A Director background worker may
  submit through the same API once calibration exists, using the rig's key and
  the same capture identity. Never upload on the exposure path.
- Show the server's results beside local grades; do not overwrite local grades
  or reject reasons when a project rejects or no longer needs a frame.
- API keys never authorize local management or database changes.

Keep the Director API and peer database transfer separate. Implement and test
this adapter in PSF Guard.
