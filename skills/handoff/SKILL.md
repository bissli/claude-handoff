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
`HANDOFF.md` carries what a fresh session needs to resume and nothing
the repo already records. Write near the budget, then kill the session:
a total clear, in which only this folder and the repo survive. Run
`/handoff <slug>` in a fresh one. A later bare `/handoff` updates the
same file.

The plugin puts `hq` on the agent's PATH. After a plugin update
mid-session `hq` fails with `command not found`: run `/reload-plugins`,
or until then call
`~/.claude/plugins/cache/claude-handoff/claude-handoff/<version>/bin/hq`.
Every verb but `list` and `help` takes the slug first. Exit 0 is
done; 1 is a refusal or a blocking finding, and nothing is written
except that a refused `stamp` appends its receipt row; 2 is a usage
error.
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

Everything the thread learned or made lives in the folder, never
loose at its top level. What it learned - a finding, its evidence, a
review - goes under `notes/` or a folder of the work's own. What it
made goes under `specs/`, `drafts/`, or `outputs/`: the folder sets the
kind and any file name serves but a snapshot-shaped one (`*.bak`,
`*.orig.*`, `*.prev.*`, `*.pre-*`, `cycle<N>`), which stays a snapshot;
`probes/` or a name the work calls for is stamped as one unit or file
by file. One exception, judged at the first `hq begin`, whose work list
ends in `work dir:` and the verb: when the thread's spec and
experiments already live in a project directory, `hq work-dir <slug>
<dir>` pins it once - a directory already on disk, inside the repo or
out, never the root, `.handoff/`, or the system temp directory unless
the repo itself sits under it. From then on the specs, drafts, and
outputs this thread makes go there, each named as its neighbors are and
stamped by its `~` or absolute path, `--kind spec` or `--kind draft` to
gate it, else `--read-before mention` when the cursor points at it so
its label prints; `notes/` stays in the folder. The pin is the thread's
own; `hq work-dir <slug>` prints the ruling, `--clear` undoes it. The
agent invents no project directory and proposes none. Only a file that
outlives the session or is stamped is bound.

## Which verb, which target

The verb is inferred, never typed. One question settles it: does this
session hold anything worth telling a fresh session?

- Yes - a file edited, a decision settled, a finding the repo does
  not already record, a handoff read here. Write.
- No - none of that. A session whose opening move this is, and
  equally one that has only looked something up. Read.

Ask it of the content, never the position: a session that answered one
question and edited nothing holds nothing worth passing on, however
many messages came first. Write is the last move of a working session,
read is the first move of the session that replaces it, and there is
no third case.

An argument is always a folder under `.handoff/`; the document inside
is always `HANDOFF.md`, never named by the caller.

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

`write` and `read` as the first word override the inference, an
optional slug after each; `--no-check` anywhere skips the reviewer
pass. A first argument matching a verb above is that verb, not a slug.

Guess neither the verb nor the target. Where either is ambiguous,
say so, list the candidates, and stop - touch nothing.

A folder argument resolves the same way everywhere, `hq` included:
exact folder name, else a unique prefix of the `.handoff/*/` names,
else list the candidates and stop (write: create the folder). A target
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

What the target holds decides the route; the write path below is the
same in every case:

- `ledger.tsv` exists: update. Read the old `HANDOFF.md` first if this
  session has not.
- `HANDOFF.md` with a conforming `Written: | Cycle:` header and no
  `ledger.tsv`: `begin` runs `adopt` itself. No copy by hand.
- `HANDOFF.md` with no conforming header: `hq adopt <slug>` refuses it
  and names `reference/adoption.md` beside this file; convert the file
  by that procedure first.
- no folder: `begin` creates it with an empty cursor.

The header line is the script's: `finish` writes it from the git
state, or without git ends it after the cycle. The agent collects no
anchors. Background tasks still running, and any second repo this
session changed (path, branch, sha), go under Environment.

### The file

Write for a reader with no memory of this session and full access to
the repo: short technical documentation in complete sentences, no
transcript narration. Task and Now are required; omit any other cursor
section that would be empty. The file is the whole bridge - a
requirement the user stated, an approval given, a quirk found is lost
unless written here. Trimming cuts what the repo records, never what
only the session knows; in doubt, write it down.

The hand-written half of a real file, three cycles in
(`reference/example-handoff.md` beside this file is the whole file):

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
  label stays in the Artifacts block instead of a count.
- Name where a credential lives, never its value.
- Absolute dates. ASCII only.
- Now is the single next action; Plan is what follows it. Plan
  carries the approved plan; neither is re-opened.
- The cursor is rewritten from `## Task` down every cycle, and every
  cursor line this session did not settle is carried forward verbatim:
  an Open question leaves only when answered, a Plan item only when
  done or rehomed.
- Anything still awaiting the user - a question, an unapproved plan -
  goes under `Open questions`; read stops there.
- An item recorded with `note` or under `## Unfiled` is not repeated
  in State: the Standing block carries it.
