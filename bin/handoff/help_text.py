"""Topic texts for the help verb.
"""


HELP_TOPICS: dict[str, str] = {
    'anchors': """\
hq help anchors - the --where grammar

--where names one or more headings of the stamped file. Join several
anchors with ';'. Write a ';' that is part of a heading's text as
'\\;'. An anchor is the heading's text without its number, or s<n>
for the heading numbered <n>.

  heading                        accepted anchors
  ## s4: Field-to-path mapping   s4 | s4: Field-to-path mapping |
                                 Field-to-path mapping
  ## 4. Cache warmup             s4 | Cache warmup
  # 11b. Proof                   s11b | 11b. Proof | Proof
  #### 2a - Basis                s2a | 2a - Basis | Basis
  ## F65. Long title             F65 | f65 | sF65 | Long title
  ### 24.4 The decision          s24.4 (s24 names '## 24. Parent')

- '4.', '4:', '4 -', 's4.', and 's4:' all count as the number 4. A
  bare 'S4 ...' with nothing of the kind after it is title text, so
  only the whole text names that heading.
- A number may carry one letter when a '.', ':', or ' - ' follows
  it. s11b names '# 11b. Proof', and s11 does not. A bare '3D ...'
  or '2a-b ...' is title text.
- A dotted sub-heading takes its whole number. s24.4 names
  '### 24.4 The decision', and s24 names '## 24. Parent'.
- A title alone lands on the first heading with that text. Name a
  repeated title by its number.
- A letter-led id of one or two letters ('## F65. Title', '## Q3:
  Title') is named by that id: F65, f65, or sF65. F7 never lands on
  '## 7. Seven'. A longer word such as 'Log4j:' is title text.
- The rendered read block shows where each anchor resolves today
  and what the span costs: .handoff/<slug>/SPEC.md:11-13 (320 tok).
  A row with no anchor, which only an older cycle can leave, shows
  (N lines, T tok), and the read is the whole file. A :? in place
  of the span means the anchor matches no heading. Read the whole
  file, re-stamp with a --where that resolves, and re-run the same
  hq open.
  A ? that survives means the anchor is still wrong.
- A section dropped from --where stays reachable. The read verb
  takes --section <anchor>, in this same grammar, for an anchor the
  row does not carry, and --whole for the whole file. Both record
  the receipt the gate credits.""",
    'kinds': """\
hq help kinds - how stamp infers kind and read_before

A ledger row has thirteen fields: cycle ts path base kind status
read_before successor where sha12 lines reason label.

Kind inference, first match wins:

  name or shape                                        kind       read_before
  contains 'conflicted copy'; a tab or newline in      skip       -
    the name; not a regular file or directory
  *.pre-*, *.prev.*, *.orig.*, *.bak                   snapshot   never
  HANDOFF*.md at the folder's top level                snapshot   never
  name contains cycle<digits>                          snapshot   never
  a directory                                          probe-dir  never
  a file one level under specs/                        spec       always
  a file one level under drafts/                       draft      edit
  a file one level under notes/                        notes      never
  a file one level under outputs/                      other      never
  SPEC*, DESIGN*, PROPOSAL*, *-DECLARATION*            spec       always
  notes-*, REVIEW*                                     notes      never
  todo*, TODO*                                         todo       never
  first heading starts Spec/Design, any level          spec       always
  *.py *.sql *.js *.ts *.ps1 at the folder's top level draft      edit
  anything else, a nested or outside file included     other      never

read_before is a tier (hq help write has the table). What belongs
in each:

- always holds spec sections and anchored notes. edit holds drafts,
  code, tests, and templates. never holds notes, evidence, reviews,
  outputs, snapshots, and superseded rows.
- always requires an anchor (R1 in hq help rules), so a file with
  no headings cannot be always.
- drafts/ holds the candidate that becomes the final file, gated
  edit. A throwaway script goes to probes/. A thread pinned with
  hq work-dir keeps the specs, drafts, and outputs it makes in that
  directory instead (hq work-dir --help).
- Outside the folder only the draft rule lapses. A SPEC*-shaped name
  or a Spec/Design first heading still infers spec. Any other
  outside file infers other/never. Pass --kind spec to gate it
  always, or --kind draft to gate it edit.
- When a stem (SPEC) has several members, adopt and begin gate only
  the newest spec-kind file. Every older stem-mate is stamped
  superseded/never, pointing at the newest.
- Read first block: one line per live always row, with the size of
  what hq read prints. An anchored row shows
  .handoff/<slug>/specs/SPEC.md:11-13 (320 tok)  label. A whole
  file an older cycle left shows (186 lines, 8.2k tok). A file that
  is not UTF-8 text shows bytes, (35 KB). A directory shows
  (N files). Tokens are len(text) // 4.
- Artifacts block: a live always row prints its path, kind, tier,
  and cycle, and leaves its label to the Read first block. A live
  row stamped this cycle, or whose path or basename the cursor
  names, adds its label to 120 characters, then +Nc and the hq when
  command that prints the rest. Every other live row prints as its
  path alone, and one closing line names hq when <slug> <path>,
  which prints that path's rows. A folded row still gates. Rows no
  longer live collapse to superseded/archived/missing counts.
- Paths in both blocks resolve from a base the block's first line
  names. The two bases are the project root and a work dir pinned
  outside the root, written root ~/code/proj or work dir
  ~/code/proj-wt. The line names only the bases the printed rows
  use, which may be one, both, or neither. A row under no base
  prints absolutely. The folder's own files carry the folder,
  .handoff/<slug>/specs/SPEC.md, so none of them reads as a repo
  path. Where the line names both bases, one spelling can sit under
  either. The ledger, hq artifacts, and the begin work list keep
  the path as stamped. stamp takes either spelling, so the spelling
  a block prints keys the same row the stored spelling keys.
- Standing block: an item the cursor cites by id, or one this cycle
  recorded, prints expanded. A constraint prints its headline and
  the first sentence of its body. A decision, dead end, or evidence
  item prints its headline. +Nc counts the characters held back.
  Evidence prints only when the cursor cites it, and one line counts
  the rest. Every other item prints as [id] headline, with no cap.
  hq standing <slug> <id> prints any item in full. Ids are d
  decision, c constraint, x dead end, e evidence. (cN) is the cycle
  that recorded the item.
- Log line: <branch>@<sha> +N, where N counts the paths git reported
  dirty when that cycle finished.""",
    'merge': """\
hq help merge - carrying one thread into another

Two threads that hold one task become one. The keeper stays; the
source ends. Nothing in the source moves or changes: its ids, ledger,
and cycles stay whole, and every read verb still answers on it.

- Ask the user which thread is the keeper. Never pick it.
- Finish any open cycle on the source, then hq begin the keeper.
- Run hq standing <source>. Re-record each item that still holds
  with hq note on the keeper, --batch for many. Evidence keeps its
  --grade and --open, and drops the '<grade>, open:' prefix standing
  prints on its headline, since note writes the prefix again.
- A copied body drops its '; contradicts [<id>]' tail. Re-record
  the contradicted item first and pass --contradicts its keeper id,
  or name the item in the body by slug and headline.
- A note body or cursor line names the source slug, never a source
  id. The keeper reads every c, d, x, or e id as its own.
- Carry the source cursor: each Directive word for word, each open
  Plan step into Plan, each open question into Open questions, each
  Environment line still true. A Directive that cites a source id is
  the user's to reword: ask first. Past the keeper's first cycle, a
  changed Directives section needs finish --replace-directives
  "<why>". Task names the source slug once, as where its history
  lives.
- Stamp each source file the keeper still needs by its path,
  .handoff/<source>/notes/<file>, with --kind notes. Add
  --read-before edit when the cursor points at it. A source spec
  takes no --kind and a --where <heading>: R1 refuses --kind notes
  on it, and the refused row still gates. The row stores the path
  whole, as for any file outside the folder, so a repo move needs a
  re-stamp.
- After finish, tell the user to run /handoff done <source>. Never
  run it yourself.""",
    'read': """\
hq help read - the steps that follow hq open, in order

hq open prints these reading steps after its findings. Each binds.

- Every finding open prints is information, and none stops the read.
  A WARNING: (an edited recorded line), an unfinished cycle, or LEDGER
  BEHIND: note it and read past it. git drift names the git log to
  run. A moved sha, an unresolved anchor, or a moved span says what
  to read instead. A stale folder path waits for the next write.
- Read HANDOFF.md.
- Every ## Directives line binds from the first action for the whole
  session. A subagent brief whose work it touches repeats that line
  word for word.
- Then run hq read <slug> <path> on every ## Read first line that
  shows a span. It prints the span and records the read. A line
  printed instead of the span names its move.
- Read a whole-file row, which only an older cycle leaves, when the
  Now step names its file or the gate names it at a write. Never
  before.
- Read a section the row does not anchor when the work needs it:
  --section <anchor> in --where form, or --whole for the whole file.
- A conforming target with no ledger.tsv has no blocks yet. Follow
  its ## Key files 'Read now:' pointers by hand.
- A target with no conforming header names its own reading order.
  Follow it.
- Read nothing else.
- Drift check. When the header says dirty, run git status
  --porcelain and note what is still uncommitted. When the header
  has no sha (the file was written outside a repo), or there is no
  conforming header, skip the git steps. Note drift in one line and
  proceed.
- Do not re-plan, and do not reopen a Standing item. Re-check an
  open evidence item, or one a contradiction advisory names, by
  recording a new item.
- When open printed no 'hq loop: no one is watching this session'
  line and Open questions is present, recap in six lines at most:
  the task, what is settled, and what each question decides, each
  restated for a reader outside the thread. Then put the questions
  to the user and stop. Otherwise state the task and the Now step
  in two sentences, then execute Now. The Plan follows.
- An item the Standing block names prints whole with
  hq standing <slug> <id>. Where the thread started, how it got
  here, or where it stands overall goes to hq arc <slug> first.
  Answer from it, and read further only for a fact it lacks.""",
    'rules': """\
hq help rules - what the script refuses and why

R1  A spec or draft row keeps its tier and its kind, whether the
    kind was inferred, stored, or given by --kind. A spec read_before
    stays always. A draft read_before stays edit or always. Neither
    row may change its tier, leave live, or change kind, unless its
    successor, passed or carried forward, names a file on disk other
    than itself, or --archive --reason is given (--status archived
    --reason is the same). An explicit --successor whose own current
    row is not live is refused, so two specs cannot name each other. A successor with
    no row yet is accepted, and the next begin lists it as unstamped.
    A path that has ever been spec or draft stays gated: its way back
    to live is always for a spec and edit for a draft. The refusal
    lines, each printed after 'hq stamp: ':
      refused: R1: spec read_before must stay always without --successor or --archive
      refused: R1: draft read_before must stay edit or always without --successor or --archive
      refused: R1: <kind> status must stay live without --successor or --archive
      refused: R1: <kind> kind must stay spec or draft without --successor or --archive
      refused: R1: successor <path> is <status>, not live
      refused: R1: successor <path> is not another file on disk
    A live always row needs an anchor, whatever its kind. A stamp
    that would leave where at '-' is refused, new row and re-stamp
    alike, and the file's headings print under the line:
      refused: <path> always with no anchor - N lines read whole at every
        resume; stamp --where <heading> or --read-before edit
    begin lists every live always row with no anchor the same way.
R2  A refused stamp still appends a row: the previous fields, with
    reason set to 'refused: <why>'. With no previous row, it appends
    the kind table's seed and the file's sha and line count. The
    attempt is in the record and clears nothing.
R3  A live row with read_before always or edit, whose file sha
    differs from its sha12, blocks finish until the agent re-stamps
    it. A re-stamp alone clears R3.
W1, W2  finish and open check that ledger.tsv and standing.md are
    byte-prefix-identical to their last finished state. An edited
    recorded line blocks finish. When a known tool caused the break
    (a formatter, a merge), pass --acknowledge "<reason>" to finish.
    finish records the reason in the manifest.""",
    'stale-path': """\
hq help stale-path - a folder path under a former directory

'stale folder path in <file>: <dir>/<slug>/ x<n>' means the cursor or
an unsuperseded standing item names the folder under a directory an
earlier plugin version used (working/ or scratch/). The text points
at where the folder was.

- Run grep -rl '<dir>/<slug>/' over the repo to find every carrier.
- At the next write, inside a cycle, correct the path in each cursor
  line that carries it. Re-note and supersede each standing item
  that carries it. The count then clears.
- When a standing item records the move itself, name the old
  directory without the slug, as in "moved out of working/". The
  scan counts only the <dir>/<slug>/ form.
- Edit the path alone, and never a quoted sentence. Never edit
  HANDOFF.md outside a cycle: a rewrite outside one trips block sha
  mismatch at open, and begin archives the file as c<NN>.hand.md.""",
    'write': """\
hq help write - the cursor, ledger, Unfiled, and todo rules of a cycle

hq begin prints these writing rules after its work list. Each binds
this cycle.

Write for a reader with no memory of this session and full access to
the repo: short technical documentation in complete sentences, with
no transcript narration. Task and Now are required. Omit any other
cursor section that would be empty. A requirement the user stated,
an approval given, or a quirk found is lost unless written here. In
doubt, write it down.

Cursor rules:

- Directives holds an instruction the user gave for the life of this
  thread: one bullet each, whole, a few lines at most. "Run the
  integration suite with --db staging-copy. Never point a test at
  the shared database." A rule the agent worked out is a constraint.
  A rule for every thread in the repo belongs in CLAUDE.md. The next
  step is Now.
- Directives carries word for word. To add, rewrite, or clear one on
  the user's word, edit the section and pass finish
  --replace-directives "<why>". finish records the old section in a
  decision, and refuses any other change to the section.
- Point at a file, and never paste its content. When a cursor line
  moves into a sibling file, stamp that file with hq stamp in this
  cycle. Its pointer line is generated. Never type one.
- Skip what the repo records: git history, CLAUDE.md, README content.
- Environment carries the background tasks still running, any second
  repo this session changed (path, branch, sha), and a check that
  outlasts the Now step, such as the test command.
- A notes sibling is notes/<topic>.md. Stamp it --read-before edit
  when the cursor points at it. Its label, to 120 characters, prints
  in the Artifacts block while the cursor names it.
- Name where a credential lives, never its value.
- Absolute dates. ASCII only.
- Now is the single next action. Put the action in its first
  sentence, which is the one hq arc prints. If no other section
  says how the next session checks that the action is done, Now
  says so. Plan, the approved plan, holds the steps in order. Now
  is its first open one, stated as the next action. A resuming
  session carries out Now and follows Plan without re-planning.
- Each cycle replaces Now and nothing else. Every other line stays,
  or moves whole: a closed approach to a Standing item by the next
  rule, a fact true for the rest of the thread to a constraint, and
  the rest to a notes sibling. Never cut one. finish lists a line
  dropped anyway.
- Every approach a cycle closes becomes one Standing item that names
  it. A ruled-out approach is a dead end, and its headline carries
  the scope it was ruled out on. A result later work builds on is a
  decision. A headline that only points elsewhere does not count.
- Anything still awaiting the user, a question or an unapproved
  plan, goes under Open questions. The next read stops there. Under
  hq loop, only a security or stability question about a step the
  user did not ask for stops it.
- An item recorded with note or under ## Unfiled is not repeated in
  State, because the Standing block carries it. A line that cites
  evidence ids stays, since uncited evidence leaves the block.
- Cite a c or x id in brackets, [c07]. A bare one reads as a cycle.
  A range, [e01-e05], cites nothing.
- No line ceiling binds the cursor. A block may hold text back,
  because its store keeps the whole. The cursor has no store, so a
  reworded line drops facts.

The Log line. The finish --log text is the cycle's whole entry in
hq arc:

- Say what the cycle did to the task: what shipped, what a
  measurement found, what was decided, with the name, version, or
  figure a reader can check.
- Write out what a standing id says. A bare id reads as nothing.
- A cycle that only repaired the record says which fact it restored.
- A check writes check: <n> findings applied - <what they changed>.

Ledger tiers. read_before is a tier, and stamp seeds it from the
kind: a spec seeds always, a draft edit, all else never.

  tier     means                    loaded when
  always   the contract             every resume
  edit     before a change to it    the gate, at a write to it
  never    on the record            on demand: hq read, hq when

- always requires an anchor. A stamp that would leave a live always
  row with no --where is refused, and the file's headings print
  under the refusal. A spec needed whole is anchored at its title
  heading. A whole file cannot be always: a notes file the cursor
  points at is edit, and a source file is edit or never.
- stamp accepts a wrong anchor, which shows only as SPEC.md:? in the
  read block. Check the heading first. Every --where form is in
  hq help anchors.
- Path resolution, the values a re-stamp carries forward, and the
  status moves --successor and --archive --reason make are in
  hq stamp --help.

Rules the script enforces (hq help rules has each in full):

- R1 A spec row keeps always, and a draft row keeps edit or always,
  whether the kind was inferred, stored, or given by --kind. Both
  keep that tier, live, and their kind, unless --successor names
  another file on disk, or --archive --reason is given. A path that has ever
  been spec or draft stays gated: its way back to live is that tier.
- R2 A refused stamp still appends a receipt row, reason set to
  'refused: <why>'. It clears nothing.
- R3 A live row graded always or edit whose file sha moved blocks
  finish. A re-stamp alone clears it.
- W1, W2 An edited recorded line in ledger.tsv or standing.md blocks
  finish. When a known tool caused it (a formatter, a merge), pass
  --acknowledge "<reason>" to finish.

Standing items:

- supersede echoes the item it drops from the block. A ruling inside
  that text which still holds takes one more hq note under a new id.
- To retire an item of any kind that no longer holds and has no
  replacement, run hq supersede <slug> <id> --reason "<why>".
- A claim the work rests on is evidence: a measurement, quoted
  primary wording, a data-field fact, a fact about the code as
  built, a prior finding. A rule the work follows stays a decision
  or a constraint.
- Grades. measured means the work observed it or quoted the primary
  source. inferred means the work reasoned it from other items.
  asserted means a source states it and nothing checked it.
- The body names the source and the inputs. A qualified status
  ("one witness") and the Plan item that acts on the claim
  ("action: Plan item 3") go there too.
- --open marks a claim not yet settled. To settle it, record a new
  item and supersede the open one onto it.
- finish checks --contradicts between evidence items. A
  contradiction with anything outside standing.md goes in the body
  as text.
- The cursor cites the claims in view, [e01, e02].
- An item that rests on an asserted item carries no mark of it. A
  doubt the grades and contradictions do not show goes in the body.

Unfiled. Items a later cycle must follow or know, settled this
session with no note call, go under ## Unfiled, above the first
<!-- hq: marker, as typed bullets, one per item:

  - decision: **<headline>** <body>
  - constraint: **<headline>** <body>
  - dead-end: **<headline>** <body, wrapped onto indented lines
    when long>

- A bullet with no bold span takes its first sentence as the
  headline. A one- or two-word sentence, 'Cycle 26.', is a label,
  and the headline runs on past it.
- finish drains the section into standing.md. An untyped bullet
  blocks finish.
- Omit the section when every item went through note.
- A call that settles only this step goes in the finish --log line
  and nowhere in Standing.

A todo file the project keeps holds work no thread has taken up.
When this thread takes up an item from one, move it into the
handoff. Write the item into Plan, and delete it from the todo file
in the same write. The handoff then owns the item. Nothing in the
cursor points back at the todo file.""",
    }
