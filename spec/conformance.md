# Conformance scenarios

Implementations must pass these scenarios before claiming conformance. The
[conformance tester](../conformance/README.md) checks many rows against a live
server; its README lists the rows it cannot check.

| Area | Scenario and required result |
| --- | --- |
| Pairing | A code yields one working key and then fails with `401`, including when two clients race. Expired codes fail. The response has `Cache-Control: no-store`. Pairing the same installation again revokes the old key. Repeated bad codes return `429`. |
| Credentials | Unknown, expired and revoked keys fail with `401` on every private route. A key limited to one project fails on another. Accounts that are not active members get no assignments for a project and cannot submit to it. |
| Privacy | A contributor cannot read another account's rigs, check-ins or submissions. External links are visible only to the submitter and the project's maintainers. |
| Rigs | `PUT` with an identical body keeps the revision; a change creates one. `PATCH` changes only the fields sent. A rig missing planning fields gets `wait` with `rig_incomplete`. |
| Asking for work | A check-in with only a rig ID returns `image` or `wait`. Each panel fits the rig's field, uses one of its filters, and meets the objective's sampling and exposure rules. A passband matches only under the protocol's rule: narrowband filters do not serve luminance objectives. |
| Sharing out the picture | For a target larger than the rig's field, rigs get panels from a grid, not whole mosaics. Every panel needs the goal's full depth. Two similar rigs get different panels when that spreads coverage. Later check-ins move a rig as panels fill. |
| No locks | Two rigs given overlapping panels both upload useful data. Neither excludes the other; credit follows assessment. |
| No useful work | Check-in returns `wait` or `continue` with a retry interval. A failed planning attempt leaves the current assignment in force until it expires. |
| Client duties | A new assignment changes only future frames. A capture underway keeps its assignment and panel. Clients stop starting frames after `expires_at`. |
| Geometry | Check RA wrap, polar fields, rectangle orientation and coverage against the coordinate conventions. |
| Quality | Missing optional FWHM is not a failure. Missing a required fresh solve cannot pass. Embedded WCS and target coordinates do not substitute for submitted-pixel evidence. |
| Reporting progress | A check-in's `unsubmitted_captures` replaces the rig's last report. Reported frames appear in progress as `reported_frames`, steer later assignments, and never earn credit. `[]` clears the report. |
| Retry | Repeating a submission with the same `id` and body returns the existing one with `200`; a changed body returns `409 id_conflict`. Finalizing twice and repeated check-ins create nothing new. |
| Multipart | Parts arrive out of order; a lost receipt and a repeated identical part do not duplicate bytes. Different content for a received part conflicts. Validate final-part size and actual digests. |
| External delivery | A project without `external_delivery` rejects external artifacts. External artifacts get no upload session and wait for retrieval. A hash mismatch after retrieval rejects the artifact. Credit follows only after verification and assessment. |
| Partial batches | One corrupt artifact reports its own failure. Other artifacts are still assessed. No credit exists before assessment. |
| Finalize races | Two clients finalize the same submission at once and get one assessment. |
| Recalibration | Rejecting a replacement keeps the earlier credit. Accepting it replaces that credit. |
| Stacked masters | A sub sent to a masters project, or a master to a subs project, fails with `422 deliverable_mismatch`. A master below `min_sub_count`, with a sub count that differs from its list, or with mismatched integration earns no credit. An accepted master credits its sub count once; a master repeating a credited sub is rejected with `duplicate_capture`. |
| Attribution | The same file under different contributors triggers review without showing one contributor's manifest to the other. |
| Terms and deadlines | Finalization requires current terms consent. Deadlines are exclusive. A new requirements revision does not change how earlier data is judged. |
| Abuse | Oversized JSON or images, decompression bombs, malicious paths, arbitrary fetch URLs and cross-project IDs cannot bypass checks or resource limits. |

[Rejection fixtures](../tests/invalid-payloads.json) test structural constraints.
Implementations must separately test range ordering, referenced IDs, hashes,
unique capture credit and spherical coverage.
