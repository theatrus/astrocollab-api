# AstroCollab API

An open protocol for collaborative astrophotography: publish observing goals,
volunteer equipment and time, coordinate without target locks, and contribute
calibrated exposures with traceable quality and credit.

**Status: 0.1.0-draft.1 — specification only.** There is no hosted service,
server implementation, client SDK or claim of runtime conformance here.
Breaking changes are expected while the draft is reviewed. `/v1` is the proposed
first wire version, not a released compatibility promise.

**[Read the documentation and API reference →](https://theatrus.github.io/astrocollab-api/)**

AstroCollab stands for astrophotography collaboration. It is independent of
PSF Guard, Director, N.I.N.A. and any catalog format. Its first design grew out
of PSF Guard's acquisition planning work; this repository owns the protocol.

- [Protocol specification](spec/protocol.md): authority, authentication,
  lifecycle, planning, sync, transfers and acceptance.
- [OpenAPI 3.1.1 contract](openapi/astrocollab.yaml): routes, payload schemas,
  bounds, security contexts and examples.
- [Payload walkthrough](spec/walkthrough.md): a connected example from project
  publication to credited calibrated data.
- [JSON payloads](examples): synthetic, schema-checked request and response files.
- [Conformance scenarios](spec/conformance.md): behavior a future implementation
  must prove beyond structural schema validation.
- [PSF Guard integration](integrations/psf-guard.md): one client's mapping.

## Core contract

A collaboration server owns a project's published requirements and assessments.
A participant owns their equipment, local scheduling and upload consent.
Participation tokens authorize project access and submissions; **they never
reserve a target or authorize equipment control**. Multiple volunteers may
observe the same objective. Only assessed, compatible captures earn credit.

Projects specify footprint, filters, exposure goals, calibration and quality.
Participants offer equipment and monthly rig-hours. Optional recommendations
suggest framing, mosaics and useful objectives. Calibrated subs retain origin
capture identity so retries, alternate clients and recalibration cannot multiply
integration time.

Equipment registration and routine check-ins can refresh framing automatically.
An opt-in policy lets a participant set approved equipment, regions and limits
once, then follow changing project coverage needs without reviewing every panel.

## Validate the specification

Python 3.12 or later:

```sh
python -m venv .venv
# Activate .venv using your shell's normal command.
python -m pip install -r requirements-dev.txt
python tools/validate.py
```

Validation checks the OpenAPI document, local references, every schema and
example, operation example coverage and negative payload fixtures. It does not
test a running service. CI runs the same command.

## Documentation site

The site at [theatrus.github.io/astrocollab-api](https://theatrus.github.io/astrocollab-api/)
combines an overview, guides and a searchable API reference. It is a static
documentation site, not an API server. The reference loads the canonical OpenAPI
contract, and guides are rendered from the Markdown files above. No external
fonts, JavaScript CDN or analytics are required.

```sh
python tools/build_site.py
python -m unittest discover -s tests -p 'test_*.py'
python -m http.server 8000 --directory _site
```

Open `http://localhost:8000`. Edit presentation in `site/`; edit the public
contract in `openapi/` and guides in `spec/`. Never edit generated `_site/` files.
GitHub Actions checks pull requests and publishes `main` to GitHub Pages.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Discuss wire changes here before adapting
clients. Deployment choices such as account verification, storage quotas and
data licensing belong to each service and must be disclosed through the API.
Specification and examples are licensed under [MIT](LICENSE).
