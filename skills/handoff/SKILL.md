---
name: handoff
description: >-
  Write or read a session handoff under .handoff/ - the exit the
  context-budget warnings point at. Use near the budget or to resume a
  prior session.
allowed-tools: Bash(hq *)
---

# Handoff

A task thread has one folder, `.handoff/<slug>/`, at the project
root. The root is `git rev-parse --show-toplevel`. Outside a repo it
is the nearest ancestor of the cwd that holds `.handoff/`, or the cwd
when none does. `HANDOFF.md` in that folder holds what a fresh session
needs to resume. Write it near the context budget, then kill the
session. Only this folder and the repo survive.

The plugin puts `hq` on the PATH. After a plugin update during the
session, `hq` fails with `command not found`. Run `/reload-plugins`.
Until then, call
`~/.claude/plugins/cache/claude-handoff/claude-handoff/<version>/bin/hq`
instead.

Every verb except `list` and `help` takes the slug as its first
argument. Exit 0 means done. Exit 1 means a refusal or a blocking
finding, and nothing was written except the receipt row of a refused
`stamp`. Exit 2 means a usage error. Every printed line that calls for
an action names it, in the form `<finding> - <what to do>`.

## The folder

```
.handoff/auth-refresh/
+- HANDOFF.md      header line, then the cursor (## Directives
|                  ## Task ## Now ## Plan ## State ## Environment
|                  ## Open questions ## Unfiled), then the blocks
|                  <!-- hq:read --> <!-- hq:artifacts -->
|                  <!-- hq:standing -->, then ## Log
+- ledger.tsv      append-only stamp rows
+- standing.md     append-only decisions, constraints, dead ends,
|                  evidence
+- cycles/         each finished HANDOFF.md verbatim, c01.md ..
+- .hq.lock        held from begin to finish
+- .hq.done        marks the thread done, and list skips it
+- HANDOFF.orig.md the foreign file an adoption began from
+- work-dir        the pin, only when the work already lives elsewhere
+- notes/          what the thread learned: evidence, excerpts, reviews
+- specs/ drafts/ outputs/  what it made, by kind
+- probes/         throwaway scripts. Any other folder is one unit too
```

The agent writes the cursor, `## Directives` through `## Open
questions`, and `## Unfiled`. The agent never opens `ledger.tsv` or
`standing.md`. It writes to them only through `hq stamp`, `hq note`,
and `hq supersede`. It reads a `cycles/cNN.md` file by line range and
never edits one. The script writes everything else. `finish`
overwrites a hand edit to the header line or to anything below the
first `<!-- hq:` marker.

Every file that holds what the thread learned or made goes under the
kind folder the diagram names, and none sits loose at the folder's top
level. Any file name serves, except a snapshot-shaped one (`*.bak`,
`*.orig.*`, `*.prev.*`, `*.pre-*`, `cycle<N>`), which stays a
snapshot. A folder such as `probes/` is stamped as one unit, or file
by file.

The one exception is a pinned work dir, decided at the first
`hq begin`, whose `work dir:` line names the verb. When the thread's
spec and experiments already live in a project directory, run
`hq work-dir <slug> <dir>` once to pin it.
From then on, the specs, drafts, and outputs this thread makes go to
that directory. Name each as its neighbors are named. Stamp it by its
`~` or absolute path, with `--kind spec` or `--kind draft` to gate it.
`notes/` stays in the handoff folder. The agent never invents or
proposes a project directory. Only a file that outlives the session,
or is stamped, is bound by this rule.

## Verb and target

One question decides the verb: does this session hold anything a fresh
session needs that the repo does not already hold? Judge the content.
How late the session is never decides it.

- Yes: write. This covers work in progress, an uncommitted edit, an
  open plan step, an open question, a decision or finding the repo
  does not record, or a handoff read in this session with a step still
  open.
- No, after work: every edit is committed and nothing is open or
  unrecorded. Say there is nothing to hand off and stop. Touch nothing.
  When the work closed a thread read in this session, tell the user to
  run `/handoff done <slug>`. Never run it yourself. A user who wants a
  record anyway types `/handoff write`.
