# Conformance scenarios

These are obligations for future server/client integration tests. This repository
currently validates only OpenAPI structure and payload fixtures; no runtime
conformance is claimed. A service must prove the stateful scenarios below before
advertising conformance to a stable release.

| Area | Scenario and required result |
| --- | --- |
| Enrollment | Open enrollment becomes active; approval enrollment stays requested. Duplicate requests retain one participation identity. Revoked participants cannot self-resume. |
| Credentials | Wrong audience/client/project, expired and revoked tokens fail on reads, part writes, renewal and finalization. Account tokens cannot upload. A maintainer cannot mint another account's token. |
| Scoped reads | A project-read token cannot read another participant's private status, submission or assessment job. All conditional scope alternatives are tested independently. |
| No locks | Two volunteers concurrently publish overlapping intents and upload useful data. Neither can exclude the other; completion and surplus policy determine credit. |
| Publication | A stale draft ETag fails with 412; missing precondition fails with 428. Publication is atomic and never mutates old requirements or their contribution windows. |
| Automatic framing | Register equipment, review bounds once, then check in as project deficits change. A compatible panel/center change can be adopted without a new prompt. It retains policy, demand and framing provenance. |
| Automatic bounds | A changed target position under the same ID, manual rotation, filter, setup revision, terms or time budget cannot escape the saved policy. `automatic_eligible` alone cannot authorize adoption. |
| Acquisition boundary | A framing revision changes only future work at a safe boundary. A capture underway retains its original objective/revision. Offline work stops starting after cached policy validity expires. |
| No useful work | A check-in may return wait/keep-current, without a fake allocation or an infinite retry loop. Recommendation failure leaves the existing still-valid local plan intact. |
| Mosaic | Two canonical tiles need independent depth. A wide frame may satisfy both when coverage allows; a narrow frame cannot claim the whole target from its center alone. Distinct project integration remains a capture union. |
| Budgets | Multiple rigs, daylight-saving transitions, month boundaries and projects on two servers do not multiply local offered time. Accepted integration differs from attempted rig-hours. |
| Geometry | RA wrap, polar fields, rectangle orientation and footprint coverage use the specified celestial conventions. Missing optics yields review rather than guessed geometry. |
| Quality | Missing optional FWHM is not a failure. Missing a required fresh solve cannot pass. Embedded WCS and target coordinates do not substitute for submitted-pixel evidence. |
| Retry | Same key/body replays before stale-precondition checks after authenticating. Changed body conflicts. Revocation blocks replay from revealing an earlier protected response. |
| Multipart | Parts arrive out of order; a lost receipt and repeated identical part do not duplicate bytes. Different content for a completed part conflicts. Validate final-part size and actual digests. |
| Partial batches | One corrupt artifact reports its own failure. Other artifacts remain inspectable. No accepted credit exists before validation and assessment. |
| Finalize races | Two clients finalize the same manifest concurrently and receive one job. An expired key cannot create duplicate submission/credit identities. |
| Recalibration | Rejecting a replacement leaves previous credit. Accepting it atomically supersedes the prior artifact; reassessment can subtract credit once. |
| Attribution | Same raw/artifact content under different producer identities triggers review without leaking another contributor's private manifest. |
| Terms/closure | Current consent is required for finalization, including older eligible capture revisions. Deadline boundaries are exclusive. Project closure does not silently erase published late-data policy. |
| Snapshot | A change during pagination is neither lost nor duplicated after installing the watermark. A revoked membership during pagination invalidates the snapshot. An expired cursor requires rebootstrap. |
| Tombstones | Access removal reveals no further private project contents; resume requires a new snapshot. User-owned local captures survive disconnection. |
| Status | Lower sequence is ignored, equal identical status retries, equal conflicting status fails. Expired status is visibly stale and cannot create accepted credit. |
| Abuse | Oversized JSON/images, decompression bombs, malicious paths, arbitrary fetch URLs and cross-project identifiers cannot bypass authorization or resource bounds. |

Structural rejection fixtures live in [tests/invalid-payloads.json](../tests/invalid-payloads.json).
They cover coordinate bounds, credential scope/expiry, unknown request fields,
capacity, automatic-region consent, artifact identity/digest, pixel scale,
assessment credit, recommendation review flags and snapshot/job state shapes.

Cross-field and database rules (for example ordered ranges, existing IDs, matching
digests, one credit per capture and real spherical coverage) are intentionally
listed as semantic requirements. JSON Schema cannot prove their implementation.
