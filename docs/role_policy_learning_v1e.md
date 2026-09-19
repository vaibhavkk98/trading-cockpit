# ROLE Policy Learning V1E

V1E is an observational, prospective-only ledger. It records an immutable
decision episode after a frozen policy has made and persisted its decision. It
has no import path into qualification, admission, ranking, sizing, exits,
exposure, or execution.

The activation record is created on the first post-deployment EOD call. Only
source decisions whose database creation time is strictly later than that
boundary are eligible. Existing decisions are deliberately not reconstructed.

Episode state is split into opportunity, portfolio, market, and action
snapshots. When a phase-specific immutable decision snapshot exists it is used.
Older control policies that expose only a same-session portfolio snapshot are
labelled `END_OF_CYCLE_CAUSAL_SNAPSHOT`; this evidence cannot silently become a
direct match.

ROLE-D1 10-session outcomes are linked read-only. Until they exist, lifecycle
is `NOT_MATURE`. Match groups require the same opportunity and signal date. A
`DIRECT_MATCH` additionally requires at least two actual legs in the existing
phase-specific match/snapshot tables. Otherwise evidence remains partial,
outcome-only, insufficient, or not mature.

Decision regret is defined exactly as chosen observed value minus the best
observed feasible alternative. It is emitted only when two or more actual
trades exist in a direct match. Unobserved or hindsight-optimal actions remain
`UNSUPPORTED`.

The review gate permits offline policy research only after 100 mature episodes,
60 direct matched comparisons, 40 signal dates, three strategies, three policy
families, and 90 calendar days. It never promotes a policy automatically.
