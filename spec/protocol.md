# AstroCollab protocol

Version: 0.2.0-draft.1. Draft specification.

MUST and MUST NOT mark requirements. SHOULD marks a recommendation; MAY marks
an option. The [OpenAPI contract](../openapi/astrocollab.yaml) and the
[JSON Schemas](../schemas/index.json) define payloads. This document defines the
behaviour they cannot.

This version adopts the protocol that [Starfront](https://github.com/bray-sfro/starfront)
already speaks, with its paths unchanged. Starfront's capture app and
collaboration server are its first implementation.

## 1. What the API covers

A project is one image that many astrophotographers build together: a region of
sky, the depth wanted in each filter, and the rules data must meet. The API is
what a telescope's software calls to take part:

1. Get a telescope token, by signing a person in and enrolling the telescope, or
   by pairing.
2. Say hello: describe the rig.
3. Browse open projects and join one.
4. Ask what to shoot tonight.
5. Report what was shot, and hear whether it counts.

Running projects belongs to each server and its own tools: starting and editing
projects, pushing work to a telescope by hand, overruling verdicts. This protocol
says how a server behaves toward telescopes, not how coordinators run it.

The telescope always asks; the server never reaches into an observatory. So a
rig behind a home router works, and a server going down means "no new work",
not "the night stops". The server never moves a mount, and never reserves sky.

## 2. Conventions

**Paths.** Routes live under `/api/v1` on the server's address.
`GET /api/v1/health` is public and gives the protocol version, an integer (this
draft is protocol 1), and, optionally, `features`: the optional parts the server
offers, such as `signin`, `pairing` or `files`.

**Bodies.** Requests and responses are JSON. Objects are open: a receiver keeps
or ignores fields it does not know, so a newer program is never refused for
sending one field more. Senders send numbers as numbers and unknown values as
null. Receivers SHOULD read a blank or unreadable number as unknown rather than
refuse the request, as Starfront does.

**Identifiers** are 12 lowercase hexadecimal characters, made by the server.
**Times** are seconds since 1970 UTC, as numbers. A **night** is named by the
rig, such as `2026-10-05`.

**Units.**

| Quantity | Unit |
| --- | --- |
| Region and footprint centres | Degrees of RA and declination. RA is degrees, not hours. |
| Region width and height | Degrees of sky, not degrees of the RA coordinate. |
| Presence RA | Hours, as the rig's own chart shows it. |
| Star size (HFR) and guiding error | Arcseconds, never pixels. |
| Image scale | Arcseconds per pixel. |
| Focal length, pixel size | Millimetres, microns (unbinned). |
| Bandpass | Nanometres, full width. |
| Sub length, integration | Seconds. Depth goals are hours. |
| Moon illumination | 0 to 1. |

**Filter names.** Every filter name is folded to one letter wherever it enters:

| Letter | Filter | Also written as |
| --- | --- | --- |
| `L` | Luminance | Lum, Luminance, Clear, UV/IR cut, None |
| `R`, `G`, `B` | Red, green, blue | Red, Green, Blue |
| `H` | Hydrogen-alpha | Ha, H-alpha, Halpha, Ha 3nm |
| `O` | Oxygen III | OIII, O3, Oxygen |
| `S` | Sulphur II | SII, S2, Sulphur |

Case, spaces, hyphens and a trailing bandpass ("Ha 3nm") do not matter. A name
that is none of these, such as a dual-band filter, is kept as written. Servers
MUST compare filters by these letters.

**Errors** are `{"detail": ...}`. On most errors `detail` is a sentence to show
the operator. On `422`, `detail` lists each bad field. Clients act on the HTTP
status:

| Status | Meaning | What to do |
| --- | --- | --- |
| `400` | The request cannot be served as asked, such as joining before describing the rig. | Do what `detail` says first. |
| `401` | Missing, unknown or revoked token. | Stop and ask the person to sign in or pair again. |
| `403` | Not this caller's to do. | Show `detail`; do not retry. |
| `404` | No such thing, or not this caller's. | Check the ID. |
| `409` | Conflicts with how things stand, such as a rig that cannot meet a project, or a newer protocol. | Show `detail`. |
| `422` | The body does not match its type. | Fix the program. |
| `503` | Not available here, such as sign-in on a server without it. | Use the other way, or try later. |

## 3. Tokens

There are two kinds of token, and neither can do the other's job:

- A **person token** belongs to somebody signed in. It enrols and lists that
  person's telescopes. It cannot fetch work or report frames.
- A **telescope token** (agent token) belongs to one telescope. It says hello,
  fetches work and reports frames, and nothing else. It never changes a project.

Both travel as `Authorization: Bearer <token>`.

A telescope gets its token one of two ways. A server MUST offer at least one, and
SHOULD list which in `features`. A server that sends no `features` offers sign-in
when `GET /api/v1/auth` says so; programs treat pairing as offered only when
`features` lists it.

**Sign in, then enrol** (`signin`). The device flow, because capture software is
a desktop program:

1. The program calls `POST /api/v1/auth/login` and gets a `code` and a `url`.
2. It opens `url` in the browser. The person signs in there, with whatever the
   server uses; Starfront servers use Discord.
3. The program polls `GET /api/v1/auth/poll?code=...` until `state` is `done`,
   which hands over the person's token once.
4. With the person token, the program enrols each telescope with
   `POST /api/v1/agents` and gets that telescope's token, shown once.

`GET /api/v1/auth` says whether sign-in is on. `GET /api/v1/auth/me` and
`POST /api/v1/auth/logout` do what they say. A server MAY sign a person out
elsewhere when they sign in on a second machine.

**Pairing** (`pairing`). The person issues a single-use code on the server's web
pages and types it into the program, which calls `POST /api/v1/pair` with the
code and a name for the telescope. It gets the same reply as enrolling. A code
works once and expires within an hour; repeated bad codes MAY get `429`.
Programs MUST NOT retry pairing on their own; if the reply is lost, the person
issues a new code.

Programs keep tokens in the system credential store and send them only in the
`Authorization` header, over HTTPS, to the server's own origin. Servers SHOULD
store only hashes of tokens and SHOULD let people revoke a telescope.

## 4. Hello

A telescope says what it is with `POST /api/v1/agent/hello`, before asking for
work and whenever the rig changes. The hello is also its heartbeat: a telescope
counts as online for 25 minutes after its last call.

The rig profile gives:

- **Optics and sensor:** focal length, pixel size, unbinned sensor size,
  binning. From these the server works out image scale and field of view.
- **Filters:** each name with its bandpass, or null where it is not known.
- **Colour:** a one-shot colour camera is broadband RGB, whatever is in front of
  it.
- **Rotation:** the camera's fixed position angle, or null when a rotator can
  set any angle. A fixed camera's tiling follows its real angle.
- **What it achieves:** typical star size and guiding error, in arcseconds.
- **Sub length per filter:** what its darks are built for. Work is dealt at these
  lengths, so every frame can be calibrated.
- **Time:** hours per night it gives, and its local window, such as 21:00 to
  03:00.

A hello MAY carry **presence**: where the telescope points and what it is doing.
`GET /api/v1/presence` shows every telescope seen in the last day, with what it
chose to share, so the group can see who is on the sky.

A hello whose `protocol` is newer than the server's gets `409`.

## 5. Projects

A project is a **region** of sky, of one of two kinds:

- **`mosaic`:** an area to cover. Each telescope tiles the whole region with its
  own camera, so telescopes with different fields need not agree on a grid. Each
  cell MUST fit the rig's field at the angle it shoots, and the cells SHOULD
  cover the region; the exact layout is the server's choice.
- **`single`:** one object. Every telescope frames it whole, at whatever field it
  has; nobody tiles it.

Its **goals** are depth wanted at every point of the region, per filter, in
hours. Depth is integration time at a point on the sky, which means the same on
a 300 mm refractor and a 2000 mm reflector. Its **requirements** are the rules
data must meet: focal length or scale, colour cameras allowed or not, star size
and guiding, sub length, filters with the widest bandpass accepted, Moon
illumination and separation, lowest altitude, calibration, and the fewest frames
a visit to one panel is worth.

`GET /api/v1/agent/projects` lists every open project with whether this
telescope can help, checked against the profile it last sent, rule by rule.
Programs SHOULD show the operator why a rig cannot help before a night is spent
on it. Each listing also shows the accepted hours so far and who is taking part.

`POST /api/v1/agent/projects/{id}/join` takes a share. A rig that has not said
hello with its focal length, sensor size and pixel size gets `400`: the server
cannot cut cells without them. Otherwise the server checks the requirements
against the rig's profile itself and against its sub lengths, and answers `409`
with the reason if they are not met. A join MAY carry the night, `moon` and
`moonUp`, as asking for tonight does, so the share's first list is tonight's and
made with the Moon in mind. A rig need carry only some of the filters
a project wants, each within its bandpass limit; its share and every night's list
use only those. A rig with none of them cannot help. The share is the whole region tiled with this
rig's camera, at its own sub lengths. Joining is consent: the share arrives
`accepted`. Joining twice returns the share already held.

## 6. Tonight

`GET /api/v1/agent/task` returns every share the telescope holds, each dealt for
tonight:

- `cells`: the rig's tiling of the region.
- `share`: which cells to shoot tonight, in order.
- `visit`: tonight's filter and frames per panel.
- `version`: rises whenever the share changes. A version the program already has
  means nothing new.

The program says which night it is in with `night`, and describes its own sky
with `moon` (how much of the Moon is lit) and `moonUp` (the fraction of its dark
hours the Moon is up). The server cannot know where a rig is, so the rig tells it.

Rules for dealing a night:

- A list MUST hold for the whole night it was dealt for. Other rigs' frames
  arriving at 2 a.m. must not move panels under a rig that is shooting them. The
  list is dealt afresh the first time the rig asks in the next night. A rig that
  never names its night gets a list held for 20 hours. A server MAY deal a list
  again within the night once, when the rig first reports its Moon, or when the
  hours it gives that night change by more than 15%.
- A visit to a panel MUST NOT be shorter than the project's `minFramesPerVisit`
  in any filter, because a rig stacks its own frames first.
- A rig is dealt only filters it carries.
- On a mosaic, a rig SHOULD shoot one filter a night: every panel gets a stack in
  that filter, the wheel never turns between panels, and one set of flats
  serves the night.
- Which filter: under a bright Moon, the red narrowband lines (H, S), which shoot
  through moonlight; on a dark night, what cannot be shot any other time (L, R,
  G, B, O). Among those, the filter with the most depth still wanted once what
  other rigs are putting in tonight is counted.
- Which panels, in this order: not where somebody else is tonight; where this rig
  has been least, so no patch is one camera's alone; where the field is thinnest.
  Panels already at full depth are skipped. The night holds as many visits as
  fit.

The reply also carries each project's requirements, so the program can judge its
own data before reporting it.

A share a coordinator pushed by hand arrives `offered` and stays so until the
program accepts it with `POST /api/v1/agent/task/{id}`. That route also declines
or finishes a share. Shares from joining need no answer.

Tonight's list is advice. The program and its operator decide when and whether
to shoot it, and the rig's own safety limits always win.

## 7. Reports and credit

After shooting, the program reports with `POST /api/v1/agent/report`: one record
per night, filter and panel. Each record gives frames, seconds and sub length,
the panel's **solved** footprint (where the telescope really pointed), image
scale, focal length, mean star size and guiding error over the night, Moon
illumination and separation, whether the frames are calibrated, the filter's
bandpass, and whether the camera is colour.

The server judges each record against the project's rules and answers with a
verdict per record:

- **Filter:** one the project wants, and no wider than its limit.
- **Colour camera:** allowed, and under the colour Moon limit.
- **Focal length and scale** within limits.
- **Star size and guiding** within limits, in arcseconds.
- **Sub length** within limits.
- **Moon** illumination and separation within limits.
- **Calibration**, when required.

A star-size, guiding or image-scale rule that cannot be checked because the
measurement is missing is listed under `unverified`: a missing measurement is
not a pass. Other rules are judged only on what the record gives. Verdicts are advisory: a
project's coordinator can overrule one, with the server's own tools, because a
night the numbers reject may be the only data anybody has on that patch of sky.

Accepted records build the project's depth map: each record adds its seconds,
per filter, to the sky its footprint covers. That map drives the next night's
dealing for every rig.

A record for the same telescope, share, night, filter and panel replaces the
earlier one at the larger figure, so sending a report again never counts hours
twice. Programs SHOULD send every unreported panel on each poll and mark a panel
reported only when the server has recorded it.

## 8. Files (optional)

Core credit is the night report: the server judges the numbers a rig measured
and keeps no images. A project that needs pixels can require files through an
optional extension, offered by servers that list `files`. It will carry over the
upload, stacked-master and shared-folder design from draft 0.1. It is not part of
this draft's routes yet.

## 9. Retries

Every call is safe to repeat:

| Call | Why a repeat is safe |
| --- | --- |
| Hello | It replaces the rig's profile and presence. |
| Tonight | It returns the list already dealt for that night. |
| Join | Joining twice returns the share already held. |
| Report | The same night, filter and panel keep the larger figure. |
| Accept, decline, finish | Setting the same state again leaves the share in that state. |
| Sign-in poll | It hands the token over once, then says `claimed`. |
| Pair | Never repeated automatically; see section 3. |

## 10. Out of scope

This draft leaves out equipment control, coordinator tools, the depth map as a
picture, federation between servers, payments and image upload (see section 8).
Servers publish their sign-in rules, terms and retention policies themselves.
