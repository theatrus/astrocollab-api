# REST reference

<!-- Generated from openapi/astrocollab.yaml by tools/build_openapi.py. Do not edit. -->

Version 0.2.0-draft.1. Paths are relative to the server's address, for example `https://collab.example`.

Send `Authorization: Bearer <token>` where a token is needed: a telescope's agent
token on telescope routes, a person's token on account routes. Bodies are JSON.
Errors are `{"detail": ...}`: words for the operator on most errors, a list of
bad fields on `422`. Objects are open: keep or ignore fields you do not know. Every
type links to a standalone [JSON Schema](../schemas/index.json), so you can
validate payloads without OpenAPI tools. The [protocol](protocol.md) gives the
rules behind each route.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | [`/api/v1/health`](#health) | Check the server and its protocol |
| `GET` | [`/api/v1/auth`](#authstatus) | Can people sign in here |
| `POST` | [`/api/v1/auth/login`](#authlogin) | Start signing a person in |
| `GET` | [`/api/v1/auth/poll`](#authpoll) | Wait for the person to finish signing in |
| `GET` | [`/api/v1/auth/me`](#authme) | Who is signed in |
| `POST` | [`/api/v1/auth/logout`](#authlogout) | Sign out |
| `POST` | [`/api/v1/agents`](#enroltelescope) | Enrol a telescope |
| `GET` | [`/api/v1/agents`](#listtelescopes) | List your telescopes |
| `POST` | [`/api/v1/pair`](#pairtelescope) | Pair a telescope with a code |
| `POST` | [`/api/v1/agent/hello`](#hello) | Say hello and describe the rig |
| `GET` | [`/api/v1/agent/projects`](#openprojects) | Browse open projects |
| `POST` | [`/api/v1/agent/projects/{project_id}/join`](#joinproject) | Join a project |
| `GET` | [`/api/v1/agent/task`](#tonight) | Ask what to shoot tonight |
| `POST` | [`/api/v1/agent/task/{task_id}`](#settaskstate) | Accept, decline or finish a share |
| `POST` | [`/api/v1/agent/report`](#report) | Report what was shot |
| `GET` | [`/api/v1/presence`](#presence) | See who is on the sky |

## Discovery

<a id="health"></a>

### Check the server and its protocol

`GET /api/v1/health`

Token: None.

| Status | Body | Meaning |
| --- | --- | --- |
| `200` | [`Health`](../schemas/Health.schema.json) (`application/json`) | Success. |

Response `200` ([file](../examples/health.response.json)):

```json
{
  "ok": true,
  "protocol": 1,
  "time": 1791171001.0,
  "version": "0.2.9",
  "adminConfigured": true,
  "discord": true,
  "roleRequired": false
}
```

## Signing in

<a id="authstatus"></a>

### Can people sign in here

`GET /api/v1/auth`

Token: None.

| Status | Body | Meaning |
| --- | --- | --- |
| `200` | [`AuthStatus`](../schemas/AuthStatus.schema.json) (`application/json`) | Success. |

Response `200` ([file](../examples/authStatus.response.json)):

```json
{
  "discord": true,
  "guild": "000000000000000001",
  "roleRequired": false,
  "publicUrl": "https://collab.example"
}
```

<a id="authlogin"></a>

### Start signing a person in

`POST /api/v1/auth/login`

Token: None.

Starts a device sign-in. The program opens `url` in the browser and polls `authPoll` with `code` until the person has signed in there.

| Status | Body | Meaning |
| --- | --- | --- |
| `200` | [`LoginStarted`](../schemas/LoginStarted.schema.json) (`application/json`) | Success. |
| `503` | [`ErrorBody`](../schemas/ErrorBody.schema.json) (`application/json`) | Not available on this server, such as sign-in when it is not set up. |

Response `200` ([file](../examples/authLogin.response.json)):

```json
{
  "code": "EXAMPLE_ONLY_TOKEN_01_xxxxxxxx",
  "url": "https://collab.example/auth/discord/start?code=EXAMPLE_ONLY_TOKEN_01_xxxxxxxx",
  "expiresIn": 600
}
```

<a id="authpoll"></a>

### Wait for the person to finish signing in

`GET /api/v1/auth/poll`

Token: None.

| Name | In | Type | Notes |
| --- | --- | --- | --- |
| `code` | query | `string`, required |  |

| Status | Body | Meaning |
| --- | --- | --- |
| `200` | [`LoginPoll`](../schemas/LoginPoll.schema.json) (`application/json`) | Success. |

Response `200` ([file](../examples/authPoll.done.response.json)):

```json
{
  "state": "done",
  "token": "EXAMPLE_ONLY_TOKEN_02_xxxxxxxx",
  "user": {
    "id": "000000000000000042",
    "name": "Vega Observatory",
    "avatar": "",
    "admin": false,
    "canStart": true
  }
}
```

<a id="authme"></a>

### Who is signed in

`GET /api/v1/auth/me`

Token: A person's token, from signing in.

| Status | Body | Meaning |
| --- | --- | --- |
| `200` | [`Me`](../schemas/Me.schema.json) (`application/json`) | Success. |
| `401` | [`ErrorBody`](../schemas/ErrorBody.schema.json) (`application/json`) | Missing, unknown or revoked token. |
| `422` | [`ValidationErrorBody`](../schemas/ValidationErrorBody.schema.json) (`application/json`) | The body does not match its type. |

Response `200` ([file](../examples/authMe.response.json)):

```json
{
  "id": "000000000000000042",
  "name": "Vega Observatory",
  "admin": false,
  "canStart": true
}
```

<a id="authlogout"></a>

### Sign out

`POST /api/v1/auth/logout`

Token: A person's token, from signing in.

| Status | Body | Meaning |
| --- | --- | --- |
| `200` | [`SignedOut`](../schemas/SignedOut.schema.json) (`application/json`) | Success. |
| `401` | [`ErrorBody`](../schemas/ErrorBody.schema.json) (`application/json`) | Missing, unknown or revoked token. |
| `422` | [`ValidationErrorBody`](../schemas/ValidationErrorBody.schema.json) (`application/json`) | The body does not match its type. |

Response `200` ([file](../examples/extra/authLogout.response.json)):

```json
{
  "signedOut": true
}
```

## Telescopes and their tokens

<a id="enroltelescope"></a>

### Enrol a telescope

`POST /api/v1/agents`

Token: A person's token, from signing in.

Takes a person's token. Each telescope gets its own token, shown once.

Request body (`application/json`): [`EnrolRequest`](../schemas/EnrolRequest.schema.json)

| Status | Body | Meaning |
| --- | --- | --- |
| `200` | [`TelescopeCreated`](../schemas/TelescopeCreated.schema.json) (`application/json`) | Success. |
| `401` | [`ErrorBody`](../schemas/ErrorBody.schema.json) (`application/json`) | Missing, unknown or revoked token. |
| `422` | [`ValidationErrorBody`](../schemas/ValidationErrorBody.schema.json) (`application/json`) | The body does not match its type. |
| `503` | [`ErrorBody`](../schemas/ErrorBody.schema.json) (`application/json`) | Not available on this server, such as sign-in when it is not set up. |

Request ([file](../examples/enrolTelescope.request.json)):

```json
{
  "name": "Vega 530"
}
```

Response `200` ([file](../examples/enrolTelescope.response.json)):

```json
{
  "agent": {
    "id": "000000000001",
    "name": "Vega 530",
    "owner": "Vega Observatory",
    "owner_id": "000000000000000042",
    "created": 1791171016.0,
    "seen": 0.0,
    "profile": {},
    "presence": {}
  },
  "token": "EXAMPLE_ONLY_TOKEN_03_xxxxxxxx"
}
```

<a id="listtelescopes"></a>

### List your telescopes

`GET /api/v1/agents`

Token: A person's token, from signing in.

Takes a person's token. Lists the person's own telescopes.

| Status | Body | Meaning |
| --- | --- | --- |
| `200` | [`TelescopeList`](../schemas/TelescopeList.schema.json) (`application/json`) | Success. |
| `401` | [`ErrorBody`](../schemas/ErrorBody.schema.json) (`application/json`) | Missing, unknown or revoked token. |
| `422` | [`ValidationErrorBody`](../schemas/ValidationErrorBody.schema.json) (`application/json`) | The body does not match its type. |
| `503` | [`ErrorBody`](../schemas/ErrorBody.schema.json) (`application/json`) | Not available on this server, such as sign-in when it is not set up. |

Example: [Response `200`](../examples/listTelescopes.response.json).

<a id="pairtelescope"></a>

### Pair a telescope with a code

`POST /api/v1/pair`

Token: None.

The other way to get a telescope token, for servers that list the `pairing` feature. The person issues a single-use code on the server's web pages and types it into the program. Takes no token. Never retry on your own: a lost reply needs a new code.

Request body (`application/json`): [`PairRequest`](../schemas/PairRequest.schema.json)

| Status | Body | Meaning |
| --- | --- | --- |
| `200` | [`TelescopeCreated`](../schemas/TelescopeCreated.schema.json) (`application/json`) | Success. |
| `401` | [`ErrorBody`](../schemas/ErrorBody.schema.json) (`application/json`) | Missing, unknown or revoked token. |
| `404` | [`ErrorBody`](../schemas/ErrorBody.schema.json) (`application/json`) | No such thing, or not this caller's. |
| `422` | [`ValidationErrorBody`](../schemas/ValidationErrorBody.schema.json) (`application/json`) | The body does not match its type. |
| `429` | [`ErrorBody`](../schemas/ErrorBody.schema.json) (`application/json`) | Too many tries, such as repeated bad pairing codes. |

Request ([file](../examples/extra/pairTelescope.request.json)):

```json
{
  "code": "EXAMPLE-ONLY-PAIRING-CODE",
  "name": "Vega 530"
}
```

Response `200` ([file](../examples/extra/pairTelescope.response.json)):

```json
{
  "agent": {
    "id": "000000000009",
    "name": "Vega 530",
    "owner": "Vega Observatory",
    "owner_id": "000000000000000042",
    "created": 1791171016.0,
    "seen": 0.0,
    "profile": {},
    "presence": {}
  },
  "token": "EXAMPLE_ONLY_TOKEN_03_xxxxxxxx"
}
```

## The telescope's night

<a id="hello"></a>

### Say hello and describe the rig

`POST /api/v1/agent/hello`

Token: The telescope's agent token.

Says what this telescope is and, if it likes, where it points. Also a heartbeat. Send it before asking for work and whenever the rig changes.

Request body (`application/json`): [`HelloRequest`](../schemas/HelloRequest.schema.json)

| Status | Body | Meaning |
| --- | --- | --- |
| `200` | [`HelloResponse`](../schemas/HelloResponse.schema.json) (`application/json`) | Success. |
| `401` | [`ErrorBody`](../schemas/ErrorBody.schema.json) (`application/json`) | Missing, unknown or revoked token. |
| `409` | [`ErrorBody`](../schemas/ErrorBody.schema.json) (`application/json`) | Conflicts with how things stand, such as a rig that cannot meet a project. |
| `422` | [`ValidationErrorBody`](../schemas/ValidationErrorBody.schema.json) (`application/json`) | The body does not match its type. |

Example: [Request](../examples/hello.request.json).

Response `200` ([file](../examples/hello.response.json)):

```json
{
  "agent": "000000000001",
  "name": "Vega 530",
  "protocol": 1,
  "serverTime": 1791171023.0
}
```

<a id="openprojects"></a>

### Browse open projects

`GET /api/v1/agent/projects`

Token: The telescope's agent token.

Every open project, and whether this telescope can help each one.

| Status | Body | Meaning |
| --- | --- | --- |
| `200` | [`OpenProjects`](../schemas/OpenProjects.schema.json) (`application/json`) | Success. |
| `401` | [`ErrorBody`](../schemas/ErrorBody.schema.json) (`application/json`) | Missing, unknown or revoked token. |
| `422` | [`ValidationErrorBody`](../schemas/ValidationErrorBody.schema.json) (`application/json`) | The body does not match its type. |

Example: [Response `200`](../examples/openProjects.response.json).

<a id="joinproject"></a>

### Join a project

`POST /api/v1/agent/projects/{project_id}/join`

Token: The telescope's agent token.

Takes a share of a project, checked against the profile the rig last sent. The share is the whole region tiled with this rig's own camera, at its own sub lengths. Joining twice returns the share already held. Joining is consent: the share arrives accepted. A rig that has not described its optics gets 400; one that cannot meet the rules gets 409.

| Name | In | Type | Notes |
| --- | --- | --- | --- |
| `project_id` | path | [`Id`](../schemas/Id.schema.json), required |  |

Request body (`application/json`): [`JoinRequest`](../schemas/JoinRequest.schema.json)

| Status | Body | Meaning |
| --- | --- | --- |
| `200` | [`JoinResponse`](../schemas/JoinResponse.schema.json) (`application/json`) | Success. |
| `400` | [`ErrorBody`](../schemas/ErrorBody.schema.json) (`application/json`) | The request cannot be served as asked yet, such as joining before describing the rig. |
| `401` | [`ErrorBody`](../schemas/ErrorBody.schema.json) (`application/json`) | Missing, unknown or revoked token. |
| `404` | [`ErrorBody`](../schemas/ErrorBody.schema.json) (`application/json`) | No such thing, or not this caller's. |
| `409` | [`ErrorBody`](../schemas/ErrorBody.schema.json) (`application/json`) | Conflicts with how things stand, such as a rig that cannot meet a project. |
| `422` | [`ValidationErrorBody`](../schemas/ValidationErrorBody.schema.json) (`application/json`) | The body does not match its type. |

Request ([file](../examples/joinProject.request.json)):

```json
{
  "hours": 0.0,
  "exposure": 0.0,
  "exposures": {
    "Ha": 300.0,
    "OIII": 300.0,
    "SII": 300.0,
    "L": 120.0
  }
}
```

Example: [Response `200`](../examples/joinProject.response.json).

Response `409` ([file](../examples/errors/cannot-join.response.json)):

```json
{
  "detail": "cannot contribute: 1000 mm is longer than the 400 mm the project wants; can shoot L — but no R; no G; no B"
}
```

<a id="tonight"></a>

### Ask what to shoot tonight

`GET /api/v1/agent/task`

Token: The telescope's agent token.

Tonight's work: each share with tonight's panels in `share` and tonight's filter and frames in `visit`. A list holds for the whole night the rig names and is dealt afresh the first time it asks in the next one. `moon` and `moonUp` describe the rig's own sky tonight and decide whether it is a narrowband night. A version the rig already has means nothing new.

| Name | In | Type | Notes |
| --- | --- | --- | --- |
| `night` | query | [`NightName`](../schemas/NightName.schema.json), optional | The night the rig is in, as it names its nights, such as `2026-10-05`. |
| `moon` | query | `number`, optional | How much of the Moon is lit tonight, 0–1.; minimum `0`, maximum `1` |
| `moonUp` | query | `number`, optional | The fraction of the dark hours the Moon is up, 0–1.; minimum `0`, maximum `1` |

| Status | Body | Meaning |
| --- | --- | --- |
| `200` | [`TaskResponse`](../schemas/TaskResponse.schema.json) (`application/json`) | Success. |
| `401` | [`ErrorBody`](../schemas/ErrorBody.schema.json) (`application/json`) | Missing, unknown or revoked token. |
| `422` | [`ValidationErrorBody`](../schemas/ValidationErrorBody.schema.json) (`application/json`) | The body does not match its type. |

Query ([file](../examples/tonight.query.json)):

```json
{
  "night": "2026-10-05",
  "moon": 0.12,
  "moonUp": 0.3
}
```

Example: [Response `200`](../examples/tonight.response.json).

Response `401` ([file](../examples/errors/unknown-token.response.json)):

```json
{
  "detail": "unknown agent token"
}
```

<a id="settaskstate"></a>

### Accept, decline or finish a share

`POST /api/v1/agent/task/{task_id}`

Token: The telescope's agent token.

Accept, decline or finish a share. Only needed for shares a coordinator offered.

| Name | In | Type | Notes |
| --- | --- | --- | --- |
| `task_id` | path | [`Id`](../schemas/Id.schema.json), required |  |

Request body (`application/json`): [`TaskStateRequest`](../schemas/TaskStateRequest.schema.json)

| Status | Body | Meaning |
| --- | --- | --- |
| `200` | [`TaskEnvelope`](../schemas/TaskEnvelope.schema.json) (`application/json`) | Success. |
| `401` | [`ErrorBody`](../schemas/ErrorBody.schema.json) (`application/json`) | Missing, unknown or revoked token. |
| `404` | [`ErrorBody`](../schemas/ErrorBody.schema.json) (`application/json`) | No such thing, or not this caller's. |
| `422` | [`ValidationErrorBody`](../schemas/ValidationErrorBody.schema.json) (`application/json`) | The body does not match its type. |

Request ([file](../examples/setTaskState.request.json)):

```json
{
  "state": "accepted"
}
```

Example: [Response `200`](../examples/setTaskState.response.json).

<a id="report"></a>

### Report what was shot

`POST /api/v1/agent/report`

Token: The telescope's agent token.

Hands back what was shot, one record per night, filter and panel, and hears whether each counts. Reporting the same night, filter and panel again keeps the larger figure.

Request body (`application/json`): [`ReportRequest`](../schemas/ReportRequest.schema.json)

| Status | Body | Meaning |
| --- | --- | --- |
| `200` | [`ReportResponse`](../schemas/ReportResponse.schema.json) (`application/json`) | Success. |
| `401` | [`ErrorBody`](../schemas/ErrorBody.schema.json) (`application/json`) | Missing, unknown or revoked token. |
| `403` | [`ErrorBody`](../schemas/ErrorBody.schema.json) (`application/json`) | The caller may not do this. |
| `422` | [`ValidationErrorBody`](../schemas/ValidationErrorBody.schema.json) (`application/json`) | The body does not match its type. |

Example: [Request](../examples/report.request.json).

Response `200` ([file](../examples/report.response.json)):

```json
{
  "recorded": [
    {
      "id": "000000000005",
      "accepted": true,
      "duplicate": false,
      "verdict": {
        "accepted": true,
        "reasons": [],
        "unverified": [],
        "summary": "accepted"
      }
    },
    {
      "id": "000000000006",
      "accepted": true,
      "duplicate": false,
      "verdict": {
        "accepted": true,
        "reasons": [],
        "unverified": [],
        "summary": "accepted"
      }
    }
  ]
}
```

<a id="presence"></a>

### See who is on the sky

`GET /api/v1/presence`

Token: The telescope's agent token.

Telescopes that checked in during the last day, where they point and what they do.

| Status | Body | Meaning |
| --- | --- | --- |
| `200` | [`PresenceResponse`](../schemas/PresenceResponse.schema.json) (`application/json`) | Success. |
| `401` | [`ErrorBody`](../schemas/ErrorBody.schema.json) (`application/json`) | Missing, unknown or revoked token. |
| `422` | [`ValidationErrorBody`](../schemas/ValidationErrorBody.schema.json) (`application/json`) | The body does not match its type. |

Response `200` ([file](../examples/presence.response.json)):

```json
{
  "telescopes": [
    {
      "id": "000000000001",
      "name": "Vega 530",
      "owner": "Vega Observatory",
      "ra": 0.7123,
      "dec": 41.27,
      "state": "imaging",
      "target": "M31 halo in narrowband",
      "project": "000000000002",
      "ageSeconds": 0,
      "online": true
    }
  ],
  "online": 1,
  "people": 1,
  "onlineSeconds": 1500,
  "serverTime": 1791171043.0
}
```