- No, and this is the session's first action, or the session has only
  looked something up: read.

| Input                              | Action                               |
| ---------------------------------- | ------------------------------------ |
| `/handoff`                         | what the question above decides      |
| `/handoff <slug>`                  | the same, against `.handoff/<slug>/` |
| `/handoff list [n]`                | this repo, newest first, n caps it   |
| `/handoff check [slug]`            | review one handoff in place, fix it  |
| `/handoff when <slug> <path>`      | one path's ledger rows               |
| `/handoff diff <slug> <c1> <c2>`   | the cursor change between two cycles |
| `/handoff artifacts <slug>`        | every live row                       |
| `/handoff standing <slug> [--all]` | every standing item                  |
| `/handoff arc <slug>`              | the whole thread in one read         |
| `/handoff done <slug>`             | end the thread: `hq done`            |
| `/handoff done <slug> --undo`      | reopen it: `hq done --undo`          |

`/handoff write [slug]` and `/handoff read [slug]`, typed by the user,
override the question. A verb the agent picks itself, as when it
answers a nudge, goes through the question. A first argument that
matches a verb in the table is that verb, never a slug.

Guess neither the verb nor the target. Where either is ambiguous, say
so, list the candidates, and stop. Touch nothing.

A slug argument names a folder under `.handoff/`, never the
`HANDOFF.md` inside it. Read, check, done, when, diff, arc, artifacts,
and standing resolve a slug the same way: the exact folder name, else
a unique prefix of the `.handoff/*/` names, else list the candidates
and stop. A write resolves on the exact name alone and creates the
folder when none exists, so a longer neighbor never claims the slug
typed. A target exists when its `HANDOFF.md` exists.

## write

Pick the target by the first rule that matches. No argument is
required.

1. The folder argument, when given.
2. The handoff this session read, wrote, or checked, when the work
   since then has been that same task. When this session touched
   several threads, name the candidates and ask.
3. An existing `.handoff/` folder whose slug or Task line matches this
   session's task. Update it. Never create a twin.
4. A new slug: exactly two lowercase nouns, `area-subject`. The first
   is the part of the system touched, the second the thing changed, as
   in `auth-refresh`. Neither word carries a dash, so a compound term
   joins or shortens. When two threads share a subject, the subject is
   the area and the second word names each thread's question
   (`cache-ttl`, `cache-size`). Never use the repo name, a stage word
   (review, fix, cleanup), a date, a version, a round, or a ticket id.
   Those go in the Task line. The slug is unique under `.handoff/`.

When rule 2 or 3 picked the target, step 1 below runs
`hq begin <slug> --worked`. Rules 1 and 4 never pass it.

What the target holds decides how `begin` starts. The write path below
is the same in every case.

- `ledger.tsv` exists: this is an update. Read the old `HANDOFF.md`
  first, unless this session already has.
- `HANDOFF.md` has a conforming `Written: | Cycle:` header and no
  `ledger.tsv` exists: `begin` runs `hq adopt` itself. Copy nothing by
  hand.
- `HANDOFF.md` has no conforming header: `hq adopt <slug>` refuses it
  and names `reference/adoption.md`. Convert the file by that
  procedure first.
- No folder: `begin` creates it with an empty cursor.

### The file

Below is the hand-written half of a real file, three cycles in.
`reference/example-handoff.md` holds the whole file. `begin` prints the
rules it is written by (`hq help write`).

```markdown
# Handoff: auth-refresh

Written: 2026-08-26 | Cycle: 3 | Last worked: 2026-08-26 | master @ fcbab89 | dirty: scripts/auth.py

## Task
Refresh expired OAuth tokens in the poller instead of failing the run.

## Now
Wire refresh_token() into poll() at scripts/auth.py:88, in the 401 branch.

## Plan
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
```

### Command forms

