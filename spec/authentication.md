# Authentication

This version uses API keys only. Every private request carries one header:

```http
Authorization: Bearer <api key>
```

Each client installation, such as each rig, gets its own key by pairing. There
is no sign-in flow and nothing to refresh.

## Pairing a client

1. **The user issues a pairing code.** Show `account_url` from
   `GET /capabilities`. On that page the user issues a code and picks what the
   key may do.
2. **The user enters the code in the client.**
3. **The client trades the code for a key.** No `Authorization` header:

   ```sh
   curl -X POST $API/pair \
     -H "Content-Type: application/json" \
     -d '{"pairing_code": "acpc_...", "installation_id": "'$INSTALLATION'", "client_name": "Roof rig 2"}'
   ```

   ```json
   {
     "key_id": "00000000-0000-4000-8000-000000000018",
     "api_key": "acpk_...",
     "client_name": "Roof rig 2",
     "scopes": ["account:read", "participation:manage", "project:read", "submission:write"]
   }
   ```

   [Full response](../examples/pairClient.response.json).
4. **Store the key** in the system credential store, not a plain config file.
   The server never shows it again.
5. **Check it:**

   ```sh
   curl -H "Authorization: Bearer $KEY" $API/me/participations
   ```

   `200` means the key works and lists the user's projects. `401` means the key
   is wrong, expired or revoked.
6. **Use it on every route.** The same key joins projects, registers equipment
   and uploads frames.

Make `installation_id` a random UUID when the client is installed, and keep it.
Pairing again with the same ID replaces that installation's old key, so a user
can re-pair a rig without leaving stale keys behind.

A code works once and expires within an hour. If pairing fails with
`401 invalid_pairing_code`, ask the user for a new code. Never retry pairing on
your own: if the response was lost, the user issues a new code and revokes the
orphan key on the account pages.

Some servers also let users copy a key from the account pages. Treat a pasted
key the same way: store it, check it, use it.

## What a key can do

When the user issues a pairing code, they pick the key's scopes and may limit
it to some projects. Account routes, such as joining a project, need only the
key's scopes. On a project route, the server allows a request only when all of these
hold:

- the account has an active participation in that project;
- the key covers that project;
- both the key and the participant's role include the needed scope.

So a contributor's key cannot publish a project, even if the key lists
`project:manage`. Only the project's maintainers hold that scope.

| Scope | Allows |
| --- | --- |
| `account:read` | Read memberships and sync project data. |
| `participation:manage` | Join, pause, resume or leave projects; renew terms consent. |
| `project:create` | Create projects. |
| `project:read` | Read project data, your own offers, progress and jobs. |
| `offer:write` | Register equipment, offer time, set planning policy, check in. |
| `intent:write` | Publish planned work. |
| `status:write` | Report live status. |
| `submission:write` | Create submissions, upload parts, finalize. |
| `submission:read-own` | Read your own submissions and assessments. |
| `project:manage` | Maintainers: edit and publish requirements. |
| `participation:review` | Maintainers: approve or revoke members. |
| `assessment:write` | Maintainers: record manual assessments. |
| `submission:read-all` | Maintainers: read every submission's metadata. |

The API reference lists the scopes each operation needs. The protocol gives the
[full rules](protocol.md#scopes-and-contexts).

## Server checklist

To support API keys, a server needs:

- an account page where users issue pairing codes and list and revoke keys;
- `POST /pair`, which consumes a code and creates a key in one transaction, and
  revokes any earlier key for the same account and installation;
- at least 128 bits of randomness in each code and key, stored only as hashes;
- codes that work once and expire within an hour, and a `429` after repeated
  bad codes;
- for each key: account, client name, installation, scopes, optional project
  list, optional expiry, last use;
- on each request: look up the key by hash; reject unknown, expired or revoked
  keys with `401`; then check membership and scopes as above;
- the key's ID as the client ID for idempotency keys, status writers and sync
  cursors.

The [reference server](../reference/README.md) shows one way to do this.

## Keeping keys safe

- Send keys only in the `Authorization` header, only over HTTPS, and only to the
  server's own origin.
- Never write them to URLs, logs, manifests or status messages.
- Pair each installation separately, so the user can revoke one machine at a time.
- On `401`, stop and ask the user to pair again. Do not retry in a loop.
