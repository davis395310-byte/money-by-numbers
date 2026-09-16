# Best Bets stored-edges freshness policy

Local-only fallback for the Best Bets page. Production behavior is unchanged:
when a live odds API key is configured, the page uses live lines and this
policy never applies.

## As-of source

`snapshot_as_of` on `GET /api/edges` = the newest `timestamp` across the odds
snapshots backing the response. It is the snapshot's own ingest time — never
the current clock.

## Staleness threshold

One weekly refresh cycle: **7 days** (`STORED_EDGES_STALE_AFTER_DAYS` in
`backend/app/routers/odds.py` — the single place this is defined).

`snapshot_stale` is true when the snapshot is older than 7 days. The frontend
holds no threshold of its own; it renders from these two flags.

## Page states (key absent)

| Snapshot | Page shows |
|---|---|
| Fresh (≤ 7 days) | Stored edges table with a prominent "lines as of {date}" banner. Copy states the lines are a stored snapshot, not live odds. |
| Stale (> 7 days) | "Refresh pending" state: no table. Honest message that the stored lines are too old to show. |

## Copy rules

- Never imply profit or guaranteed outcomes.
- Never frame edges as against-the-spread winners. Edges are model
  probability vs. no-vig market-implied probability on the listed market.
- Every number shown comes from the stored snapshot; nothing is fabricated.
