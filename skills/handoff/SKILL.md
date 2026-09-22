---
name: handoff
description: >-
  Write or read a session handoff under .handoff/ - the exit the
  context-budget warnings point at. The verb is inferred, never typed.
  Bare /handoff writes or updates this session's
  .handoff/<slug>/HANDOFF.md. In a fresh session it reads one back
  instead. list shows what exists, check reviews one in place, and
  when, diff, artifacts, standing query the ledger. Replaces /compact,
  and replaces re-planning: the file carries the approved plan across
  sessions.
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

Write for a reader with no memory of this session and full access to
the repo: short technical documentation in complete sentences, no
transcript narration. Task and Now are required; omit any other cursor
section that would be empty. A requirement the user stated, an
approval given, a quirk found is lost unless written here. In doubt,
write it down.

The hand-written half of a real file, three cycles in
(`reference/example-handoff.md` is the whole file):

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

Rules:

- Point, never paste: a rehomed sibling is stamped with `hq stamp`;
  its pointer line is generated, never typed.
- Skip what the repo records: git history, CLAUDE.md, README content.
- Too big for the file but worth keeping: a sibling `notes/<topic>.md`,
  stamped `--read-before edit` when the cursor points at it, so its
  label, to 120 characters, stays in the Artifacts block instead of a
  count.
- Name where a credential lives, never its value.
- Absolute dates. ASCII only.
- Now is the single next action; Plan, the approved plan, is what
  follows it; neither is re-opened.
- Now alone is spent each cycle. Every other line stays (a done Plan
  item ticked `- [x]`) or moves whole to a `note` body or a stamped
  sibling - what a resuming reader does not need first moves, never
  cut; `finish` refuses a dropped line.
- Anything still awaiting the user - a question, an unapproved plan -
  goes under `Open questions`; read stops there.
- An item recorded with `note` or under `## Unfiled` is not repeated
  in State: the Standing block carries it.
- No line ceiling binds the cursor: a block holds text back over a
  store; the cursor has none, so a reworded line drops facts.

### The artifact ledger

`stamp` and `note` take `--batch` too: one row per stdin line, the
same arguments minus the slug.

`read_before` is a tier, and `stamp` seeds it from the kind: a spec
seeds `always`, a draft `edit`, all else `never`. Of several `SPEC*`
stem-mates only the newest is gated.

| tier | read | when |
| --- | --- | --- |
| `always` | the contract | every resume |
| `edit` | before a change to it | the gate, at a write to it |
| `mention` | on demand | `hq read` |
| `never` | on the record | `hq artifacts`, `hq when` |

`always` requires an anchor: a stamp that would leave a live `always`
row with no `--where` is refused and the file's headings print under
the refusal; a spec needed whole is anchored at its title heading. A
whole file is never eager: a note or a source file the cursor points
at is `edit` or `mention`.

Rules the script enforces:

- R1 A spec row keeps `always` and a draft row `edit` or `always` -
  inferred, stored, or by `--kind`; both keep `live` and their kind,
  unless `--successor` names a live file on disk other than itself, or
  `--archive --reason` is given. A path that has ever been spec or
  draft stays gated: its way back to `live` is that tier.
- R2 A refused stamp still appends a receipt row, `reason` set to
  `refused: <why>`; it clears nothing.
- R3 A live row graded always or edit whose file sha moved blocks
  `finish`; a re-stamp alone clears it.
- W1, W2 An edited recorded line in `ledger.tsv` or `standing.md` is a
  hard fail in `finish`; when a known tool caused it (a formatter, a
  merge), pass `--acknowledge "<reason>"` to `finish`.

Stamp forms:

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

Path resolution, the values a re-stamp carries forward, and the
status moves `--successor`, `--archive --reason`, and `--defer` make
are in `hq stamp --help`; every `--where` form is in `hq help
anchors`. `stamp` accepts a wrong anchor, which shows only as
`SPEC.md:?` in the read block - check the heading first.

