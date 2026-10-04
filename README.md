# AstroCollab API

An API specification for collaborative astrophotography. Project owners define
required frames. Participants offer equipment and time, receive framing
recommendations, and submit calibrated exposures for assessment.

**0.1.0-draft.1.** No API implementation yet. The draft may change incompatibly.

[Documentation and API reference](https://theatrus.github.io/astrocollab-api/)

- [Protocol](spec/protocol.md): authorization, planning, sync, uploads and credit.
- [OpenAPI](openapi/astrocollab.yaml): endpoints, schemas and examples.
- [Walkthrough](spec/walkthrough.md): project creation through submission.
- [JSON examples](examples): validated request and response payloads.
- [Conformance](spec/conformance.md): required implementation tests.
- [PSF Guard integration](integrations/psf-guard.md): proposed client adapter.

## Behavior

Each project has one server. Participants retain local equipment control.
Tokens permit project access and submissions; they grant no target locks.
The server credits only assessed, compatible captures and counts each exposure
once in project integration totals.

Equipment registration and check-ins can recommend new framing as coverage needs
change. Participants can approve automatic updates within equipment, sky-region,
rotation and monthly-time limits.

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
It does not test a server implementation. CI runs the same checks.

## Documentation site

The [site](https://theatrus.github.io/astrocollab-api/) renders the API reference
from OpenAPI and guides from Markdown. It uses no external fonts, CDN or analytics.

```sh
python tools/build_site.py
python -m unittest discover -s tests -p 'test_*.py'
python -m http.server 8000 --directory _site
```

Open `http://localhost:8000`. Edit site presentation in `site/`, API definitions
in `openapi/`, and guides in `spec/`. `_site/` contains generated output.
GitHub Actions checks pull requests and publishes `main` to GitHub Pages.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The specification and examples use the
[MIT license](LICENSE).
