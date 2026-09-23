---
name: handoff
description: >-
  Write or read a session handoff under .handoff/ - the exit the
  context-budget warnings point at. Use near the budget or to resume a
  prior session; replaces /compact.
allowed-tools: Bash(hq *)
---

# Handoff

One folder per task thread, `.handoff/<slug>/` at the repo root
(`git rev-parse --show-toplevel`; outside a repo, the nearest ancestor
of the cwd that already holds `.handoff/`, else the cwd). Its
`HANDOFF.md` carries what a fresh session needs to resume. Write near
the budget, then kill the session: only this folder and the repo
survive.

The plugin puts `hq` on the agent's PATH. After a plugin update
mid-session `hq` fails with `command not found`: run `/reload-plugins`,
or until then call
`~/.claude/plugins/cache/claude-handoff/claude-handoff/<version>/bin/hq`.
Every verb but `list` and `help` takes the slug first. Exit 0 is
done; 1 is a refusal or a blocking finding, and nothing is written
but a refused `stamp`'s receipt row; 2 is a usage error.
Every line that calls for a move names it: `<finding> - <what to do>`.

## The folder

```
.handoff/auth-token-refresh/
+- HANDOFF.md      header line; cursor: ## Task ## Now ## Plan ## State
|                  ## Environment ## Open questions ## Unfiled; then the
|                  blocks <!-- hq:read --> <!-- hq:artifacts -->
|                  <!-- hq:standing --> and ## Log
+- ledger.tsv      append-only stamps
+- standing.md     append-only decisions, constraints, dead ends
+- cycles/         each finished HANDOFF.md verbatim, c01.md ..
+- .hq.lock        held from begin to finish
+- .hq.done        marked done; list skips it
+- HANDOFF.orig.md the foreign file an adoption began from
+- work-dir        the pin, only when the work already lives elsewhere
+- notes/          what the thread learned: evidence, excerpts, reviews
+- specs/ drafts/ outputs/  what it made, by kind; probes/ or any other
|                  folder is one unit
```

The agent writes the cursor (Task through Open questions) and
`## Unfiled`, and never opens `ledger.tsv`, `standing.md`, or
`cycles/`: it dictates them through `hq stamp`, `hq note`, and
`hq supersede`. The script writes everything else; a hand edit to the
header line or below the first `<!-- hq:` marker is overwritten by
`finish`.

Everything the thread learned or made lives in the folder under the
kind folder the diagram names, never loose at its top level; any file
name serves but a snapshot-shaped one (`*.bak`, `*.orig.*`, `*.prev.*`,
`*.pre-*`, `cycle<N>`), which stays a snapshot; `probes/` or a name the
work calls for is stamped as one unit or file by file. One exception,
judged at the first `hq begin`, whose work list ends in `work dir:`
and the verb:
when the thread's spec and experiments already live in a project
directory, `hq work-dir <slug> <dir>` pins it once - a directory
already on disk, inside the repo or out, never the root, `.handoff/`,
or the system temp directory unless the repo itself sits under it. From
then on the specs, drafts, and outputs this thread makes go there, each
named as its neighbors are and stamped by its `~` or absolute path,
`--kind spec` or `--kind draft` to gate it, else `--read-before
mention` when the cursor points at it so its label prints; `notes/`
stays in the folder. `hq work-dir <slug>` prints the ruling, `--clear`
undoes it. The agent invents no project directory and proposes none.
Only a file that outlives the session or is stamped is bound.

## Which verb, which target

One question settles the verb: does this session hold anything worth
telling a fresh session?

- Yes - a file edited, a decision settled, a finding the repo does
  not already record, a handoff read here. Write.
- No - a session whose opening move this is, or one that has only
  looked something up. Read.

Ask it of the content, never of how late the session is.

