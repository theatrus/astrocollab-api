# AstroCollab API

An API for many astrophotographers to build one picture together. A project
sets out the picture: its targets, filters and depth. Each contributor's rig
asks the server what to image, gets the part of the picture that most needs
data and suits the rig, and sends back calibrated subs or stacked masters. The
server checks them and credits accepted data toward the shared goal.

The API covers what a contributor's client calls: 15 operations. Servers run
signup, project setup and review with their own tools.

**0.1.0-draft.1.** Only a local reference server exists. The draft may change incompatibly.

[Documentation and API reference](https://theatrus.github.io/astrocollab-api/)

- [How the API works](spec/overview.md): one contributor's requests, step by step.
- [Authentication](spec/authentication.md): pairing and API keys.
- [Walkthrough](spec/walkthrough.md): every example payload, in order.
- [Protocol](spec/protocol.md): the rules servers and clients must follow.
- [REST reference](spec/api.md): every route, its bodies, responses and examples.
- [JSON Schemas](schemas): one standalone JSON Schema 2020-12 file per type.
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
