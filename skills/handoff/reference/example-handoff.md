# Example handoff: three finished cycles

The file below is `hq` output: three finished cycles on a synthetic
thread whose repo sits at `~/code/poller`. The cursor (Task through
Open questions) is the agent's; the header line and everything from the
first `<!-- hq:` marker down is the script's. The golden test in
`tests/test_hq_messages.py` replays the three cycles and compares the
rendered file to this one line for line.

```markdown
# Handoff: auth-token-refresh

Written: 2026-08-26 | Cycle: 3 | master @ fcbab89 | dirty: scripts/auth.py

## Task
Refresh expired OAuth tokens in the poller instead of failing the run.

## Now
Wire refresh_token() into poll() at scripts/auth.py:88, in the 401 branch.

## Plan
- [x] Steps 1-3: token store, refresh endpoint, unit tests (cycles 1-2)
- [ ] Wire refresh into the poll() 401 branch
- [ ] Integration test against the staging IdP

## State
- Verified: refresh_token() round-trips against staging (cycle 2).
- Unverified: retry backoff - written, never exercised.

## Environment
- test: python3 -m pytest tests/ -q
- staging IdP secret: env IDP_CLIENT_SECRET, set in ~/.env.staging

## Open questions
- Cap retry backoff at 60s, or give up after five tries? Blocks the
  integration test.

<!-- hq:read 68645f0c76e2 -->
## Read first
root ~/code/poller
.handoff/auth-token-refresh/specs/SPEC.md:11-13  (14 tok)  refresh contract; s3 is the retry schedule
<!-- /hq:read -->

<!-- hq:artifacts f8c343183272 -->
## Artifacts
root ~/code/poller
.handoff/auth-token-refresh/specs/SPEC.md  spec  always  c1
scripts/auth.py  draft  edit  c1  poller; the 401 branch is under edit
.handoff/auth-token-refresh/notes/idp-quirks.md
hq when auth-token-refresh <path> prints any row above whole
<!-- /hq:artifacts -->

<!-- hq:standing 936662dbc05b -->
## Standing
### Constraints
[c01] Never log token values
### Decisions
[d01] Refresh in-process, no sidecar
### Dead ends
[x01] httpx event hooks for auto-refresh
[x02] A pid in the lock.
hq standing auth-token-refresh <id> prints any item above whole
<!-- /hq:standing -->

## Log
- 2026-08-24 (cycle 1, master@fcbab89 +1): token store and refresh endpoint written
- 2026-08-25 (cycle 2, master@fcbab89 +1): refresh verified against staging; backoff added
- 2026-08-26 (cycle 3, master@fcbab89 +1): poll() wiring started
```

