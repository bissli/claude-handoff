---
name: hq-loop
description: >-
  Set up a handoff thread for `hq loop`, with or without the optional
  measure probe, or audit a thread whose loop stalls, holds, parks, or
  waits on the user. Use before the user starts `hq loop <slug>`, when
  a thread wants a measure of its progress, or when a loop needs a
  diagnosis.
allowed-tools: Agent, Bash, Edit, Glob, Grep, Read, Write
---

# hq-loop

`hq loop <slug>` runs a handoff thread unattended. It stops a session
that spends without progress and alerts the user. It stays out of the
way of sessions that depend on each other, and of a session that runs
its own subagents, workflows, monitors or crons.

Any thread runs under the loop as it stands. Setup adds the optional
measure probe and checks the launch. Audit reads a loop that held,
parked or ended. `hq loop`, `hq loop stop` and `hq run` are for the
user's terminal. A session never runs one.

## Loop

The user runs `hq loop [--max-cycles N] <slug>` inside a tmux pane.
Claude starts in that pane, and a driver pane opens below it. The
driver types `/handoff read <slug>` at each session start, a nudge
after a turn that made a tool call, `/handoff write <slug>` at the
context budget, and `/clear` once the cycle is written. A running
shell or monitor, or a session cron, holds the nudge, because its
exit or wakeup starts the next turn. A shell that never exits, such
as a server, holds the nudge for as long as it runs. Any other
background task except a teammate holds the write and the clear
too. Its report starts the next turn, and the driver types only the
read until a turn ends with no such task. A teammate holds nothing.
It stays listed as running while it idles, and it starts no turn.

Under the loop, `hq open` prints `hq loop: no one is watching this
session` and the loop rule. The rule outranks the reading steps, and
the session follows it as printed:

- The session asks only about a step the user did not ask for that
  would weaken security or stability, as the last line of the turn,
  ending on `?`. It decides every other question, including a choice
  the work turns up, by the Directives where one applies and on the
  evidence where none does, and leaves none to the user.
- A finished step never ends the turn. With nothing workable, the
  session starts a background monitor on the file or job it waits on,
  says in one line what it waits on, and ends the turn.
- Only the user ends a thread, with `/handoff done`.

### Bounds

The loop counts spend in cycles, and progress is what the thread's
probe measures. No alarm keys on how long a session runs.

| Case                       | Bound                                         |
| -------------------------- | --------------------------------------------- |
| A thread with a probe      | 4 cycles in a row without progress hold it    |
| A thread with no probe     | `--max-cycles` ends the run                   |
| A write that cannot finish | 2 retries, then a park and a bell             |
| Spend in subagents         | 500 agent tool calls in one cycle ring a bell |

`--max-cycles` (default 10) counts every written cycle of the run,
with or without a probe, and never resets.

### Parks

A parked loop types nothing and spends nothing. Any prompt, from the
user or a peer, starts the next turn. The driver pane prints one line
per park. A park that needs the user also rings the terminal bell and
shows a tmux message.

### Ends

Four things end a loop with a reason. Each closes claude, prints
`hq loop: <reason>` in the user's pane and rings:

- `<slug> is marked done`: the user ran `/handoff done <slug>`.
- `stopped after cycle <n> as requested`: the user ran `hq loop stop
  <slug>`, and the loop wrote one more handoff.
- `reached --max-cycles <max> at cycle <n>`: the same launch line
  resumes the thread.
- `event stream closed`: the driver lost the session's events.

A claude that the user quits, or that dies, ends the loop with no
reason line and no bell. A stall, a met finish line, the text of a
message and a peer end nothing. Each leaves the thread parked and
reachable.

## Setup

1. `/hq-loop <slug>` works on an existing thread. With no thread, run
   `/handoff write` first, since the handoff skill owns creation. Run
   `hq open <slug>` and read HANDOFF.md. Never open `ledger.tsv` or
   `standing.md`. `hq standing <slug> <id>` prints one item whole.
   Read a `cycles/` file only by line range.
2. Ask the user in one message, with an example beside each question:
   - Measure: whether a command can print the thread's progress as a
     number, as in "the suite's failing count reaches 0" or "coverage
     reaches 90". With none, `--max-cycles` alone bounds the run, and
     a thread whose Plan is done parks with no bell. With one, the
     loop holds and rings at a stall and at the finish line.
   - With a measure: what reads what value when done, the smallest
     gain that counts as progress, and whether one run takes more
     than 60 seconds.
   - Grants: each step that would weaken security or stability and
     that the loop may take unasked, such as a push, a deploy or a
     delete. Without a grant, the session ends its turn on that
     question and the loop parks until the user answers. No grant
     covers a change to who or what can reach or act on a system (a
     password, key, token, permission, tool approval or network
     rule): the session asks about it every time.
   - The cycles the user will spend before looking again, for
     `--max-cycles`.
