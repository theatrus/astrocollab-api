# Reference server and client

A small AstroCollab 0.2 server and an example telescope program, for testing
software against the protocol and for reading. Starfront's collaboration server
is the first implementation of 0.2; this is a second one, written from the
[protocol](../spec/protocol.md) alone, so the two can check each other.

Do not run it for real projects. It keeps everything in memory, serves plain
HTTP and has no accounts beyond what its tools make.

## Run it

Use Python 3.12 or later with `requirements-dev.txt` installed. From the
repository root:

```sh
python -m reference.server --port 8080
```

It prints a person token, a pairing code and the sample projects:

```text
AstroCollab reference server on http://127.0.0.1:8080
  person token   _cwX0HL1l1lLSQDKjaaIB-Y3zJ6mtwE96zKzC-2NpkE
  pairing code   9HyJnIk4T5DLbh15
  project        11ba95f0c9c5  M31 halo in narrowband
  project        a0c42f301767  M51 in LRGB
```

In another terminal, run one night with the example program:

```sh
python -m reference.client --server http://127.0.0.1:8080 --pairing-code 9HyJnIk4T5DLbh15
```

or enrol the telescope with the person token instead:

```sh
python -m reference.client --server http://127.0.0.1:8080 --person-token <token>
```

The program pairs, says hello as a 530 mm refractor with narrowband filters,
browses, joins the first project it can help, asks for tonight's list, reports
two panels and reads who is on the sky. It prints each request and its status.

To sign in the way a desktop program does, call `POST /api/v1/auth/login`,
open the `url` it returns, press **Approve**, and poll `/api/v1/auth/poll`.

## Sample projects

- **M31 halo in narrowband:** a 5° × 3° mosaic wanting 10 hours each of H and O
  at every point, 7 nm filters or narrower, stars of 3.5″ or better, subs of
  120–600 s.
- **M51 in LRGB:** one object wanting 20 hours of L and 5 each of R, G and B,
  from focal lengths of 800 mm and up.

## From Python

```python
import threading
from reference.server import serve

httpd = serve(port=0)                      # sample=False starts empty
threading.Thread(target=httpd.serve_forever, daemon=True).start()
base = httpd.base_url                      # such as http://127.0.0.1:53211
code = httpd.tools.issue_pairing_code("Observer")
```

`httpd.tools` does what a real server's web pages and coordinators do:

| Tool | Does |
| --- | --- |
| `sign_in(name)` | Returns a person token for `name`. |
| `approve_login(code, name)` | Finishes a device sign-in, as the sign-in page does. |
| `issue_pairing_code(name)` | Returns a single-use pairing code for `name`, good for an hour. |
| `create_project(name, region, kind, goals, requirements, notes="")` | Starts a project and returns its ID. |

`httpd.sample_project_ids` maps each sample project's name to its ID.
`httpd.state.now` can be replaced to move the clock in tests.

## How it decides

The rules are in `rules.py`, each a plain function:

- **Filter names** fold to one letter (L R G B H O S), as the protocol says.
- **Tiling.** A rig tiles a mosaic with its own frame, at the angle it shoots:
  its fixed angle, or the project's when a rotator can turn it. The grid is laid
  along the camera's axes, large enough to cover the north-up region, with 10%
  overlap. A single target is one frame centred on the object.
- **Depth** is integration time at a point. A cell's depth is the mean, over 25
  points spread across it, of the seconds of every accepted footprint covering
  each point.
- **A night's list** holds for the night the rig names, or 20 hours if it names
  none. A mosaic night is one filter: under a bright Moon (lit fraction times
  the fraction of the night it is up, 0.2 or more) H or S, otherwise the rest;
  then the filter with the most depth still wanted after what other rigs hold
  tonight. Panels are taken least claimed by other rigs tonight first, then
  least shot by this rig in any filter, then thinnest. The night holds as many
  visits as fit, with 90 s per visit for the slew, each at least
  `minFramesPerVisit` frames and no longer than a panel's full depth. A rig that
  has not said how long its night is gets 6 hours.
- **A single target's night** spreads over all its filters, by the depth each
  still wants.
- **Joining** needs the rig's focal length, pixel size and sensor size (`400`
  without them) and at least one filter the project wants, within its bandpass
  limit (`409` with the reason otherwise). The share and every night's list use
  only the wanted filters the rig carries.
- **Judging** checks every rule the project sets against what the record gives.
  A star-size, guiding or image-scale rule whose measurement is missing goes
  under `unverified`; other rules with nothing to check against are skipped.
- **Reports** for the same telescope, share, night, filter and panel keep the
  larger figure.
- **Pairing** answers `429` after five bad codes from one address in a minute.
- **A night's list** never moves once dealt; the protocol lets a server re-deal
  once when Moon data first arrives, and this one does not.

Blank or unreadable numbers in a request are read as unknown, as the protocol
recommends. Everything else is checked against `schemas/`, and a body that does
not match gets `422` with a list of the bad fields.

## Tests

```sh
python -m unittest discover -s tests -p 'test_reference.py'
```

The tests check every reply against the schema the contract gives for that
route and status, and cover tokens, sign-in, pairing, hello, joining, dealing,
judging, duplicate reports and the example program.
