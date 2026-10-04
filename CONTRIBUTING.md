# Contributing

This repository contains the specification and documentation site. Agree on
scope before adding an API implementation. Keep client-specific adapters under
`integrations/`.

Use direct, technical English. Name the actor and action. Remove slogans,
repetition and claims that add no requirement. Keep protocol terms, units,
authorization rules and failure behavior explicit.

For a contract change, update the relevant section of `spec/protocol.md`, the
OpenAPI schema, JSON examples and conformance scenarios together.
Add rejection fixtures for new structural constraints. Specify authorization,
retry behavior and state changes for every mutation. Preserve nonexclusive
participation and assessment before credit.

Run `python tools/validate.py` and `git diff --check`. Report these as contract
checks, not implementation tests. Real credentials, sites, images and personal
data do not belong in fixtures. Use reserved example domains.

During draft development, record material compatibility changes in
`spec/changes.md`. A released major wire version must not acquire new required
fields or silently reinterpret existing units or state transitions.
