# Conformance scenarios

Implementations must pass these scenarios before claiming conformance with this
draft. The [conformance tester](../conformance/README.md) checks them against a
running server; it runs against both the [reference server](../reference/README.md)
and Starfront's server.

| Area | Scenario and required result |
| --- | --- |
| Discovery | `GET /api/v1/health` needs no token and gives `ok`, the protocol number and the server's version. `features` lists `signin` or `pairing`, or both. |
| Tokens | A person token cannot say hello, fetch work or report; a telescope token cannot enrol or list telescopes. Both get `401`. A missing or unknown token gets `401`. Pairing, where offered, works once and then fails with `401`. |
| Hello | A hello stores the rig's profile; browsing then judges compatibility from it. A hello with a newer protocol gets `409`. |
| Browsing | `GET /api/v1/agent/projects` lists every open project with a compatibility check per rule, the hours collected, who takes part and progress per filter. The depth map covers the region with cells and gives seconds per filter for each; accepted reports raise it. |
| Joining | A rig that meets a project gets an `accepted` share: the whole region tiled with cells that fit its field, turned to a fixed camera's angle, or one cell for a `single` project. Joining twice returns the same share. A rig that has not described its optics gets `400`; a rig that cannot meet the project, including one with none of the wanted filters, gets `409` with the reason. A rig with only some of the wanted filters can join. |
| Tonight | Asking twice in the same night returns the same panels and version, even after the rig reports different hours or a different Moon. Asking in the next night deals again. Without a night, a list holds for 20 hours. |
| Dealing | A rig is dealt only filters it carries. A mosaic night uses one filter. Every visit has at least `minFramesPerVisit` frames in each filter. Two rigs on one mosaic get different panels on the same night. Panels at full depth are not dealt. |
| Moon | With a bright Moon up most of the night, a mosaic that wants H and O deals H or S. On a dark night it deals the filter with the most depth still wanted, preferring L, R, G, B and O. |
| Reports | A report returns one verdict per record, in order. Accepted records add depth where their footprints lie and change later dealing. |
| Judging | A record outside a rule is rejected with a reason naming it. A star-size, guiding or scale rule that cannot be checked for lack of a measurement is listed under `unverified`, not passed. |
| Duplicates | Sending the same telescope, share, night, filter and panel again keeps one record, at the larger figure, and says `duplicate`. |
| Presence | A telescope that said hello in the last 25 minutes is listed as online, with only what it chose to share, under the name it gave in its presence. |
| Errors | Errors are `{"detail": ...}`. A body that does not match its type gets `422` with a list of bad fields. Unknown fields are kept or ignored, never refused. |

[Rejection fixtures](../tests/invalid-payloads.json) test structural constraints
on the examples.
