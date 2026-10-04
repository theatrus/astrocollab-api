# Contributing

This repository specifies a protocol. Do not add an application or deployment
without first agreeing on its scope. Keep vendor-specific mappings under
`integrations/` and the public wire contract independent of those mappings.

For a contract change, update the relevant section of `spec/protocol.md`, the
OpenAPI schema, connected JSON examples, and conformance scenarios together.
Add rejection fixtures for new structural constraints. Specify authorization,
retry behavior and lifecycle effects for every mutation. Never add target locks
or equate receipt of bytes with accepted astronomical evidence.

Run `python tools/validate.py` and `git diff --check`. Report these as contract
checks, not implementation tests. Real credentials, sites, images and personal
data do not belong in fixtures. Use reserved example domains.

During draft development, record material compatibility changes in
`spec/changes.md`. A released major wire version must not acquire new required
fields or silently reinterpret existing units or state transitions.
