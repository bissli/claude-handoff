# Adoption: converting a foreign HANDOFF.md

A target whose `HANDOFF.md` has no conforming `Written: | Cycle:`
header was written by some other process. `hq adopt <slug>` refuses
it and names this file. Converge the form, destroy no content, in
this order.

## 1. Run `hq adopt <slug>`

On a file with no conforming header it changes nothing and prints
`hq adopt: non-conforming header; write a conforming HANDOFF.md
first` and the heading inventory (exit 1) - the map for step 3.

## 2. Keep the original

`cp -n .handoff/<slug>/HANDOFF.md .handoff/<slug>/HANDOFF.orig.md`,
before any other write; skip it when the copy already exists. That
copy is never overwritten or deleted; the walk stamps it as a
`snapshot` row. A `HANDOFF.prev.md` needs no copy: `adopt` archives
it to `cycles/c<N-1>.md` itself.

## 3. Rewrite HANDOFF.md by hand

`# Handoff: <slug>`, a blank line, `Written: <today> | Cycle: 1`,
then the sections.

Five foreign sections `adopt` reads itself. Keep each under its own
heading, wording unchanged:

- `## Key files` - bullets under a `Read now:` line and a
  `Reference only:` line, with a path first on each bullet and its
  text on the same line or an indented line below.
  - A clause after the label is fine (`Read now, under x/ unless
    noted:`), wrapped over several lines or not.
  - `Read now:` seeds `always`; `Reference only:` seeds `edit` on a
    notes file and leaves any other at `never`; a spec or draft stays
    `always` under either.
  - A label grades every bullet below it until the next label. A
    line ending in `:` that is neither label ends the group and opens
    an ungraded one, whose pointers keep their seed: `never`, or
    `always` for a spec or draft.
  - Every pointer names a real path: `adopt` resolves no shorthand
    such as an alias declared in prose. A pointer it cannot find on
    disk seeds a `missing` row and prints `Key files pointer not on
    disk: <path>`.
  - A pointer may name lines after a colon, `mod.py:96-115,157`; the
    ledger stores the bare path with the line numbers leading the
    label.
  - Several paths on one bullet, separated by a comma or `and`, each
    take a row sharing the bullet's text.
  - Two bullets naming one path merge into one row, labels joined
    with `; ` (or a space after a label that ends a sentence) and
    anchors with `;`.
  - A bullet whose first token is not a path lands whole under
    `## Unfiled`, its indented lines joined as written. An unindented
    run of lines with no path first joins into one bullet that a
    blank line ends, as the lines between the header and the first
    heading do.
- `## Decisions`, `## Constraints`, `## Dead ends` - moved whole into
  `standing.md`, one item per bullet or unindented line. A sentence
  above the first bullet of a bulleted section introduces the
  bullets, is no item, and lands under `## Unfiled`.
- `## Log` - carried in the manifest row (below).

Everything else:

- A `Written:` header wrapped over several lines - the lines under it
  up to the next blank line or heading - lands as bullets under
  `## Environment`, to keep or prune there; `finish` rewrites the
  header from the git state.
- Map every other foreign section to the cursor section carrying the
  same kind of fact - Task, Now, Plan, State, Environment, Open
  questions - and keep every fact.
- A directive the file quotes - a reading order, a backup or worktree
  it says never to delete - becomes a `## Constraints` entry.
- What fits nowhere moves whole to a sibling `notes/<topic>.md`,
  stamped `--read-before edit` when the cursor points at it. Touch no
  sibling file except to add.

## 4. Continue at Write path step 1

`begin` sees the conforming header with no `ledger.tsv`, runs
`adopt`, prints `hq begin: ran adopt on existing HANDOFF.md`, and
opens cycle 2; the hand-written file is cycle 1, archived as
`cycles/c01.md`. `adopt` infers kinds, seeds the read obligations,
and prints what still needs judgment. Each line and its move:

- `label: <text>` - one per seeded label; settle each with a `stamp`
  in write step 3.
- `read_before=<x>: <n>` - counts by grade.
- `gated (n): <names>` - the rows the read block will carry.
- `where dropped: <path> '<anchor>'` - an `s<n>` or `section <n>` the
  label cites names no heading of that file. `s1` in prose most often
  means another file's step; where it was meant, re-stamp with a
  `--where` that resolves (`hq help anchors`).
- `Key files pointer not on disk: <path>` - stamp the path that
  exists and archive this row.
- `skipped conflicted copy: <name>` - a sync duplicate; resolve it by
  hand.
- `unstampable name: <name>` - a tab or newline in the name, or a
  socket or FIFO; rename the file before stamping it.
- `advisory: R1 ...` - a seeded row R1 would refuse; `hq help rules`
  has the rule.
- `unfiled: <n> bullets to rehome` - the `- unfiled: <text>` bullets
  the rewrite left under `## Unfiled`. Each blocks `finish` until it
  is typed `decision:`, `constraint:`, or `dead-end:`, moved into a
  cursor section, or rehomed to a sibling.
- `conservation: every original line carried`, or `conservation: N
  original lines not carried` with one `not carried: <line>` line
  under it per missing line, each printed whole - text of the file
  `adopt` read that reached neither the cursor, nor `standing.md`,
  nor a label, nor the manifest row's `note`.
- Where `HANDOFF.orig.md` exists, a second line measures the hand
  rewrite against it, counting a line rehomed into a live notes
  sibling or any row graded `edit` as carried: `conservation vs
  HANDOFF.orig.md: N lines not carried` (or `conservation vs
  HANDOFF.orig.md: every line carried`), with the same
  `not carried:` lines under it.

Rehome each not-carried line - a typed `## Unfiled` bullet, a cursor
section, or a stamped sibling - and settle each label with a `stamp`.

## Other adopt lines

- `hq adopt: already adopted; ledger.tsv exists` and `hq adopt: no
  HANDOFF.md in <folder>` (exit 1 each).
- `hq adopt: seeded N entries in <slug>` on success - the ledger's
  row count: every walk entry but `HANDOFF.md`, `ledger.tsv`,
  `standing.md`, `cycles/`, `.hq.lock`, and dotfiles, plus every Key
  files pointer the walk cannot see.

## The adopt manifest row and Log line

An adopted folder's first Log line reads `adopted`, or `adopted;
prior Log: N lines in cycles/cNN.md` when the file had a Log. The
archived file holds those lines, and the manifest row's `note` field
carries them, each wrapped item joined back onto one line, separated
by ` | `.

- The note opens with the adopt-time provenance, `adopted <date> by
  <session>` plus ` at <branch>@<sha>` under git, then the wrapped
  `Written:` header's lines as one `header: <text>` item.
- The row's `written`, `repos`, `cursor_lines`, `payload_tokens`, and
  `handoff_sha` describe `cycles/cNN.md`: its `Written:` date, its
  `branch @ sha`, the lines from its first `## ` heading to `## Log`
  or the first `<!-- hq:` marker (the slice `hq diff` compares), its
  length over four, and its digest. The Log line for that cycle
  carries the same date and sha, so it names the archived write, not
  the header of the file that renders it.
- `session` is `-`; the ledger and standing columns measure those
  files as adopt left them; `rewrite_sha` names the HANDOFF.md adopt
  wrote.