| Input                              | Action                                 |
| ---------------------------------- | -------------------------------------- |
| `/handoff`                         | write; read when the session is fresh  |
| `/handoff <slug>`                  | the same, against `.handoff/<slug>/`   |
| `/handoff list [n]`                | this repo, newest first; n caps it     |
| `/handoff check [slug]`            | review one handoff in place, fix it    |
| `/handoff when <slug> <path>`      | query the ledger: one path's rows, the |
| `/handoff diff <slug> <c1> <c2>`   | cursor change between two cycles,      |
| `/handoff artifacts <slug>`        | every live row, every standing item    |
| `/handoff standing <slug> [--all]` | (the last section)                     |
| `/handoff done <slug>`             | end the thread: `hq done`              |
| `/handoff done <slug> --undo`      | reopen it: `hq done --undo`            |

`write` and `read` as the first word override the inference, an
optional slug after each. A first argument matching a verb above is
that verb, not a slug.

Guess neither the verb nor the target. Where either is ambiguous,
say so, list the candidates, and stop - touch nothing.

An argument is always a folder under `.handoff/`, never the
`HANDOFF.md` inside. Read, check, done, when, diff, artifacts, and
standing resolve it the same way: exact name, else a unique prefix of
the `.handoff/*/` names, else list the candidates and stop. A write
resolves on the exact name alone and creates the folder where none
exists, so a longer neighbor never claims the slug typed. A target
exists when its `HANDOFF.md` exists.

## write

Target, first match wins - an argument is never required:

1. an explicit folder argument
2. the handoff this session read, wrote, or checked, when the work
   since has been that same task; several threads this session - name
   the candidates and ask
3. an existing `.handoff/` folder whose slug or Task line matches this
   session's task - update it, never create a twin
4. a new slug: 2-4 kebab-case words naming the task as this session
   would state it (`auth-token-refresh`), unique under `.handoff/`

What the target holds decides the route; the write path is the same
in every case:

- `ledger.tsv` exists: update. Read the old `HANDOFF.md` first if this
  session has not.
- `HANDOFF.md` with a conforming `Written: | Cycle:` header and no
  `ledger.tsv`: `begin` runs `adopt` itself. No copy by hand.
- `HANDOFF.md` with no conforming header: `hq adopt <slug>` refuses it
  and names `reference/adoption.md`; convert the file by that
  procedure first.
- no folder: `begin` creates it with an empty cursor.

`finish` writes the header line from the git state, or without git
ends it after the cycle. The agent collects no anchors. Background
tasks still running, and any second repo this session changed (path,
branch, sha), go under Environment.

### The file

The hand-written half of a real file, three cycles in
(`reference/example-handoff.md` is the whole file); the rules it is
written by print at `begin`:

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

<!-- hq:read 53c21ca4645f -->
```

### The artifact ledger

`stamp` and `note` take `--batch` too: one row per stdin line, the
same arguments minus the slug. Stamp forms:

```
hq stamp <slug> specs/SPEC.md \
  --where "3. Retry" --label "refresh contract; s3 is the retry schedule"
hq stamp <slug> ~/code/poller/scripts/auth.py \
  --kind draft --label "poller; the 401 branch is under edit"
hq stamp <slug> specs/SPEC.md \
  --successor specs/SPEC-v2.md
hq stamp <slug> --batch <<'ROWS'
specs/SPEC.md --where "3. Retry" --label "the contract"
notes/idp-quirks.md --read-before edit --label "staging IdP quirks"
ROWS
```

Note and supersede forms:

```
hq note <slug> decision \
  --headline "Refresh in-process, no sidecar" "One caller; latency is fine."
