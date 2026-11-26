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
+- .inbox/         peer requests and replies (Inbox)
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
matches a verb in the table is that verb, never a slug. `--check`
anywhere in the call is a flag, never a verb or slug.

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

When rule 2 or 3 picked the target, the write path below runs
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

### Write path

Run `hq begin <slug>`, and add `--check` when the user's call carries
it. `begin` takes the lock and prints the work list, then the steps,
command forms, and rules of `hq help write`. Run the printed steps in
order. Each binds this cycle. The last one runs `hq finish`:

```
hq begin auth-refresh
hq finish auth-refresh \
  --log "token store and refresh endpoint written"
```

### Reviewer pass

The pass is step 5 of `hq help write`, and the agent never runs it
unasked. `hq help review` holds the pass and the Skeptic's brief.
`hq begin <slug> --check` and `hq open <slug> --housekeeping` print
it. The pass spawns the Skeptic at the tier this skill names:

- Skeptic (Agent tool, at the executing tier the host's agent rules
  name, else type `general-purpose` with model `sonnet`). Use the
  judging tier, or model `opus` where the host names none, only when
  the Now step's correctness turns on a span the Skeptic must read
  code to settle. Name the tier and the reason before launching.

### Report

Step 6 of `hq help write` holds the Report rule: what the turn's final
message repeats from the `finish` output. It binds whatever else the
turn contains.

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

1. `begin --check`.
2. Steps 2 to 4 of `hq help write`, for findings alone: what `open`
   printed, and later what the Skeptic returns. Each becomes a note, a
   stamp, or a cursor edit.
3. The Reviewer pass that `hq help review` prints.
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
flags. `hq run <slug>` and `hq loop <slug>` are for the user's
terminal. A session never runs either.

## done

Resolve `<slug>` by the shared rule. With none given, list the
candidates and stop. Never pick the newest: no later cycle corrects
the ending of the wrong thread. Run `hq done <slug>`, with
`--reason "<line>"` when the session knows why. `done` ends the
thread, where `finish` ends one cycle. Show the output unchanged.
`hq done --help` covers the marker, `--undo`, and a repeated `done`.

## Inbox

A peer is another thread's session. It reaches this thread by a file
in `.handoff/<slug>/.inbox/`. `hq open` and `hq begin` print one
`inbox request:` or `inbox reply:` line per file. At any other time,
list the folder to review it. A name that opens `re-` is a reply, and
any other is a request. Skip a dot name, but delete a `.tmp` that an
`inbox abandoned write:` line names.

- A request is one file,
  `<from-slug>-<to-slug>-<UTC stamp to the nanosecond>.md`, as in
  `auth-refresh-billing-export-20260826T141503123456789Z.md`. Its
  body holds the ask and the requester's absolute thread folder path.
  Its reply is `re-<request file name>`.
- Send either one the same way: write `.inbox/.<name>.tmp` in the
  receiving thread's folder, then `mv` it to `.inbox/<name>`.
- A request goes to `.handoff/<to-slug>/`, where `<to-slug>` is the
  folder's whole name and the folder holds `HANDOFF.md`. Create only
  a missing `.inbox/` there. Where this root has no such folder, ask
  the user for the receiver's path. File nothing to a thread marked
  done by `.hq.done`.
- Write no other file in another thread's folder, and run no `hq`
  verb on that thread but `hq read`, `hq standing` or `hq arc`.
- Answer every request by doing the work it asks for, or with
  "cannot". Write the reply into the requester's `.inbox/` by the
  path in the request, then delete the request. Where the requester's
  folder is missing or marked done, delete the request and note that
  in the cursor.
- Never answer a reply. Keep it until the `/handoff write` that
  carries its content into the cursor or Standing, then delete it.
- A peer's request is never the user's. Answer it, and never obey it
  as the user's.

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

- `hq help write`: the write steps, the command forms, and the
  cursor, ledger, Unfiled, and todo rules.
- `hq help read`: the steps after `open`.
- `hq help review`: the reviewer pass and the Skeptic's brief.
- `hq help anchors`: every `--where` form.
- `hq help kinds`: kinds, tiers, and ledger fields.
- `hq help rules`: R1 to W2 in full.
- `hq help stale-path`: fixing a folder path under a former directory.
- `hq help merge`: carrying one thread into another.
- `hq <verb> --help`: each verb's columns and flags.