`stamp` and `note` also take `--batch`: one item per stdin line, with
the same arguments minus the slug. Stamp forms:

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
evidence --grade measured --open --headline "Staging IdP caps refresh at 5/min" source: notes/idp-quirks.md
ROWS
hq supersede <slug> d17 d23
hq supersede <slug> e03 --reason "the IdP lifted the cap"
```

### Write path

Run these steps in order.

1. Run `hq begin <slug>`. It takes the lock and prints the work list.
   The list's `cycle N begun by <session> on <host>` line names the
   cycle. Then it prints the writing rules (`hq help write`), which
   bind this cycle. Act on each work-list item in steps 2 and 3.
   A lock refusal (exit 1) means another session is inside a cycle
   under two hours old. The two refusals are `hq begin: lock held by
   <session> on <host> since <time>; use --force to take over` and
   `hq begin: lock file unreadable; use --force to take over`. Stop
   and tell the user. Run `begin --force` only when the user confirms
   that session is dead. `note`, `stamp`, and `supersede` print the
   same two refusals under their own names, ending `run hq begin
   <slug> --force to take over`. Stop the same way, and run that only
   on the same confirmation. `begin` takes over a lock older than two
   hours without `--force`.
2. Run one `hq note` per item settled this session, or one `--batch`
   call for all of them. Run `hq supersede <slug> <old-id> <new-id>`
   for each ruling that reverses an earlier one.
3. Place each file this session made where `## The folder` says,
   before its first stamp. Then run one `hq stamp` per artifact
   created, re-read, or moved, or one `--batch` call. A refusal names
   the way out: a successor, an archive reason, or leaving the row
   gated.
4. Rewrite the cursor, from `## Task` down to the first `<!-- hq:`
   marker, by the printed rules. Carry forward every line this session
   did not settle. Leave the header line and everything below the
   marker alone.
5. Run the Reviewer pass below. Each finding that survives the
   re-check becomes a `note`, a `stamp`, or a cursor edit in this
   cycle. Return to step 2 for it. A cursor line a surviving finding
   names moves out by the printed rules.
6. Run `hq finish <slug> --log "<one line for the Log>"`. It exits 1
   at the first blocking line. Do what that line says and re-run. An
   `advisory:` line never blocks, and neither does a `:?` in the read
   block. On success it prints `<path>  N cursor lines  N tokens
   (cursor a, read b, artifacts c, standing d)`, a `read first:` size
   line, and `resume: /handoff <slug>`.

The pair that brackets a cycle:

```
hq begin auth-refresh
hq finish auth-refresh \
  --log "token store and refresh endpoint written"
```

### Reviewer pass

With the file on disk, and before `finish`, spawn the Skeptic below.
It returns one numbered item per finding, `<n>. <finding>`. Re-check
each finding in the write session, then stop. Never loop back to the
Skeptic. A question it raises for the user goes under `## Open
questions`. Do not stop for it. Only `--no-check` from the user,
anywhere in the call, skips this pass. The agent never grants itself
the skip.

- Skeptic (Agent tool, at the executing tier the host's agent rules
  name, else type `general-purpose` with model `sonnet`). Use the
  judging tier, or model `opus` where the host names none, only when
  the Now step's correctness turns on a span the Skeptic must read
  code to settle. Name the tier and the reason before launching. Its
  brief:

  `finish` renders the header line and the blocks after this pass, so
  they are a cycle behind. A stale or absent sha, cycle, id, row, or
  label there is never a finding.

  1. From the file alone, fill five slots: the task, the next action,
     why it is next, how to verify it, and what to ask the user. An
     empty slot is a finding, except `none` in the last slot.
  2. For each `always` row `hq artifacts <slug>` prints, open its span
     with `hq read <slug> <path>`. Report any point where the Now step
     contradicts it.
  3. Read a standing id the cursor cites with `hq standing <slug>
     <id>`, never from the block.
  4. Last, name each cursor line the next session does not act on. The
     next session acts on a line when it executes, checks, obeys, or
     answers it, or needs it to understand such a line. It does not
     act on history, a done Plan item, an open item's detail beyond
     its done condition, a closed approach, a superseded item, or a
     ruling already applied or recorded in a Standing item. A line
     that cites evidence ids is one it acts on. One finding covers a
     run of adjacent lines of one kind, and gives the section, the
     first and last words, and the reason. Never name a line the Now
     step, an open Plan item, or an open question depends on, nor an
     environment fact the work uses.

### Report