hq note <slug> --batch <<'ROWS'
constraint --headline "Never log token values" "Not even at debug."
dead-end --headline "httpx event hooks" "A hook cannot retry the request."
ROWS
hq supersede <slug> d17 d23
```

### Write path

Run these steps in order:

1. `hq begin <slug>` takes the lock and prints the work list, ending
   in `cycle N begun by <session> on <host>`, then the writing rules
   (`hq help write`: cursor, ledger tiers, Unfiled, a todo file the
   Plan points at). Make each move in steps 2 and 3; the printed
   rules bind this cycle. `hq begin: lock held by <session>
   on <host> since <time>; use --force to take over` and `hq begin:
   lock file unreadable; use --force to take over` (exit 1) mean
   another session is inside a cycle less than two hours old: stop and
   say so; `begin --force` only when the user confirms that session is
   dead. `note`, `stamp`, and `supersede` print the same two refusals
   under their own names, ending `run hq begin <slug> --force to take
   over`: stop the same way, and run that only on the same
   confirmation. An older lock is taken over without `--force`.
2. One `hq note` per item settled this session, or one `--batch`; one
   `hq supersede <slug> <old-id> <new-id>` when a ruling reverses an
   earlier one.
3. Place each file this session made by `## The folder` before its first
   stamp; then one `hq stamp` per artifact created, re-read, or moved,
   or one `--batch`. A refusal names the way out: a successor, an
   archive reason, or leave the row gated - unless its file is missing,
   which blocks `finish` until the row is re-pointed, superseded, or
   archived.
4. Rewrite the cursor from `## Task` down, above the first `<!-- hq:`
   marker, by the printed rules, carrying forward every line this
   session did not settle; leave the header line and everything below
   the marker alone.
5. Run the Reviewer pass (below); each surviving finding becomes a
   `note`, a `stamp`, or a cursor edit in this cycle - return to step 2
   for it.
6. `hq finish <slug> --log "<one line for the Log>"`. It exits 1 on
   the first blocking line; make its move and re-run. `advisory:`
   lines never block. Then it drains Unfiled, renders the blocks, writes
   the header and Log, archives the file to `cycles/cNN.md`, releases
   the lock, and prints `<path>  N cursor lines  N tokens (cursor a,
   read b, artifacts c, standing d)`, a `read first:` size line, and
   `resume: /handoff <slug>`. A `:?` in the read block does not
   block.

The pair that brackets a cycle:

```
hq begin auth-token-refresh
hq finish auth-token-refresh \
  --log "token store and refresh endpoint written"
```

### Reviewer pass

With the file on disk, spawn the skeptic below before `finish`. It
returns one numbered item per finding, `<n>. <finding>`; re-check
each in the write session and stop - never loop. A question it raises
for the user goes under `## Open questions`; do not stop for it. Only
`--no-check` from the user, anywhere in the call, skips this pass;
the agent never grants itself the skip.

- Skeptic (Agent tool, at the executing tier the host's agent
  rules name - a host with none takes type `general-purpose`, model
  `sonnet` - and at the judging tier, or model `opus` there, only
  where the Now step's correctness turns on a span the skeptic must
  read code to settle: absent one, the contradiction clause below is
  vacuous; the session names its tier and reason before launching):
  from the file alone, fill five slots - the task, the next action,
  why it is next, how to verify it, what to ask the user; an empty
  slot is a finding. For each line of the read block, open the
  resolved span and report any point where the Now step contradicts
  it. For each todo file the Plan points at, verify that the Now step
  agrees with its live item. `finish` renders the header line and the
  blocks after this pass, so they are a cycle behind: a stale or
  absent sha, cycle, id, row, or label there is never a finding. Read
  a standing id the cursor cites with `hq standing <slug> <id>`, never
  from the block.

### Report

Whatever else the turn contains, its final message relays verbatim
the path-and-size line `finish` printed and its `resume:` line, and
tells the user to kill this session and start a fresh one before
running that resume line - with `--no-check` too.

## read

1. Resolve `<slug>` per the shared rule. With none given, run `hq list`:
   one line, read that slug; several, list them and ask which; none,
   say so. The last two stop there - never pick the newest, and never
   fall through to write.
