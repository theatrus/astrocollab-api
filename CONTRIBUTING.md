# Contributing

This repository contains the specification and documentation site. Agree on
scope before adding an API implementation. Keep client-specific adapters under
`integrations/`.

Use direct, technical English. Name the actor and action. Remove slogans,
repetition and claims that add no requirement. Keep protocol terms, units,
authorization rules and failure behavior explicit.

The protocol follows what Starfront's server speaks. For a contract change,
update the relevant section of `spec/protocol.md`, the TypeSpec source, the
examples and the conformance scenarios together, and say whether Starfront
already behaves that way.

Examples are captured from Starfront's server: run
`tools/capture_starfront_examples.py` with a Starfront checkout's Python. Write
an example by hand, in `examples/extra/`, only for a route Starfront lacks.
Regenerate `openapi/astrocollab.yaml`, `schemas/` and `spec/api.md` with
`python tools/build_openapi.py`; never edit them by hand.

Add rejection fixtures for new structural constraints. Specify which token each
route takes, how a repeat behaves, and what changes. Keep person and telescope
tokens apart, and keep verdicts advisory.

Run `python tools/validate.py`, the unit tests and `git diff --check`. The
conformance tests run against the reference server, and against Starfront's
server when a checkout is present. Real credentials, sites, images and personal
data do not belong in fixtures. Use reserved example domains.

During draft development, record material compatibility changes in
`spec/changes.md`. A released major wire version must not acquire new required
fields or silently reinterpret existing units or state transitions.