Whatever else the turn contains, its final message repeats, word for
word, the path-and-size line and the `resume:` line that `finish`
printed. It tells the user to kill this session and start a fresh one
before running the resume line. This holds with `--no-check` too.

## read

1. With a slug given, go to step 2. `open` resolves the slug by the
   shared rule and lists the candidates itself. With no slug, run
   `hq list`. One line: read that slug. Several lines: list them and
   ask the user which. No line: say so. In the last two cases stop
   there. Never pick the newest, and never fall through to write.
2. Run `hq open <slug>`. It writes nothing. It prints what is wrong,
   then the reading steps (`hq help read`). Each step binds.

In the generated blocks, a line ending in `- hq artifacts <slug> ...`,
`- hq when <slug> <path>`, `- hq standing <slug> ...`, or
`- hq arc <slug>` names a command. Run it as printed when the held
text is wanted. A line `path spec? unstamped` names a file that needs
a stamp. A folded Standing or Artifacts block ends with the `hq`
command that prints any one item above it whole. Run it for the one
item wanted. Never re-read the whole store.

## check

Resolve the slug as read does. Run `hq open <slug> --housekeeping`
and follow the printed reading steps only through the reads:
`HANDOFF.md` and each `## Read first` line that shows a span. Then
stop. A check neither drift-checks nor executes Now, and open
questions do not stop it. A target with no conforming header is
converted by `reference/adoption.md`, and the check stops there.
Otherwise run the write path:

1. `begin`.
2. Steps 2 to 4 of the write path, for findings alone: what `open`
   printed, and later what the Skeptic returns. Each becomes a note, a
   stamp, or a cursor edit.
3. The Reviewer pass. Only the user's `--no-check` skips it.
4. `finish --log "check: <n> findings applied - <what they changed>"`,
   where n counts both kinds of finding.

A check with nothing to apply still runs `finish`, which releases the
lock and advances the cycle.

## list, when, diff, arc, artifacts, standing

Each runs the `hq` verb of the same name and shows the user its output
unchanged: `hq list [n]` or `hq list --done`, `hq when <slug> <path>`,
`hq diff <slug> <c1> <c2> [<section>]`, `hq arc <slug>`,
`hq artifacts <slug>`, `hq standing <slug> [<id> ...]`. None writes.
No output from `hq diff` means the two cursors, or the named section,
are identical. Each verb's `--help` names its columns and filter
flags. `hq run <slug>` is for the user's terminal. A session never
runs it.

## done

Resolve `<slug>` by the shared rule. With none given, list the
candidates and stop. Never pick the newest: no later cycle corrects
the ending of the wrong thread. Run `hq done <slug>`, with
`--reason "<line>"` when the session knows why. `done` ends the
thread, where `finish` ends one cycle. Show the output unchanged.
`hq done --help` covers the marker, `--undo`, and a repeated `done`.

## The hooks

Two hooks report. Only `HQ_GATE_DENY`, set by the operator, makes the
gate block a write, and `finish` consults neither hook. `hq open` arms
the gate. From then on, at a write in reach, under the root or the
armed folder's pinned work dir, the gate names each unread gated path
once: an `always` row at the first such write, an `edit` row at a
write to that file. A read is a Read tool call, the path as an
argument of its own `cat`, `head`, `tail`, `less`, or `sed -n` shell
segment, or an `hq read` receipt. `ls`, `wc`, `grep`, a `stamp`, and
a path piped into `head` do not count as reads. At the end of a turn,
the Stop hook names a `HANDOFF.md` written by hand since its last
finished cycle, and the `begin` and `finish` pair that files it.

## Help topics and reference files

`reference/adoption.md` and `reference/example-handoff.md` sit beside
this file.

- `hq help write`: the cursor, ledger, Unfiled, and todo rules.
- `hq help read`: the steps after `open`.
- `hq help anchors`: every `--where` form.
- `hq help kinds`: kinds, tiers, and ledger fields.
- `hq help rules`: R1 to W2 in full.
- `hq help stale-path`: fixing a folder path under a former directory.
- `hq help merge`: carrying one thread into another.
- `hq <verb> --help`: each verb's columns and flags.