- No line ceiling binds the cursor. What a resuming reader does not
  need first moves whole to a sibling by the rule above - moved, never
  cut. Rewording to reach a count drops facts and barely moves it.

### The artifact ledger

Rows are dictated through `hq stamp`, `hq note`, and `hq supersede`;
`stamp` and `note` also take `--batch`, one row per stdin line, the
same arguments minus the slug.

`read_before` is a tier, and `stamp` seeds it from the kind (`hq help
kinds` has the table): a spec seeds `always`, a draft `edit`, all else
`never`; `--kind spec` or `--kind draft` gates an outside file. Of
several `SPEC*` stem-mates only the newest is gated.

| tier | read | when |
| --- | --- | --- |
| `always` | the contract | every resume |
| `edit` | before you change it | the gate, at a write to it |
| `mention` | on demand | `hq read` |
| `never` | on the record | `hq artifacts`, `hq when` |

`always` requires an anchor: a stamp that would leave a live `always`
row with no `--where` is refused and the file's headings print under
the refusal; a spec needed whole is anchored at its title heading. A
whole file is never eager: a note or a source file the cursor points
at is `edit` or `mention`.

Rules the script enforces (`hq help rules` has them in full):

- R1 A spec row keeps `always` and a draft row `edit` or `always` -
  inferred, stored, or by `--kind`; both keep `live` and their kind,
  unless `--successor` names a live file on disk other than itself, or
  `--archive --reason` is given. A path that has ever been spec or
  draft stays gated: its way back to `live` is that tier.
- R2 A refused stamp still appends a receipt row, `reason` set to
  `refused: <why>`; the attempt is in the record and clears nothing.
- R3 A live row graded always or edit whose file sha moved blocks
  `finish` until re-stamped; a re-stamp alone clears it.
- W1, W2 An edited recorded line in `ledger.tsv` or `standing.md` is a
  hard fail in `finish`; when a known tool caused it (a formatter, a
  merge), pass `--acknowledge "<reason>"` to `finish`.

Stamp forms, all real:

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

- A path is relative to the folder; a path outside it - a repo file, a
  `~` or absolute path - is stored whole and gated the same way. There
  is no search of the working directory.
- `--kind`, `--read-before`, `--status`, `--where`, and `--label` each
  default to the previous row's value; omit them on a re-stamp.
- `--where` names a heading: its text without its number (`3. Retry`
  or `Retry`), or `s<n>` for the heading numbered `<n>`; several join
  with `;`. `stamp` accepts a wrong anchor, which shows only as
  `SPEC.md:?` in the read block - check the heading first.
- `--successor P` marks the row superseded, `--archive --reason`
  archived, `--defer` deferred until the next work list.

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
decisions, the Now step. Neither restates the other.

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

1. `hq begin <slug>` takes the lock and prints the work list, each
   line naming its move, then `cycle N begun by <session> on <host>`.
   Make each move in steps 2 and 3. `hq begin: lock held by <session>
   on <host> since <time>; use --force to take over` and `hq begin:
   lock file unreadable; use --force to take over` (exit 1) mean
   another session is inside a cycle less than two hours old: stop and
   say so; `begin --force` only when the user confirms that session is
   dead. An older lock is taken over without `--force`.
2. One `hq note` per item settled this session, or one `--batch`; one
   `hq supersede <slug> <old-id> <new-id>` when a ruling reverses an
   earlier one.
3. Place each file this session made by `## The folder` before its first
   stamp; then one `hq stamp` per artifact created, re-read, or moved,
   or one `--batch`. A refusal (exit 1) leaves the row as it was and
   names the way out: a successor, an archive reason, or leave the row
   gated - unless its file is missing, which blocks `finish` until the
   row is re-pointed, superseded, or archived.
4. Rewrite the cursor from `## Task` down, above the first `<!-- hq:`
   marker, carrying forward every line this session did not settle;
   leave the header line and everything below the marker alone.
   Anything settled with no `note` call goes under `## Unfiled`.
5. Run the Reviewer pass (below): the skeptic seat. Each surviving
   finding becomes a `note`, a `stamp`, or a cursor edit in this
   cycle; return to step 2 for it, then continue.
6. `hq finish <slug> --log "<one line for the Log>"`. It exits 1
   having written nothing on the first blocking line, which names its
   move; make it and re-run. `advisory:` lines never block; each names
   what to check. Then it drains Unfiled, renders the blocks, writes
   the header and Log, archives the file to `cycles/cNN.md`, releases
   the lock, and prints `<path>  N cursor lines  N tokens (cursor a,
   read b, artifacts c, standing d)`, a `read first:` size line, and
   `resume: /handoff <slug>`. A
   `<path>:?` in the rendered read block does not block, but the anchor
   is unresolved: re-stamp with a `--where` that resolves.

One whole cycle on the example thread:

```
hq begin auth-token-refresh
hq stamp auth-token-refresh specs/SPEC.md \
  --where "3. Retry" --label "refresh contract; s3 is the retry schedule"
hq finish auth-token-refresh \
  --log "token store and refresh endpoint written"
```

### Reviewer pass

With the file on disk, spawn the skeptic below before `finish`. It
returns one numbered item per finding, `<n>. <finding>`; re-check
each in the write session, apply what survives, and stop - never
loop. A question it raises for the user goes under `## Open
questions`; do not stop for it.

- Skeptic (always; Agent tool, at the reviewer tier the host's own
  agent rules name; a host with no such rules takes type
  `general-purpose`, model `sonnet`): from the file alone, fill five
  slots - the task, the next action, why it is next, how to verify it,
  what to ask the user; an empty slot is a finding. For each line of
  the read block, open the resolved span and report any point where
  the Now step contradicts it. For each todo file the Plan points at,
  verify that the Now step agrees with the corresponding live item in
  that file.

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
   wrong. Each line names its move and none stops the read: a
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
   present: put them to the user and stop. Otherwise state the task
   and the Now step in two sentences, then execute Now; the Plan
   follows.
6. A later bare `/handoff` targets this handoff - write rule 2.

In the generated blocks, `Read first` has one line per live `always`
row with the size of what `hq read` prints:
`.handoff/<slug>/specs/SPEC.md:11-13  (320 tok)` is where its anchor
resolves today, `(N lines, T tok)` a whole file an older cycle left,
`:?` an anchor matching no heading - read it `--whole`, re-stamp
with a `--where` that resolves, re-run `hq open`. `Artifacts` prints
rows graded always, edit, or mention in full and `path spec?
unstamped` for a file with no row; `never` rows collapse to counts,
and a counted line ends in the command that expands it -
`- hq artifacts <slug>`, `- hq when <slug> <path>`,
`- hq standing <slug>` - run it. Every path resolves from the base
the first line names; the folder's own files carry
`.handoff/<slug>/`. `Standing` ids are `d`
decision, `c` constraint, `x` dead end, a decision or dead end by its
headline; `hq standing <slug> <id>` prints the body. In `Log`, `+1`
counts dirty paths.

## check

Resolve like read, then read steps 2 and 3; open questions do not stop
a check. Then run the write path: `begin`; steps 2 to 4 only for
findings - what `open` printed and what the skeptic returns - as notes,
stamps, and cursor edits; the skeptic; `finish --log "check: <n>
findings applied"`, counting both kinds. A check with nothing to apply
still runs `finish`, which releases the lock and advances the cycle. A
target with no conforming header runs the adoption pass and stops.

## list

`hq list [n]` prints one line per folder under `.handoff/` holding a
`HANDOFF.md`, newest first, and writes nothing:
`<slug>  <Written date>  c<N>  <done>/<total>  <Task line>`; `list 5`
caps it at five. Show the user the lines unchanged. `<done>/<total>`
counts `- [x]` over the checkbox items under `## Plan`, `-` when there
are none; it tells a live thread from a finished one where the cycle
count says neither.

## when, diff, artifacts, standing

Each runs the `hq` verb of the same name and shows the user its output
unchanged; none writes. `hq when <slug> <path>`: every ledger row for
the path, oldest first; a relative path resolves against the folder,
root, then pin. `hq diff <slug> <c1> <c2>`: a line per cursor
section, `## Now  +3 -1` or `unchanged`; a section name after the
cycles expands it, `--full` all; no output means identical.
`hq artifacts <slug>`: every live row plus unstamped files.
`hq standing <slug>`: every unsuperseded item in full; `--all` adds
the superseded ones; `<id> [<id> ...]` prints the named items, a
superseded one with the current id.

## The hooks

Two hooks report and, unless the operator sets `HQ_GATE_DENY`, never
block; `finish` consults neither - R3 blocks `finish`. Once `hq open`
arms it, at a write in reach - under the root or the armed folder's
pinned work dir - the gate names each unread gated path once: an
`always` row at the first such write, an `edit` row at a write to that
file. A read is a Read tool call, the path as an argument of its own
`cat`/`head`/`tail`/`less`/`sed -n` segment, or an `hq read` receipt;
`ls`, `wc`, `grep`, a `stamp`, and a path piped into `head` are none.
At the end of a turn, the Stop hook names a `HANDOFF.md` written by
hand since its last finished cycle and the `begin`/`finish` pair that
files it.

## Adoption

`hq adopt <slug>` changes nothing, prints the heading inventory, and
names `reference/adoption.md`. Follow it, then continue at write path
step 1; `begin` runs `adopt` and opens cycle 2.

## Where the rest lives

`reference/adoption.md` and `reference/example-handoff.md` sit beside
this file. `hq help anchors`: every `--where` form. `hq help kinds`:
kinds, tiers, ledger fields, flag values. `hq help rules`: R1 to W2
in full. `hq help stale-path`: fixing a folder path under a former
directory. `hq <verb> --help`: each verb's columns.
