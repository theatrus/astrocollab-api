# Authentication

Every private request carries one header:

```http
Authorization: Bearer <token>
```

There are two kinds of token, and neither can do the other's job:

| Token | Belongs to | Can |
| --- | --- | --- |
| Person token | Somebody signed in | Enrol and list their own telescopes. Nothing else. |
| Telescope token | One telescope | Say hello, browse and join projects, fetch tonight's work, report frames. Never change a project. |

A telescope token lives in a settings file on an observatory PC, so it must never
be able to rewrite a project. A person's sign-in must never be able to drive a
mount. Keeping them apart guarantees both.

A program gets a telescope token one of two ways. `GET /api/v1/health` lists
what the server offers in `features`: `signin`, `pairing` or both.

## Signing in, then enrolling

The device flow, because capture software is a desktop program with no web page
of its own.

1. **Start.** Ask for a login code:

   ```sh
   curl -X POST $SERVER/api/v1/auth/login
   ```

   ```json
   { "code": "EXAMPLE_ONLY_TOKEN_01_xxxxxxxx", "url": "https://collab.example/auth/discord/start?code=EXAMPLE_ONLY_TOKEN_01_xxxxxxxx", "expiresIn": 600 }
   ```

2. **Open `url` in the browser.** The person signs in there with whatever the
   server uses. Starfront servers use Discord, and check the person belongs to
   the community's Discord server. The program never sees a password.
3. **Poll** every few seconds until the person is done:

   ```sh
   curl "$SERVER/api/v1/auth/poll?code=EXAMPLE_ONLY_TOKEN_01_xxxxxxxx"
   ```

   `pending` means keep waiting. `done` hands over the person's token once,
   with their name and whether they may start projects
   ([example](../examples/authPoll.done.response.json)). `claimed` means the
   token was already handed over; `expired` means start again.
4. **Enrol each telescope** with the person token:

   ```sh
   curl -X POST $SERVER/api/v1/agents \
     -H "Authorization: Bearer $PERSON" -H "Content-Type: application/json" \
     -d '{"name": "Vega 530"}'
   ```

   The reply holds the telescope's `token`, shown once
   ([example](../examples/enrolTelescope.response.json)). One person can enrol
   several telescopes; each gets its own token.

`GET /api/v1/auth` says whether sign-in is on; `POST /api/v1/auth/login` gives
`503` where it is not. `GET /api/v1/auth/me` says who is signed in, and
`POST /api/v1/auth/logout` signs out. `GET /api/v1/agents` lists the person's
telescopes, without their tokens.

## Pairing

For servers that list `pairing`. Nothing to sign into from the program:

1. The person issues a pairing code on the server's web pages.
2. They type it into the program.
3. The program trades it for a telescope token, with no `Authorization` header:

   ```sh
   curl -X POST $SERVER/api/v1/pair \
     -H "Content-Type: application/json" \
     -d '{"code": "EXAMPLE-ONLY-PAIRING-CODE", "name": "Vega 530"}'
   ```

   The reply is the same as enrolling: the telescope and its token, shown once.

A code works once and expires within an hour. An unknown, used or expired code
gets `401`. Never retry pairing on your own: if the reply was lost, ask the
person for a new code.

## Keeping tokens safe

- Store tokens in the system credential store, not a plain settings file where
  you can avoid it.
- Send them only in the `Authorization` header, only over HTTPS, and only to the
  server's own address.
- Never write them to URLs, logs or reports.
- Enrol or pair each telescope separately, so one can be revoked alone.
- On `401`, stop and ask the person to sign in or pair again. Do not retry in a
  loop.

## For servers

- Offer at least one of sign-in and pairing, and list it in `features`.
- Keep person and telescope tokens apart: answer `401` to either kind on the
  other's routes.
- Store tokens as hashes where you can, and let people revoke a telescope.
- Pairing codes: single use, at most an hour, at least 128 bits of randomness,
  and `429` after repeated bad codes.
