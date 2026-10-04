# Conformance scenarios

Implementations must pass these scenarios before claiming conformance. This
repository currently checks schemas and examples, not server or client behavior.

| Area | Scenario and required result |
| --- | --- |
| Enrollment | Open enrollment becomes active; approval enrollment stays requested. Duplicate requests retain one participation identity. Revoked participants cannot self-resume. |
| Pairing | A code yields one working key and then fails with `401`, including when two clients race. Expired codes fail. The response has `Cache-Control: no-store`. Pairing the same installation again revokes the old key. Repeated bad codes return `429`. |
| Credentials | Unknown, expired and revoked keys fail with `401` on reads, part writes, renewal and finalization. A key limited to one project fails on another. A key cannot exceed the participant's role: a contributor's key cannot publish. Project routes fail without an active participation. |
| Scoped reads | A project-read token cannot read another participant's private status, submission or assessment job. All conditional scope alternatives are tested independently. |
| No locks | Two volunteers concurrently publish overlapping intents and upload useful data. Neither can exclude the other; completion and surplus policy determine credit. |
| Publication | A stale draft ETag fails with 412; missing precondition fails with 428. Publication is atomic and never mutates old requirements or their contribution windows. |
| Automatic framing | Register equipment, review bounds once, then check in as project deficits change. A compatible panel/center change can be adopted without a new prompt. It retains policy, demand and framing provenance. |
| Automatic bounds | A changed target position under the same ID, manual rotation, filter, setup revision, terms or time budget cannot escape the saved policy. `automatic_eligible` alone cannot authorize adoption. |
| Acquisition boundary | A framing revision changes only future work at a safe boundary. A capture underway retains its original objective/revision. Offline work stops starting after cached policy validity expires. |
| No useful work | Check-in returns wait or keep-current with a retry interval. Recommendation failure preserves the current plan until it expires. |
| Mosaic | Track depth for each target region. Credit a frame only where it meets coverage requirements. Count its integration once in project totals, even if it covers several regions. |
| Budgets | Multiple rigs, daylight-saving transitions, month boundaries and projects on two servers do not multiply local offered time. Accepted integration differs from attempted rig-hours. |
| Geometry | Check RA wrap, polar fields, rectangle orientation and coverage against the coordinate conventions. Request missing optical geometry before planning. |
| Quality | Missing optional FWHM is not a failure. Missing a required fresh solve cannot pass. Embedded WCS and target coordinates do not substitute for submitted-pixel evidence. |
| Retry | Same key/body replays before stale-precondition checks after authenticating. Changed body conflicts. Revocation blocks replay from revealing an earlier protected response. |
| Multipart | Parts arrive out of order; a lost receipt and repeated identical part do not duplicate bytes. Different content for a completed part conflicts. Validate final-part size and actual digests. |
| External delivery | A project without `external_delivery` rejects external artifacts. External artifacts get no upload session and await retrieval. Only maintainers record retrieval; a hash mismatch rejects the artifact. Credit follows only after verification and assessment. Links stay hidden from other participants and public views. |
| Partial batches | One corrupt artifact reports its own failure. Other artifacts remain inspectable. No accepted credit exists before validation and assessment. |
| Finalize races | Two clients finalize the same manifest concurrently and receive one job. An expired key cannot create duplicate submission/credit identities. |
| Recalibration | Rejecting a replacement leaves previous credit. Accepting it atomically supersedes the prior artifact; reassessment can subtract credit once. |
| Attribution | Same raw/artifact content under different producer identities triggers review without leaking another contributor's private manifest. |
| Terms/closure | Finalization requires current consent, including captures from older eligible revisions. Deadlines are exclusive. Closure preserves published late-submission rules. |
| Snapshot | A change during pagination is neither lost nor duplicated after installing the watermark. A revoked membership during pagination invalidates the snapshot. An expired cursor requires rebootstrap. |
| Tombstones | Access removal reveals no further private project contents; resume requires a new snapshot. User-owned local captures survive disconnection. |
| Status | Lower sequence is ignored, equal identical status retries, equal conflicting status fails. Expired status is visibly stale and cannot create accepted credit. |
| Abuse | Oversized JSON/images, decompression bombs, malicious paths, arbitrary fetch URLs and cross-project identifiers cannot bypass authorization or resource bounds. |

[Rejection fixtures](../tests/invalid-payloads.json) test structural constraints.
Implementations must separately test range ordering, referenced IDs, hashes,
unique capture credit and spherical coverage.