`note` takes a kind - `decision`, `constraint`, `dead-end` - a one-line
`--headline`, and the body as the last argument. `supersede` takes two
ids of one kind, old then new, and echoes the item it drops from the
block; a ruling inside that text which still holds takes one more
`hq note` under a new id.

```
hq note <slug> decision \
  --headline "Refresh in-process, no sidecar" "One caller; latency is fine."
hq note <slug> --batch <<'ROWS'
constraint --headline "Never log token values" "Not even at debug."
dead-end --headline "httpx event hooks" "A hook cannot retry the request."
ROWS
hq supersede <slug> d17 d23
```

### Unfiled

Items settled this session with no `note` call go under `## Unfiled`,
above the first `<!-- hq:` marker, as typed bullets, one per item:

```
- decision: **<headline>** <body>
- constraint: **<headline>** <body>
- dead-end: **<headline>** <body, wrapped onto indented lines
  when long>
```

A bullet with no bold span takes its first sentence as the headline
(a one- or two-word sentence, `Cycle 26.`, is a label and the headline
runs on). `finish` drains the section into `standing.md`; an untyped
bullet is a hard fail that writes nothing. Omit the section when every
item went through `note`.

### A plan that lives in a todo file

Work often has a ledger of its own - `todo/foobar.md`, tracked in the
repo. One home per fact, or the copies drift: the todo file owns what
is open and done; the handoff owns how this thread works it - state,
decisions, the Now step.

- Plan points at the live item (`todo/foobar.md item 3`) and keeps
  only thread-only steps of its own. Never copy the item's text across.
- write syncs the todo first - mark what this session closed, append
  what it found, in the todo file's own format - then writes the
  handoff against the result. A dirty todo shows in the header.
- On first pointing at an item, add one back-pointer line under it,
  `entry: .handoff/<slug>/HANDOFF.md`, and nothing else from here.
- An untracked todo file cannot anchor to a sha: mark the pointer
  `(untracked)`, and at read its current content is the truth.

### Write path

Run these steps in order:

1. `hq begin <slug>` takes the lock and prints the work list, then
   `cycle N begun by <session> on <host>`.
   Make each move in steps 2 and 3. `hq begin: lock held by <session>
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
   marker, carrying forward every line this session did not settle;
   leave the header line and everything below the marker alone.
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

Whatever else the turn contains, its final message names the absolute
path, the size `finish` printed, and the resume line - with
`--no-check` too:

```
Wrote /home/me/code/poller/.handoff/auth-token-refresh/HANDOFF.md
  (~528 tokens, cycle 3).
Resume: kill this session, start a fresh one, run
  /handoff auth-token-refresh
```

## read

1. Resolve `<slug>` per the shared rule. With none given, run `hq list`:
   one line, read that slug; several, list them and ask which; none,
   say so. The last two stop there - never pick the newest, and never
   fall through to write.
2. Run `hq open <slug>`. It is read-only and prints only what is
   wrong; none stops the read: a
   `WARNING:` (an edited recorded line), an unfinished cycle, or
   `LEDGER BEHIND` is reported and read past; `git drift` names the
   `git log` to run; a moved sha, an unresolved anchor, or a moved span
   says what to read instead; a `stale folder path` waits for the next
   write.
3. Read `HANDOFF.md`. Then run `hq read <slug> <path>` on every
   `## Read first` line that shows a span: it prints the span and
   records the read; a line printed instead names its move. A
   whole-file row, an older cycle's, is read when the Now step names
   its file or the gate names it at a write, never before. A section
   the row does not anchor is read when the work needs it:
   `--section <anchor>` in `--where` form, or `--whole` for the file
   whole. Then read every todo file the Plan points at. A conforming
   target with no `ledger.tsv` has no blocks yet: follow its `## Key
   files` `Read now:` pointers by hand. A target with no conforming
   header names its own reading order - follow it. Read nothing else.