2. Run `hq open <slug>`. It is read-only and prints what is wrong,
   then the remaining steps (`hq help read`); each binds.

In the generated blocks, a line ending in `- hq artifacts <slug>
...`, `- hq when <slug> <path>`, or `- hq standing <slug> ...` is a
command to run, as printed, when the held text is wanted; `path spec?
unstamped` is a file that needs a stamp. The Standing block already
holds the items: `hq standing` is for a held body or `--all`, never a
re-read.

## check

Resolve like read, then run `hq open <slug>` and the printed reading
steps only as far as the Read-first reads: read `HANDOFF.md` and each
spanned Read-first line, then stop - a check neither drift-checks nor
executes Now, and open questions do not stop it. Then run the write
path: `begin`;
steps 2 to 4 only for findings - what `open` printed and what the
skeptic returns - as notes, stamps, and cursor edits; the Reviewer
pass, skipped only by the user's `--no-check`; `finish --log "check:
<n> findings applied"`, counting both kinds. A check with nothing to
apply still runs `finish`, which releases the lock and advances the
cycle. A target with no conforming header runs the adoption pass and
stops.

## list

`hq list [n]` prints the open threads, newest first, `n` capping the
lines; `hq list --done` lists the threads marked done instead. It
writes nothing. Show the user the lines unchanged; `hq list --help`
names the columns.

## done

Resolve `<slug>` per the shared rule; with none given, list the
candidates and stop - never pick the newest: ending the wrong thread
is not a mistake a later cycle corrects. Run `hq done <slug>`, with
`--reason "<line>"` when the session knows why. `done` ends the
thread; `finish` ends one cycle. Show the output unchanged. The
marker, `--undo`, `--force`, and a repeated `done` are in
`hq done --help`.

## when, diff, artifacts, standing

Each runs the `hq` verb of the same name and shows the user its output
unchanged; none writes.

- `hq when <slug> <path>`: every ledger row for the path, the path
  resolving as at `stamp`.
- `hq diff <slug> <c1> <c2> [<section>]`: the cursor change between
  two cycles; no output means identical.
- `hq artifacts <slug>`: every live row plus unstamped files, then
  the non-live counts. `--all`: the whole ledger instead - every row
  as stored, history and non-live rows in file order under the
  `--tsv` header, no unstamped files, no `missing` rewrite, no
  counts; a filter selects on the row it prints, not the path's
  current row.
- `hq standing <slug> [<id> ...]`: every unsuperseded item in full, or
  the named items.

Output formats and the filter flags are in each verb's `--help`.

## The hooks

Two hooks report and, unless the operator sets `HQ_GATE_DENY`, never
block; `finish` consults neither. Once `hq open` arms it, at a write
in reach - under the root or the armed folder's pinned work dir - the
gate names each unread gated path once: an `always` row at the first
such write, an `edit` row at a write to that file. A read is a Read
tool call, the path as an argument of its own
`cat`/`head`/`tail`/`less`/`sed -n` segment, or an `hq read` receipt;
`ls`, `wc`, `grep`, a `stamp`, and a path piped into `head` are none.
At the end of a turn, the Stop hook names a `HANDOFF.md` written by
hand since its last finished cycle and the `begin`/`finish` pair that
files it.

## Adoption

`hq adopt <slug>` changes nothing but prints the heading inventory;
after `reference/adoption.md`, continue at write path step 1, where
`begin` runs `adopt` and opens cycle 2.

## Where the rest lives

`reference/adoption.md` and `reference/example-handoff.md` sit beside
this file. `hq help write`: the writing rules `begin` prints. `hq help
read`: the reading steps `open` prints. `hq help anchors`: every
`--where` form. `hq help kinds`: kinds, tiers, ledger fields, flag
values. `hq help rules`: R1 to W2 in full. `hq help stale-path`:
fixing a folder path under a former directory. `hq <verb> --help`:
each verb's columns.
