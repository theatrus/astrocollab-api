# AstroCollab API

Shoot one deep target with other astrophotographers. A project, such as a
600-hour H-alpha mosaic of M31, sets the targets, filters and hours each panel
needs. At dusk each rig asks the server what to shoot and gets the panel and
filter that suit its field of view. Calibrated subs, or masters you stack
yourself, come back to the server, and the good ones go into one stack that no
single backyard could build.

This repository specifies the API that capture software uses: 15 REST calls to
pair a rig, ask for work and upload subs. Servers run signup, projects and
review with their own tools.

**0.1.0-draft.1: a specification, not a product.** AstroCollab is meant to be
built into capture software people already use, such as N.I.N.A., and into
servers that run projects. No capture software or public server supports it yet.
This repository holds the specification, plus an example server and client for
testing and for implementers to read. The draft may change incompatibly.

[Documentation and API reference](https://theatrus.github.io/astrocollab-api/)

- [How the API works](spec/overview.md): one contributor's requests, step by step.
- [Authentication](spec/authentication.md): pairing and API keys.
- [Walkthrough](spec/walkthrough.md): every example payload, in order.
- [Protocol](spec/protocol.md): the rules servers and clients must follow.
- [REST reference](spec/api.md): every route, its bodies, responses and examples.
- [JSON Schemas](schemas): one standalone JSON Schema 2020-12 file per type.
- [Codes](spec/codes.md): every error code, check-in `wait` reason and rejection reason.
- [TypeSpec source](typespec) and generated [OpenAPI](openapi/astrocollab.yaml).
- [Reference server](reference/README.md) and example client.
- [Conformance tester](conformance/README.md): checks servers and clients.
- [JSON examples](examples): validated request and response payloads.
- [Conformance](spec/conformance.md): required implementation tests.
- [PSF Guard integration](integrations/psf-guard.md): proposed client adapter.

## Behavior

Each project has one server. Contributors keep control of their equipment; the
server assigns work but never commands a rig or reserves a target. It credits
only assessed data and counts each sub once, whether it arrives alone or inside
a master.

The protocol originated in PSF Guard. It requires no particular client,
scheduler, camera software or catalog format.

## Validation

Use Python 3.12 or later:

```sh
python -m venv .venv
# Activate .venv for your shell.
python -m pip install -r requirements-dev.txt
python tools/validate.py
```

This checks OpenAPI, references, schemas, examples and rejection fixtures.
To test a server or client, use the [conformance tester](conformance/README.md).

## Editing the contract

Edit the TypeSpec files in `typespec/`, not the generated OpenAPI file. Requires
Node.js 20 or later:

```sh
(cd typespec && npm ci)
python tools/build_openapi.py
python tools/validate.py
```

The build compiles TypeSpec and writes three outputs: the OpenAPI file, with
every object schema closed and examples from `examples/`; standalone JSON
Schemas in `schemas/`; and the Markdown REST reference, `spec/api.md`. The
schemas and the reference need no OpenAPI tools. CI fails if any output is
stale.

## Documentation site

The [site](https://theatrus.github.io/astrocollab-api/) renders the API reference
from OpenAPI and guides from Markdown. It uses no external fonts, CDN or analytics.

```sh
python tools/build_site.py
python -m unittest discover -s tests -p 'test_*.py'
python -m http.server 8000 --directory _site
```

Open `http://localhost:8000`. Edit site presentation in `site/`, API definitions
in `typespec/`, and guides in `spec/`. `_site/` contains generated output.
GitHub Actions checks pull requests and publishes `main` to GitHub Pages.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The specification and examples use the
[MIT license](LICENSE).