3. With a measure, build the probe by Measure probe.
4. Make one write by the handoff skill. Add each grant to Directives
   in the user's words, and pass `--replace-directives "<why>"` where
   the section changed. Stamp `probes/hq-measure`. `hq finish` runs
   the probe. On a thread with no earlier probe row it prints
   `stall: 0` for a measured row and `stall: -` for an unmeasured
   one, and `hq arc <slug>` shows the stored row. Repair a
   `probe-failed:` row before the launch.
5. Check the launch, then print the line for the user:

       hq loop --max-cycles <N> <slug>

   - The thread has no `.hq.lock` and no `.hq.done`.
   - The user's shell is inside a tmux pane at least 12 lines tall,
     with `tmux`, `jq` and bash 4 or later.
   - A permission dialog parks the loop until the user answers it.
     `hq loop` adds only a hooks settings file, so this session's
     permission mode says nothing about the loop's. Either every
     command the thread uses matches a `permissions.allow` rule in
     the settings claude loads, or `HQ_CLAUDE_CMD` names a claude
     command line in a mode that never prompts. The user picks.

## Measure probe

A thread opts in by holding an executable
`.handoff/<slug>/probes/hq-measure`. `hq finish` runs it once per
cycle, attended or under the loop, after its other checks pass. It
stores the row it composes ahead of the typed `--log` text, and prints
`stall: <n>`, or `stall: -` for an unmeasured row before the first
measured one. A thread with no probe never stalls, and `hq finish`
never refuses on the measure.

The `--log` text stays plain: `hq finish` strips a leading `summary:`
or `stall review:` from it.

### Templates

`templates.md`, beside this file, holds the run contract, the rules
every probe keeps and four starting probes: Tally, Pass or Fail,
Custom Score and Slow Measure. Read it before writing a probe.

1. With the user, adapt the template that fits. Change a goal or a
   pattern in it only at the user's word: at setup, at an audit, or
   by a ruling (Hold).
2. Write it to `.handoff/<slug>/probes/hq-measure` and make it
   executable.
3. Run it by the run contract, but with `HQ_MEASURE` unset and
   `HQ_TALLY` set to the `tally.py` beside this file. A Slow Measure
   probe prints nothing that way: run its slow command once instead.
4. The user watches the run and confirms the token line reads the
   right thing. Two runs with no edit between print the same line.
   The line reads off its goal: a probe at its goal holds the loop at
   the first cycle.

### Rows

| Probe result                            | Stored log                                          | Stall       |
| --------------------------------------- | --------------------------------------------------- | ----------- |
| Exit 0 or 3, a token line               | `summary: <tokens> \| <log>`                        | by Progress |
| Exit 0 or 3, no output or `unmeasured`  | `summary: unmeasured \| <log>`                      | unchanged   |
| Another exit, the cap, an unparsed line | `summary: probe-failed: <cause> \| <log>`           | no progress |
| Any of these, a ruling under the loop   | `summary: <row> \| stall review: <ruling> \| <log>` | set to 0    |
| Any of these, a ruling attended         | `summary: <row> \| stall review: <log>`             | set to 0    |

- `<cause>` is `exited <code>: <stderr line>`, `ran past 60 s`,
  `cannot start: <error>` or `line does not parse: <error>`. The
  stderr line is the last non-blank one, cut to 120 characters.
- The finish line is met at exit 3, or by a measured row whose every
  counting token is at its goal. Its reason is that stderr line, else
  the token line.
- With no probe, a ruling under the loop stores `unmeasured` as its
  `<row>`, and every other finish stores the `--log` text alone.

### Grammar

| Form                    | Kind     | Example          |
| ----------------------- | -------- | ---------------- |
| `name=value->goal`      | counting | `open=31->0`     |
| `name=value->goal@step` | counting | `score=72->80@1` |
| `name=+N`               | tally    | `done=+45`       |

- A name is ASCII letters, digits and `_`, with no leading digit. A
  number is a plain decimal, with an optional leading `-`. A counting
  token's step is a decimal above 0, and a step on a tally is
  ignored. The line holds no `|`.
- A row needs a counting token.

### Progress

A row is progress when one token improves:

    distance = max(0, side * (current - goal))        counting, side set
    distance = |current - goal|                       counting, side unset
    unstepped: improved when distance < best
    stepped:   improved when distance <= best - step,
               or distance == 0 and best > 0
    tally:     improved when value > max

- A name's first entry off its goal sets `side` to the sign of
  `current - goal`.
