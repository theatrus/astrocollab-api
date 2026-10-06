# AstroCollab API

**This is a spec, not an implementation** (0.2.0-draft.1). AstroCollab is meant to be
built into capture software people already use, such as N.I.N.A., and into
servers that run projects. This draft adopts the protocol
[Starfront](https://github.com/bray-sfro/starfront) already speaks, so Starfront's
app and server are its first implementation. This repository holds the
specification, plus an independent example server, an example client and a
conformance tester. The draft may change incompatibly.

**[astrocollabapi.com](https://astrocollabapi.com/)**: guides, REST reference and JSON Schemas.

Shoot one deep target with other astrophotographers. A project, such as a
narrowband mosaic of M31's halo, sets the region of sky, the hours wanted in each
filter and the rules data must meet. Each telescope tiles the region with its own
camera. At dusk it asks which panels to shoot tonight, and gets a list held for
the night, in one filter chosen by the Moon and by what is thinnest. It reports
what it shot, the server checks the numbers, and good nights count toward one
image no single backyard could build.

This repository specifies the API that capture software uses: 16 REST calls to
get a telescope token, describe the rig, join projects, ask for tonight's work
and report it. Servers run projects with their own tools.

- [How a night works](spec/overview.md): one telescope's requests, step by step.
- [Authentication](spec/authentication.md): signing in, enrolling and pairing.
- [Walkthrough](spec/walkthrough.md): every example payload, in order.
- [Protocol](spec/protocol.md): the rules servers and programs follow.
- [REST reference](spec/api.md): every route, its bodies, replies and examples.
- [JSON Schemas](schemas): one standalone JSON Schema 2020-12 file per type.
- [TypeSpec source](typespec) and generated [OpenAPI](openapi/astrocollab.yaml).
- [Reference server](reference/README.md) and example client.
- [Conformance tester](conformance/README.md): checks servers, including Starfront's, and clients.
- [JSON examples](examples): payloads captured from Starfront's server.
- [Conformance](spec/conformance.md): required implementation tests.
- [PSF Guard integration](integrations/psf-guard.md): proposed client adapter.

## Behavior

Each project has one server. Telescopes always ask; the server never reaches into
an observatory, never moves a mount and never reserves sky. It judges every
night's report against the project's rules, and counts the same night, filter
and panel once.

The protocol requires no particular capture program, scheduler or catalog.

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
examples from `examples/`; standalone JSON Schemas in `schemas/`; and the
Markdown REST reference, `spec/api.md`.

The examples are captured from Starfront's server by
`tools/capture_starfront_examples.py`, run with a Starfront checkout's Python;
`examples/extra/` holds the few written by hand, for routes the captured server
does not use. The
schemas and the reference need no OpenAPI tools. CI fails if any output is
stale.

## Documentation site

The [site](https://astrocollabapi.com/) renders the API reference
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
