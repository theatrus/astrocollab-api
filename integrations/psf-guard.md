# PSF Guard integration

Proposed PSF Guard adapter; not implemented. Starfront already implements the
same protocol, and its `astrocontrol/collabclient.py` is a working client to
read alongside this.

- Map `(server, project_id)` to the existing global Library project and keep the
  server's requirements apart from local plans.
- Sign the user in with the device flow, or pair with a code, and enrol each
  registered rig as its own telescope with its own token. Bind it to the
  existing rig and catalog; do not create a second local rig identity.
- Say hello from each rig with its optics, sensor, filters and bandpasses, the
  sub length each filter is shot at (what its dark library is built for), its
  typical star size and guiding in arcseconds, and its hours and window. Convert
  TS RA hours to degrees for regions and footprints at the boundary; presence RA
  stays in hours.
- Ask for tonight from the Director at a safe boundary, naming the night and the
  site's Moon. Treat the list as work the server wants, and let the existing
  planning core and local safety rules decide when and whether to run it. It
  never grants launch authority and never overrides commissioned limits.
- Preserve local Director grants, launch ledgers, hardware ownership and safe
  execution boundaries. The server reserves nothing.
- Keep the share and panel number with every frame.
- Report each night, filter and panel from the durable PSF Guard queue: frames,
  seconds, the solved footprint, scale, mean HFR in arcseconds, guiding, Moon,
  calibration and bandpass. Mark a panel reported only when the server has
  recorded it. Never report on the exposure path.
- Show the server's verdicts beside local grades; do not overwrite local grades
  or reject reasons.
- Telescope tokens never authorize local management or database changes.

Keep the Director API and peer database transfer separate. Implement and test
this adapter in PSF Guard.