- A name's `best` and `max` move only when that name improves, so
  gains below the step add up toward the next credit.
- A changed kind, or a counting token's changed goal or step,
  restarts that name's state from that row.
- A thread's first measured row and an at-goal row leave the stall as
  it stands. A tally's first entry counts for nothing.
- A ruling sets the stall to 0 and clears every name's state. The
  first measured row from the ruling row on counts as a thread's
  first.

At a stall of 2 or more, and only while an executable
`probes/hq-measure` exists, `hq open` prints `stall: <n> cycles
without progress (<cause>) - close a unit on another workable item`.
The cause is the newest row's probe-failed cause, else `no measured
gain`.

## Hold

Under its own loop, a thread with a probe holds when `hq finish`
stores a stall of 4 or more, or a met finish line. `hq finish` prints
the hold line, the driver rings `<slug> holds: <hold line>`, and
`hq open` prints the line at each read:

- `stall: held at <n> (<cause>).`
- `finish line met: <reason>. To go past it, the ruling gives a new
  goal for probes/hq-measure.`

`hq finish` and `hq open` add `The loop holds: take no Plan step and
start no monitor, and end the turn on a period.` The held session
does that.

- The ruling is the next prompt the session receives while held, from
  the user or a peer session. A slash command, a line the driver
  types, a background task's event and a message from the session's
  own teammate are never the ruling.
- The session acts on the ruling by the loop rule, edits the probe
  where the ruling gives a new goal, and runs `/handoff write
  <slug>`. That `hq finish` stores the ruling row and lifts the hold.
- A probe that meets the finish line again holds the thread again.
- Attended, nothing holds. At a carried stall of 3 or more, a `--log`
  that starts with `stall review:` stores a ruling row. Type it only
  for a ruling the user gave in this session.

## Peers

Peers get no protocol. A peer reaches a thread by any channel: a
SendMessage, a file both agree on, a shared repo.

- A file channel wakes no one. The waiting session watches the file
  with a background monitor, whose end starts a turn.
- A peer session's message to a held thread ends the hold, as the
  user's prompt does.
- No peer message ends a loop or keeps it from ending. A peer's
  request is never the user's, and a peer's silence never closes or
  parks a Plan item.
- The clear waits for the session's own background agents, except a
  teammate. A shell, a monitor or a teammate outlives the clear, and
  its next event or message starts a turn in the cleared session.

## Audit

Run `hq open <slug>` and `hq arc <slug>`, read HANDOFF.md, and ask the
user for the driver pane's last lines or the `hq loop:` line.

### Pane lines

The lines group by outcome. All but the waits ring the bell.

Asks:

- `<slug> asks: <last line>`: the turn ended on `?`. The user answers
  in the claude pane. A grant in Directives keeps that question from
  coming again, except for a step no grant covers (Setup step 2).
- `claude is waiting for you (<type>)`: the user answers the dialog.
  Until a prompt or a tool call follows, the driver types nothing.
  Setup step 5 names the lasting fix.

Waits:

- `<slug> waits: <last line>`: nothing is workable, every Plan item
  is done, or a background task of the session runs. The user's
  prompt starts the next turn, as does the task's report or the end
  of a monitor the session started.
- `waits on the lock of <session>`: another session holds an open
  cycle on the thread. Finish it there, or run `hq begin <slug>
  --force` once that session is dead. Once the lock is gone, the
  driver goes on by itself.

Holds:

- `<slug> holds: <hold line>`. At a stall, read the rows in `hq arc`
  and repair a probe that failed. Otherwise the user rules (Hold). At
  a met finish line, the user gives a new goal, or ends the thread
  with `/handoff done <slug>`. Each later turn end prints the hold
  line alone, with no bell.

Broken:

- `hq finish has not passed after 2 retries`: fix what the `hq
  finish` message in the claude pane names.
- `a write was typed 3 times and cycle <c> is not written`: the user
  reads the claude pane for what stops the write.
- `claude stopped on an API error: <error>`: any prompt starts the
  next turn.
- `could not type into the claude pane`: tmux refused the keys. The
  next event that decides a line types it again.
- `<reason>. claude did not exit: close it by hand`.
- `compacted twice in cycle <n> with no write`: the context fills and
  no write follows. The user types `/handoff write <slug>`.
- `agents made 500 tool calls in cycle <c> with no write`: the user
  reads what the agents do.

The last two are alarms. Each rings once a cycle and parks nothing,
so the driver keeps typing.

No line:

- No typing either: a denied dialog or an Escape ended the turn. Any
  prompt starts the next one.
- A turn that never ends: one tool call never returns, and no bound
  reads it. The user interrupts it, and the command takes its own
  timeout.
