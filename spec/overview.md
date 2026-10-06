# How a night works

This is a draft specification for capture software, such as Starfront or
N.I.N.A., to build in. Starfront already speaks it. This page follows one
telescope from getting its token to having its subs counted, and is written for
people adding the API to their software. Every snippet is a real payload,
captured from Starfront's server; full files are in [`examples/`](../examples).

A project is one deep image that many astrophotographers shoot together, such as
a 10-hour-per-filter narrowband mosaic of M31's halo. It names a region of sky,
the depth wanted in each filter, and the rules data must meet. Each telescope
takes a share, tiles the region with its own camera, and every night asks which
panels to shoot. It reports what it shot; the server checks the numbers and
counts the good nights toward the project.

The server never moves your mount and never reserves sky.

## The flow

| Step | Request | Token |
| --- | --- | --- |
| 1. Get a telescope token | `POST /api/v1/auth/login`, then `POST /api/v1/agents`; or `POST /api/v1/pair` | None, then a person's |
| 2. Describe the rig | `POST /api/v1/agent/hello` | The telescope's |
| 3. Find a project | `GET /api/v1/agent/projects` | The telescope's |
| 4. Join it | `POST /api/v1/agent/projects/{id}/join` | The telescope's |
| 5. Ask what to shoot tonight | `GET /api/v1/agent/task?night=…&moon=…&moonUp=…` | The telescope's |
| 6. Shoot | Your own software | — |
| 7. Report what you shot | `POST /api/v1/agent/report` | The telescope's |

Steps 1 to 4 happen once. Steps 5 to 7 repeat each night; programs typically ask
every ten minutes, which also keeps the telescope shown as online. The examples
use two shell variables:

```sh
SERVER=https://collab.example   # the server's address
TOKEN=...                       # the telescope's token, from step 1
```

## 1. Get a telescope token

There are two ways; a server's `GET /api/v1/health` lists which it offers in
`features`.

**Sign in, then enrol.** The program asks for a login code and opens the page it
gives in the browser:

```sh
curl -X POST $SERVER/api/v1/auth/login
```

```json
{ "code": "EXAMPLE_ONLY_TOKEN_01_xxxxxxxx", "url": "https://collab.example/auth/discord/start?code=EXAMPLE_ONLY_TOKEN_01_xxxxxxxx", "expiresIn": 600 }
```

The person signs in there. The program polls `GET /api/v1/auth/poll?code=…`
until `state` is `done`, which hands over the person's token once. With that, it
enrols the telescope:

```sh
curl -X POST $SERVER/api/v1/agents \
  -H "Authorization: Bearer $PERSON" -H "Content-Type: application/json" \
  -d '{"name": "Vega 530"}'
```

The reply holds the telescope's own `token`, shown once. Store it in the system
credential store.

**Pairing.** On servers that offer it, the person issues a code on the server's
web pages, and the program trades it for the same reply:
`POST /api/v1/pair` with `{"code": "…", "name": "Vega 530"}`.

The two tokens never stand in for each other: a person token cannot fetch work,
and a telescope token cannot enrol. See [Authentication](authentication.md).

## 2. Describe the rig

Say hello with what the program already knows: optics, sensor, filters with
their bandpasses, the sub length each filter is shot at, and what the rig
usually achieves.

```sh
curl -X POST $SERVER/api/v1/agent/hello \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d @examples/hello.request.json
```

```json
{
  "protocol": 1,
  "profile": {
    "name": "Vega 530",
    "focalLength": 530.0, "pixelSize": 3.76, "sensorWidth": 6248, "sensorHeight": 4176,
    "filters": { "Ha": 7.0, "OIII": 7.0, "SII": 7.0, "L": null },
    "colour": false, "rotation": null,
    "typicalHfr": 2.4, "typicalGuideRms": 0.62,
    "exposures": { "Ha": 300.0, "OIII": 300.0, "SII": 300.0, "L": 120.0 },
    "hoursPerNight": 6.0, "windowFrom": "21:30", "windowTo": "04:30"
  },
  "presence": { "ra": 0.7123, "dec": 41.27, "state": "imaging", "target": "M31 halo in narrowband" }
}
```

Star size and guiding are in arcseconds, never pixels. The sub lengths are the
ones your darks are built for: work is dealt at those lengths. `presence` is
optional and shows the group where you point; `GET /api/v1/presence` shows
everybody.

## 3. Find a project

```sh
curl -H "Authorization: Bearer $TOKEN" $SERVER/api/v1/agent/projects
```

Each open project comes with its region, goals in hours per filter, rules, the
hours collected so far, and whether your rig can help, rule by rule:

```json
{ "name": "M51 in LRGB", "kind": "single", "goals": { "L": 20.0, "R": 5.0, "G": 5.0, "B": 5.0 },
  "compatibility": { "ok": false,
    "summary": "cannot contribute: 530 mm is longer than the 400 mm the project wants; can shoot L — but no R; no G; no B" } }
```

Show this to the operator before they spend a night on a project they cannot
help.

## 4. Join it

