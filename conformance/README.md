# Conformance tester

These tools check an AstroCollab 0.2 server, or a client, against the
[protocol](../spec/protocol.md) and the [contract](../openapi/astrocollab.yaml).
Every body is checked twice: against the OpenAPI document and against the
standalone [JSON Schemas](../schemas/index.json). They need Python 3.12 or later
and the packages in `requirements-dev.txt`.

## Test a server

Use a test server, not a live one: the suite enrols telescopes, joins projects
and reports frames.

Before you run it, make two open projects with the server's own tools:

- a **mosaic** that wants a red narrowband filter (H or S) and a dark-night
  filter (O, L, R, G or B), sets `maxHfr`, and covers a region several times the
  width of a 530 mm field (about 2.5° by 1.7°);
- optionally, a **single** target.

Then give the suite a way to get two telescope tokens: a signed-in person's
token, which it uses to enrol them, or two unused pairing codes.

```sh
python -m conformance.server_suite --server https://collab.example \
  --person-token=PERSON_TOKEN \
  --project-id MOSAIC_ID --single-project-id SINGLE_ID
```

```sh
python -m conformance.server_suite --server https://collab.example \
  --pairing-code=CODE_1 --pairing-code=CODE_2 --project-id MOSAIC_ID
```

Add one more `--pairing-code` with a person token to check pairing as well.
Write tokens and codes after `=`, as above: they can start with `-`.

The suite reads each project's requirements and builds telescopes to fit them.
It names its filters as people do ("Ha", "OIII", "Lum") to check that the
server folds them to letters. It prints one line per check:

```text
PASS  bright_moon_deals_red_narrowband [Moon]: dealt H
FAIL  a_mosaic_night_is_one_filter [Dealing]: a mosaic night deals ['H', 'O'] filters, not one
SKIP  pairing_code_works_once [Tokens]: the server does not list pairing
```

The bracket names the row in [the conformance scenarios](../spec/conformance.md).
A check fails if the server answers with the wrong status, or if any response
breaks the contract. The suite exits with status 1 if any check fails.

## What the server suite checks

| Row | Checks |
| --- | --- |
| Discovery | Health says ok and protocol 1. Sign-in status answers; starting sign-in gives a code that polls as pending, or 503 where sign-in is off. A server without `features` offers sign-in when its status says so. |
| Tokens | Enrolling or pairing gives each telescope its own token. Where `features` lists pairing, a used code gets 401. A made-up or missing token gets 401. A person's token cannot fetch work, and a telescope's token cannot enrol or list telescopes. |
| Browsing | Open projects are listed with kind and compatibility. |
| Hello | A bare rig cannot help; the same rig described properly can. A newer protocol gets 409. |
| Joining | A share arrives accepted, tiled with the rig's own field, at the rig's own sub lengths. Joining twice returns the same share. A rig with no optics gets 400; one with none of the wanted filters gets 409 with a reason. A rig with only some of them joins, and is dealt only those, even on a night that would favour another. A single target is one frame, centred on it. A fixed camera's cells follow its angle. |
| Tonight | Every share comes back. The list holds for the night, even after another rig reports. The next night moves the rig to panels it has not shot. Accepting a share keeps it accepted. |
| Dealing | A mosaic night is one filter. Each visit has at least `minFramesPerVisit` frames. Two rigs on one mosaic get different panels. |
| Moon | A bright Moon deals H or S; a dark night deals O, L, R, G or B. |
| Reports | Verdicts come back in the order sent. A rig cannot report on another rig's share (403). |
| Judging | A record past `maxHfr` is rejected with a reason. A missing star size is listed as unverified. |
| Duplicates | Reporting the same night, filter and panel again is a duplicate, and the accepted hours rise by the larger figure only. |
| Presence | A telescope that shared where it points shows as online, with what it shared. |
| Errors | Unknown projects and shares get 404 with a sentence. A body that breaks its type gets 422 listing the fields. |

## Test a client

Put the proxy between the client and a working server, such as the
[reference server](../reference/README.md):

```sh
python -m conformance.proxy --listen 127.0.0.1:8081 --upstream http://127.0.0.1:8800 --report report.json
```

Point the client at `http://127.0.0.1:8081` and run its normal night. Press
Ctrl-C to stop the proxy. It prints what the client did wrong (unknown routes,
missing tokens, bodies or queries that break the contract, tokens in URLs)
apart from what the server did wrong, and exits with status 1 if the client
did anything wrong.

## In this repository's tests

`tests/test_conformance.py` runs the suite against two servers:

- our [reference server](../reference/README.md), once with a person token and a
  pairing code, and once with pairing alone, plus the example client through the
  proxy;
- Starfront's server, from `STARFRONT_DIR` or a checkout beside this repository
  (`../starfront`), started under `STARFRONT_PYTHON`, Starfront's own `.venv`, or
  this Python. It needs FastAPI and uvicorn; without them the test is skipped.

Each server's test projects are made with that server's own tools, which the
protocol leaves out: the reference server's Python tools, and Starfront's
coordinator route with its admin token. The suite itself uses only the
contract's routes.

Where Starfront's server and this draft differ, the check is listed in
`STARFRONT_GAPS` in the test, so the test follows both as they change. It says
when a listed check starts passing, so the list stays current.

## What it cannot check

- **Coordinator tools.** Starting projects, pushing shares and overruling verdicts
  are outside the protocol.
- **The pull order in full.** It checks that two rigs avoid each other and that a
  rig moves on after a night, not the "thinnest field" order or the depth map.
- **Night length.** It does not check how many visits fit a night.
- **Sign-in in a browser.** It starts sign-in and polls once; it cannot finish it.
- **Re-dealing within a night.** A server may re-deal a held list once when Moon
  data first arrives or the rig's hours change by more than 15%. The suite sends
  the Moon on every call, so it does not test that exception.
- **Revoking telescopes and hashed tokens.** The protocol only recommends them.
- **Files.** The optional `files` extension has no routes yet.