4. Drift check: the header says dirty - run `git status --porcelain`
   and note what is still uncommitted. No sha in the header (written
   outside a repo) or no conforming header - skip the git steps. A
   pointed todo moved or disagrees with the file: the todo wins on
   what is open or done, the handoff on approach and decisions. Note
   drift in one line and proceed.
5. Do not re-plan and do not reopen a Standing item. Open questions
   present: recap in six lines at most - the task, what is settled,
   what each question decides, each restated for a reader outside
   the thread - then put them to the user and stop. Otherwise state
   the task and the Now step in two sentences, then execute Now; the
   Plan follows.
6. A later bare `/handoff` targets this handoff - write rule 2.

In the generated blocks, `Read first` has one line per live `always`
row, with its label and the size of what `hq read` prints:
`.handoff/<slug>/specs/SPEC.md:11-13  (320 tok)` is where its anchor
resolves, `(N lines, T tok)` a whole file an older cycle left,
`:?` an anchor matching no heading - read it `--whole`, re-stamp
with a `--where` that resolves, re-run `hq open`. `Artifacts` prints
one line per live row graded always, edit, or mention - `path  kind
tier  cN  label`, no label on an `always` row, `Read first` carries
it - and `path spec? unstamped` for a file with no row; live
`never` rows collapse to a count, and rows no longer live to
`superseded`, `archived`, or `missing` counts - a file gone from the
disk reads `missing` however the ledger stored it. `Standing` ids are
`d` decision, `c` constraint, `x` dead end; each kind prints its
newest cycle first, one cycle's items in recorded order. A constraint
prints its headline and the first sentence of its body, a decision or
dead end its headline. A line holding text back ends in the command
that prints the rest, runnable as printed:
`- hq artifacts <slug>` on the `never` count and
`- hq artifacts <slug> --status <status>` on each non-live count;
`+Nc - hq when <slug> <path>` on an `edit` or `mention` label past
120 characters; `- hq standing <slug> --all` on the superseded count
and `+Nc - hq standing <slug> <id>` on a held body. Every path
resolves from the base the first line names; the folder's own files
carry `.handoff/<slug>/`. In `Log`, `+1` counts dirty paths.

## check

Resolve like read, then read steps 2 and 3; open questions do not stop
a check. Then run the write path: `begin`; steps 2 to 4 only for
findings - what `open` printed and what the skeptic returns - as notes,
stamps, and cursor edits; the Reviewer pass, skipped only by the
user's `--no-check`; `finish --log "check: <n> findings applied"`,
counting both kinds. A check with nothing to apply still runs
`finish`, which releases the lock and advances the cycle. A target
with no conforming header runs the adoption pass and stops.

## list

`hq list [n]` prints the open threads, newest first, `n` capping the
lines; `hq list --done` lists the threads marked done instead. It
writes nothing. Show the user the lines unchanged; `hq list --help`
names the columns.

## done

1. Resolve `<slug>` per the shared rule. With none given, list the
   candidates and stop - never pick the newest. Ending the wrong
   thread is not a mistake a later cycle corrects.
2. Run `hq done <slug>`, with `--reason "<line>"` when the session
   knows why the thread is finished; whitespace collapses to single
   spaces.
3. Show the user the output unchanged.

`done` ends the thread; `finish` ends one cycle and leaves it running.
It writes the `.hq.done` marker in the folder and deletes nothing:
`list` hides it, `begin` and `adopt` refuse it and name the undo,
every other read verb still answers. `--force` marks a folder whose
cycle is still open, which otherwise refuses and names `hq finish`.
The marker's fields, `--undo`, and a repeated `done` are in
`hq done --help`. A directory named `.hq.done` refuses `--undo`:
remove it by hand; no verb clears it.

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
this file. `hq help anchors`: every `--where` form. `hq help kinds`:
kinds, tiers, ledger fields, flag values. `hq help rules`: R1 to W2
in full. `hq help stale-path`: fixing a folder path under a former
directory. `hq <verb> --help`: each verb's columns.
