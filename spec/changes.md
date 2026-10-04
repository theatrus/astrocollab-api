# Draft changes

## 0.1.0-draft.1 — 2026-10-03

Extract the collaboration design from PSF Guard into an independent protocol.
Define project publication, participation credentials, equipment and capacity
offers, optional recommendations, nonexclusive intent, snapshot/change sync,
authenticated resumable uploads, versioned assessments and capture credit.

The earlier PSF Guard design's `/api/collab/v1` route sketches were not an
implemented interface. This draft uses a discovered API root, illustrated as
`https://collab.example/v1`. It specifies authenticated chunk uploads rather
than requiring a storage provider's signed-URL protocol.
