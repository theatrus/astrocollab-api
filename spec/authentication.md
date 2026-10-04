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
     "installation_id": "00000000-0000-4000-8000-000000000020"
   }
   ```

   [Full response](../examples/pairClient.response.json).
4. **Store the key** in the system credential store, not a plain config file.
   The server never shows it again.
5. **Check it:**

   ```sh
   curl -H "Authorization: Bearer $KEY" $API/me/projects
   ```

   `200` means the key works and lists the user's projects. `401` means the key
   is wrong, expired or revoked.
6. **Use it on every route.** The same key lists projects, registers rigs, asks
   for work and uploads frames.

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

A key acts for its account. It can do everything a contributor does: list the
account's projects, register rigs, check in and submit data. When issuing the
pairing code, the user may limit the key to some projects or set an expiry.

On project routes the server also checks membership: only active members of a
project receive assignments for it and submit data to it. A key limited to other
projects gets `403` or `404`.

## Server checklist

To support API keys, a server needs:

- web pages where users issue pairing codes and list and revoke keys;
- `POST /pair`, which consumes a code and creates a key in one transaction, and
  revokes any earlier key for the same account and installation;
- at least 128 bits of randomness in each code and key, stored only as hashes;
- codes that work once and expire within an hour, and a `429` after repeated
  bad codes;
- for each key: account, client name, installation, optional project list,
  optional expiry, optional rig, last use;
- on each request: look up the key by hash; reject unknown, expired or revoked
  keys with `401`; then check membership.

The [reference server](../reference/README.md) shows one way to do this.

## Keeping keys safe

- Send keys only in the `Authorization` header, only over HTTPS, and only to the
  server's own origin.
- Never write them to URLs, logs, manifests or status messages.
- Pair each installation separately, so the user can revoke one machine at a time.
- On `401`, stop and ask the user to pair again. Do not retry in a loop.