```sh
curl -X POST $SERVER/api/v1/agent/projects/$PROJECT/join \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"exposures": {"Ha": 300, "OIII": 300, "SII": 300, "L": 120}}'
```

The server checks the rules against your rig and hands back your share: the whole
region tiled with your camera's field, at your own sub lengths. Joining is your
consent, so the share arrives `accepted`. Joining again returns the same share.

## 5. Ask what to shoot tonight

Name the night, and say how bright tonight's Moon is at your site and how much of
the dark hours it is up. The server cannot know where you are.

```sh
curl -H "Authorization: Bearer $TOKEN" \
  "$SERVER/api/v1/agent/task?night=2026-10-05&moon=0.12&moonUp=0.3"
```

```json
{
  "id": "000000000004", "projectName": "M31 halo in narrowband",
  "state": "accepted", "version": 3, "kind": "mosaic",
  "filters": [ { "filter": "H", "exposure": 300.0, "hours": 10.0 },
               { "filter": "O", "exposure": 300.0, "hours": 10.0 } ],
  "share": [0, 1, 2, 3, 4, 5],
  "visit": { "seconds": 3300.0, "frames": { "O": 11 }, "filter": "O", "moon": 0.036 },
  "assignedNight": "2026-10-05"
}
```

That is one share from the reply, which lists all of yours. Tonight this rig
shoots six of its nine cells (`share` indexes into `cells`), all in OIII, 11 subs
of 300 s on each. The Moon is thin, so it is an OIII night; under a bright Moon
the server would send H-alpha or SII, which shoot through moonlight.

The list holds for the whole night: ask again and you get the same panels and
the same `version`, however many frames other rigs send meanwhile. The first time
you ask in the next night, it is dealt again, sending you where the stack is
thinnest and where you have been least. A `version` you already have means
nothing new.

Filter names come back as one letter: `L`, `R`, `G`, `B`, `H`, `O`, `S`. Your
"OIII" and the server's `O` are the same filter.

## 6. Shoot

Run the panels in order with your own sequencer. The list is advice: your
software decides when and whether to slew, and your safety limits always win.
Keep the share and panel number with each frame.

## 7. Report what you shot

Send one record per night, filter and panel, with the solved footprint and what
you measured:

```sh
curl -X POST $SERVER/api/v1/agent/report \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d @examples/report.request.json
```

```json
{ "project": "000000000002", "task": "000000000004", "night": "2026-10-05",
  "panel": "0", "filterName": "OIII", "frames": 11, "seconds": 3300.0, "exposure": 300.0,
  "footprint": { "ra": 7.649, "dec": 39.769, "width": 2.540, "height": 1.697, "rotation": 35.0 },
  "scale": 1.463, "focalLength": 530.0, "hfr": 2.34, "guideRms": 0.58, "bandpass": 7.0 }
```

Each record gets a verdict, in order. A good night:

```json
{ "id": "000000000005", "accepted": true, "duplicate": false,
  "verdict": { "accepted": true, "reasons": [], "unverified": [], "summary": "accepted" } }
```

A night with soft stars:

```json
{ "accepted": false, "reasons": [ "stars averaged 4.98\", project wants 3.5\" or better" ] }
```

Accepted nights add depth where their footprints lie, which steers everyone's
next night. A measurement you did not send cannot pass a rule; it shows under
`unverified`. Verdicts are advisory: a project's coordinator can overrule one.

Send every panel you have not yet reported on each poll, and mark a panel
reported only when the server has recorded it. Sending the same panel again is
safe: the larger figure stands, and the reply says `duplicate`.

## Rules for every request

| Rule | What to do |
| --- | --- |
| Address | Paths start with `/api/v1` on the server's address. |
| Tokens | `Authorization: Bearer <token>`. Never put a token in a URL or log. |
| Bodies | JSON. Send numbers as numbers and unknown values as null. Keep or ignore fields you do not know. |
| Retries | Every call is safe to repeat as is; see the [protocol](protocol.md#9-retries). |
| Errors | `{"detail": ...}`. Act on the HTTP status; show `detail` to the operator. |

| Status | Meaning | What to do |
| --- | --- | --- |
| `400` | Asked too early, such as joining before saying hello with the rig's optics. | Do what `detail` says first. |
| `401` | Missing, unknown or revoked token. | Ask the person to sign in or pair again. |
| `403` | Not yours to do. | Show the error; do not retry. |
| `404` | No such thing, or not yours. | Check the ID. |
| `409` | Conflicts with how things stand, such as a rig that cannot meet a project. | Show `detail`. |
| `422` | The body does not match its type; `detail` lists the bad fields. | Fix the program. |
| `503` | Not available here, such as sign-in on a server without it. | Use the other way in, or try later. |

## Next

- [REST reference](api.md): every route, its body, replies and examples.
- [Authentication](authentication.md): signing in, enrolling and pairing.
- [Protocol](protocol.md): the rules servers and programs follow.
- [Reference server](../reference/README.md) and
  [conformance tester](../conformance/README.md): run and test the API locally.
