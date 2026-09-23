# claude-handoff

A Claude Code plugin that tells you, in session and while there is still
room, that the conversation has grown expensive enough to hand off.

```
248K/350K [=======---] handoff in 3  $1.09/t  opus myproject
310K/350K [========--] handoff now   $1.36/t  opus myproject
452K/350K  1.3x over                 $1.99/t  opus myproject
248K/350K [=======---] handoff in 3  $1.09/t  opus myproject:auth-token
```

## Install

```
/plugin marketplace add bissli/claude-handoff
/plugin install claude-handoff@claude-handoff
```

The first command registers this repo as a plugin source (a
"marketplace"); the second installs the hooks and the `/handoff`
command from it. It needs `python3` on `PATH` and nothing else. The
status line takes one manual step, described
[below](#status-line-optional-one-manual-step).

## The problem

Claude Code talks to a stateless API: every request re-sends the whole
conversation. A turn - one prompt from you, plus everything Claude does
before it waits for you again - is about 9 requests, one per tool call,
and every one of them re-sends everything that came before it.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/resend-dark.svg">
  <img alt="Stacked columns of tokens sent per turn. Each turn adds about
17K new tokens on top of everything before it, and the whole stack is
re-sent every turn, reaching about 270K by turn 12."
src="docs/resend-light.svg">
</picture>

Prompt caching is what makes this affordable at all: a token re-read
from the cache costs a twentieth of a fresh one on Opus 5.5 and a
fortieth on Fable 5.1. Those are the two models where this is worth
money: Opus 5.5, Claude Code's default, and Fable 5.1, the top tier,
which bills two and a half times as much for a fresh token.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/cache-discount-dark.svg">
  <img alt="Two panels comparing the cost of one turn as context grows to
500K. On Opus 5.5 the turn costs $17.60 at full price and $0.88 through
the cache. On Fable 5.1 it costs $44.00 at full price and $1.10 through
the cache."
src="docs/cache-discount-light.svg">
</picture>

But the discount is not a cure. The cache lowers the price of each
re-read token; it does nothing about the number of tokens re-read, which
grows every turn and never shrinks. Zoom in on the cached lines above
and the climb is still there:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/cost-per-turn-dark.svg">
  <img alt="Cost of one turn against context, at cache-read prices.
Fable 5.1 crosses the $0.62 target at 282K tokens and $0.88 at 400K.
Opus 5.5 crosses $0.62 at 352K and $0.88 at 500K."
src="docs/cost-per-turn-light.svg">
</picture>

So the plugin holds a dollar line, not a token line: **$0.62 a turn** as
the target and **$0.88 a turn** as over budget. Each model's token
thresholds fall out of its own price, which is why Fable 5.1's gauge
fills a quarter faster than Opus 5.5's. (A model priced at the older
cache-read rate cannot reach $0.62 a turn above the point a compaction
restarts at, so its target is the compaction cycle's floor instead.
[The budget knob](#the-budget-knob) has the arithmetic.)

Every figure below already includes the cache discount. The bill being
discussed is the discounted one.

## What a turn costs, point by point

One turn costs `context x cache-read rate x 8.8 calls`. The tables show
that cost at each context size, the multiple of that model's own target
cost, and what the same turn would have cost without the cache.

**Opus 5.5** - cache read $0.20 per million tokens, target $0.62 at
352K:

| context           | one turn | vs target | without cache | cache saved |
| ----------------- | --------: | ---------: | -------------: | -----------: |
| 100K              | $0.18    | 0.3x      | $3.52         | $3.34       |
| 200K              | $0.35    | 0.6x      | $7.04         | $6.69       |
| **352K** - target | $0.62    | 1.0x      | $12.39        | $11.77      |
| 400K              | $0.70    | 1.1x      | $14.08        | $13.38      |
| **500K** - over   | $0.88    | 1.4x      | $17.60        | $16.72      |
| 700K              | $1.23    | 2.0x      | $24.64        | $23.41      |
| 1M                | $1.76    | 2.8x      | $35.20        | $33.44      |

**Fable 5.1** - cache read $0.25 per million tokens, target $0.62 at
282K:

| context           | one turn | vs target | without cache | cache saved |
| ----------------- | --------: | ---------: | -------------: | -----------: |
| 100K              | $0.22    | 0.4x      | $8.80         | $8.58       |
| **282K** - target | $0.62    | 1.0x      | $24.82        | $24.20      |
| **400K** - over   | $0.88    | 1.4x      | $35.20        | $34.32      |
| 500K              | $1.10    | 1.8x      | $44.00        | $42.90      |
| 700K              | $1.54    | 2.5x      | $61.60        | $60.06      |
| 1M                | $2.20    | 3.5x      | $88.00        | $85.80      |

Read the Fable 5.1 row you are sitting at: a session parked at 500K pays
$1.10 for every further turn - 1.8x what it would pay at its target -
and the cache is already saving it $42.90 a turn. Both columns grow
together, because both are the same line at different prices.

## What a whole session costs

Per-turn cost climbs in a straight line, so a session's total climbs as
its square: each new turn costs more than the one before it. Handing off
resets the line; running on rides it up.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/session-cost-dark.svg">
  <img alt="Cumulative cost of a 40-turn session, growing 17K a turn.
On Opus 5.5, running on reaches $28 while handing off at 352K holds it
to $14. On Fable 5.1, running on reaches $35 while handing off at 282K
holds it to $15."
src="docs/session-cost-light.svg">
</picture>

Forty turns, growing 17K a turn, starting at the 69K fresh-session
floor, restarting at 72K after each handoff. Writing a handoff spends
about two of those turns, so the held column also buys a little less
finished work, not only fewer dollars:

| 40 turns on | hand off at target | run on to 732K | held vs run on |
| ----------- | ------------------: | --------------: | --------------: |
| Opus 5.5    | $14                | $28            | -52%           |
| Fable 5.1   | $15                | $35            | -57%           |

The cache and the handoff attack different halves of the bill. On the
run-on Fable 5.1 session the cache already turned a would-be $1,410 into
$35; handing off is what turns the $35 into $15. Neither substitutes
for the other.

## The exit: /handoff

The warnings point at `/handoff`, a command the plugin installs. It
writes the session's state - plan, key files with line anchors, settled
decisions, dead ends - to `.handoff/<task-name>/HANDOFF.md`, the folder
named after the task, and a fresh session reads it back and resumes.
The alternative exit, `/compact`, summarizes the conversation in place.
They restart at very different sizes:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/restarts-dark.svg">
  <img alt="Billed context on the first call after each exit: a new
session starts at 69K, a handoff restart at 72K, a compact restart at
123K."
src="docs/restarts-light.svg">
</picture>

Compaction cannot touch the ~69K floor - the system prompt, tool
schemas, and instruction files are re-sent in full either way - so it
compresses only the conversation, the part that was already smallest,
and its summary plus preserved tail land the restart near 123K. A
handoff file is 2-5K, exact rather than summarized, and starts a
session that carries nothing else. Compact still earns its place
mid-task, when the preserved tail - the messages a session was part-way
through - is worth paying for.

Using it:

```
/handoff                       writes or updates the handoff, checks it
  (kill the session, start fresh)
/handoff auth-token-refresh    reads that handoff back, resumes its plan
/handoff list                  every handoff here, with plan progress
/handoff check auth-token-refresh    re-reviews one in place
/handoff done auth-token-refresh     marks the thread finished
/handoff --no-check            writes without the reviewer pass
```

(`auth-token-refresh` stands for whatever folder name the writing
session chose.)

- A write cycle ends with a reviewer pass: a fresh agent reads the
  finished file cold and reports what a resuming session could not act
  on. `--no-check` skips the pass, and only the person invoking the
  command may pass that flag. A writing session never rules its own
  cycle too small to review.
- There is no verb to type. A session that edited a file or settled a
  decision holds something worth telling a fresh session, so `/handoff`
  writes; one that only looked something up reads a handoff back
  instead. Where either the verb or the target is ambiguous, it lists
  the candidates and stops rather than guessing.
- The folder outlives any one file. `.handoff/<task-name>/` holds the
  hand-written cursor - task, next step, plan, state, open questions -
  in `HANDOFF.md`; an append-only ledger of the thread's artifacts in
  `ledger.tsv` (which spec, draft, or notes file matters, and whether
  it must be read before the next edit); the settled decisions,
  constraints, and dead ends in `standing.md`, append-only; and every
  finished cycle verbatim under `cycles/`. A small script, `hq.py`,
  which the plugin puts on the agent's PATH as `hq` while it is
  enabled, writes the ledger, renders the generated blocks of
  `HANDOFF.md` from it, and refuses the three edits that lose work over
  many cycles: lowering a spec's or a draft's tier without naming its
  successor, grading a whole file as the contract with no anchor, and
  rewriting a recorded line in place. What the thread made sits in the
  thread folder under `specs/`, `drafts/`, `notes/`, or `outputs/`,
  never loose at its top level, and the ledger points at it. A thread
  whose spec and experiments already live in a project directory pins
  that directory once with `hq work-dir`, and its specs, drafts, and
  outputs go there instead; a pin is the thread's own, and no global
  setting moves every thread at once.
- Run again a session later, it updates the same folder: the cursor
  rewritten, the plan ticked off, decisions and dead ends appended,
  the previous cycle archived. What is no longer live is counted, not
  printed, and what is read at resume is a spec's anchored span, its
  size beside it, never a whole file. The file grows with what stays
  live, not with the cycle count: a thread that supersedes as it goes
  holds its size, and one that accumulates distinct live items pays
  about 58 tokens a cycle for them.
- A thread ends with `/handoff done <task-name>`. The folder survives
  whole and every query verb still answers it, but `/handoff list`
  stops showing it and the next `hq begin` refuses it. `hq list --done`
  names the finished threads, and `hq done <task-name> --undo` reopens
  one. A task leaves the list without its folder being deleted.
- Each write ends with a reviewer pass that must reconstruct the task
  from the file alone. Reading starts with `hq open`, which reports
  drift - a moved commit, a gated file edited since its stamp, a
  heading an anchor no longer finds, a label a re-stamp moved past the
  rendered block - then reads the spans the ledger
  gates and executes the file's next step without re-litigating
  settled decisions.
- Two advisory hooks back it. Once a handoff is opened, at a write in
  reach - under the project root or the thread's pinned work dir - a
  PreToolUse hook names each gated file the session has not read: a
  spec's span at the first such write, a draft at a write to that
  file, each path once. A read is the Read tool, the path as an
  argument of the `cat`, `head`, `tail`, `less`, or `sed -n` that
  names it, or an `hq read` receipt. At the end of a turn, a Stop hook
  says so when `HANDOFF.md` was written by hand since its last
  recorded cycle. Neither blocks; `HQ_GATE=0` in the environment turns
  the first off, and `HQ_GATE_DENY=1` makes it deny the write instead
  of reporting.
- Every line `hq` prints that calls for a move names it, and
  `hq help <topic>` (anchors, kinds, read, rules, stale-path, write) and
  `hq <verb> --help` carry the reference detail the skill file points
  at, so the skill stays short enough to survive a compaction whole.
- `/handoff when`, `diff`, `artifacts`, and `standing` query the
  ledger: one path's history by any spelling of the path, the cursor
  change between two cycles by section, every live artifact, every
  standing item or one item in full by its id.

Every artifact the ledger records carries a tier, `read_before`, that
says when a session loads it. A spec is the contract and seeds
`always`; a draft seeds `edit`; a note or an output is graded by the
writing session when the cursor points at it.

| tier      | means                     | loaded when                    |
| --------- | ------------------------- | ------------------------------ |
| `always`  | the contract              | every resume, an anchored span |
| `edit`    | read before you change it | the gate, at a write to it     |
| `mention` | know it exists            | on demand, `hq read`           |
| `never`   | on the record             | `hq artifacts`, `hq when`      |

`.handoff/` belongs in the project's gitignore when handoffs should stay
untracked. Versions before 0.3.0 wrote to `working/`, and before 0.2.2
to `scratch/`; move each old folder to `.handoff/<task-name>/` once.
After the move, `hq open` names every cursor line and standing item
that still says `working/<task-name>/` or `scratch/<task-name>/`, unless
that directory is the thread's own pinned work dir, where the paths are
current.

## What you see

At the end of a turn, once as each line is crossed - approaching the
target, at it, over budget:

> Context 293K of a 352K budget, growing 19K a turn. About 4 turns of
> room left - a good point to run /handoff.

> Context 362K, at the 352K budget. Run /handoff, then run it again in a
> fresh session to read it back: that restarts near 69K plus the file,
> against 123K for /compact. Compact instead only to carry the tail of
> this conversation, which buys about 12 more turns.

> Context 517K, about $0.91 a turn - 1.5x the $0.62 target. Every
> further turn pays to re-read history you are not using. Run /handoff.

The last two also raise a desktop notification.

## The budget knob

One knob, `COST_PER_TURN_TARGET` in `scripts/budget.py`, in dollars per
turn. Each model's token thresholds are derived from it:

| model     | target  | over budget | $/turn at target |
| --------- | ------- | ----------- | ---------------- |
| Opus 5.5  | 352,273 | 500,000     | $0.62            |
| Fable 5.1 | 281,818 | 400,000     | $0.62            |

Cost parity puts both current models clear of the compaction cycle's
floor, so each sits on its dollar line. A model billed at the older
cache-read rate does not: an Opus 5 turn costs $0.62 at 140,909 tokens,
below the 206,600 a five-turn compaction cycle needs, so its target is
that floor and a turn there costs $0.91. The gap is the real price of
staying on a model priced at the older rate, stated rather than hidden.
A quiet session pulls the target down toward whichever of the two
binds; it is latched per session and never rises.

Over budget stays a dollar figure ($0.88) and is never scaled up with
the target, so a heavy session cannot march the loudest warning out to
$5 a turn. Sonnet and Haiku are deliberately absent: a long session on
either costs little enough that interrupting it would cost more
attention than it saves.

## Pushing the agent to hand off (optional)

The warnings above reach the terminal, not the agent: a Stop hook
writes its message for whoever reads the screen, so a session runs past
its handoff point while the agent driving it never learns.

Creating `~/.claude/.nudge-handoff` changes that. While the file
exists, a `UserPromptSubmit` hook names the context and the handoff
point to the agent at the top of each turn, and asks for a handoff at
the next natural stopping point rather than at once. That is the one
moment an instruction redirects a turn without interrupting work
already under way.

```
hq nudge on    # arm
hq nudge off   # disarm
hq nudge       # report which it is
```

Presence alone is the switch and it takes effect at the next prompt, so
nothing sticks armed. The hook ignores the contents, which leaves room
for a line recording what the file is for. The nudge keeps no state of
its own. It measures the context from the transcript rather than
trusting the figure the Stop hook stored, because `/clear` and
`/compact` leave that figure behind them, and it stays silent below the
handoff point and inside an open cycle.

## The math

Anthropic list prices (September 2026), per million tokens:

| model     | input  | cache read | cache write | output |
| --------- | ------: | ----------: | -----------: | ------: |
| Opus 5.5  | $4.00  | $0.20      | $5.00       | $20.00 |
| Fable 5.1 | $10.00 | $0.25      | $12.50      | $50.00 |

A cache read is a multiple of the input price, and the multiple is not
the same everywhere: 0.05 on Opus 5.5 and 0.025 on Fable 5.1, against
the 0.1 every earlier model charges. The plugin stores the product, so
a model matched to its family rather than its own generation is priced
at up to two and a half times what it bills.

The plugin's cost model:

```
one turn = context x cache-read price x calls per turn
         = context / 1M x cache-read $/M x 8.8
```

Worked examples:

```
Opus 5.5  at 352K:  0.352 x $0.20 x 8.8 = $0.62 a turn
Fable 5.1 at 400K:  0.400 x $0.25 x 8.8 = $0.88 a turn
```

And inverted, to set the thresholds:

```
target tokens = budget / (cache-read $/M x 8.8 / 1M)
Opus 5.5:  $0.62 / ($0.20 x 8.8 / 1M) = 352,273
Fable 5.1: $0.88 / ($0.25 x 8.8 / 1M) = 400,000  (over-budget line)
```

Two costs are deliberately left out. Writing a turn's new tokens into
the cache (~17K at 1.25x input) and the output tokens themselves are
both real, but neither grows with context - they add a roughly flat
fraction of a dollar to every turn regardless of size. Deep in a session,
cache reads are nearly the whole bill, and they are the only part that
climbs, so they are the part the thresholds track.

The defaults, measured from a month of the author's usage - sessions
with different tool habits will measure differently, which is why the
hook re-measures growth per session:

- **8.8** assistant calls per user turn
- **1,900** tokens of growth per call (~17K a turn) until a session has
  history enough to measure its own rate
- **69,000** billed tokens for a fresh session, **123,000** after a
  compaction

## Glossary

| term           | meaning                                                                                                          |
| -------------- | ---------------------------------------------------------------------------------------------------------------- |
| turn           | one prompt from you plus everything Claude does before waiting again                                             |
| call           | one API request; each tool use is one, ~9 per turn                                                               |
| context        | everything re-sent with every call: system prompt, tools, conversation                                           |
| billed context | `cache_read + cache_creation + uncached_input` on the last call                                                  |
| cache read     | a re-sent token served from the prompt cache, at 5% of input price on Opus 5.5, 2.5% on Fable 5.1, 10% elsewhere |
| cache write    | a new token added to the cache, at 125% of input price                                                           |
| target         | context where a turn costs $0.62, lifted where the compaction cycle needs more; the gauge's 100%                 |
| over budget    | context where a turn costs $0.88; never scaled up                                                                |
| reserve        | room held below the target so the handoff itself still fits                                                      |
| floor          | what a session is billed before any conversation: ~69K                                                           |
| handoff        | write state to a file, start fresh; restarts at floor + file                                                     |
| compaction     | `/compact`; summarizes in place and restarts near 123K                                                           |

## How it decides

- Growth is measured from the billed-context series itself, not by
  counting turns - transcripts interleave prompts with injected
  reminders and tool results, and the series has no such ambiguity. The
  series is cut at every compaction so the drop never reads as negative
  growth.
- The warning sits a reserve below the target: two turns of growth at
  the measured rate plus half again for slack, floored at 60,000 tokens
  so a quiet session still gets room to write, and capped at a quarter
  of the budget - or that same floor, where the floor is larger - so a
  fast session is never warned beside a half-filled bar.
- The target and warning point latch downward per session; only a
  compaction releases them. The countdowns still track the live rate -
  "two turns left" is meant to react - but the thresholds hold still.
- A compaction is a drop that frees at least half of what a compaction
  restarts at; smaller dips (an expired cache block, a tool result
  leaving the window) do not reset the growth measurement.
- A failed API call is written to the transcript with a zeroed usage
  block; it is skipped, not read as a context of zero.
- Subagents are skipped: their context is short-lived and cannot
  compact, so a warning there gives you nothing to act on.

## Status line (optional, one manual step)

A plugin cannot set the status line - that setting lives in your
personal config - so this one is wired by hand. Add to
`~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "python3 \"$(ls ~/.claude/plugins/cache/*/claude-handoff/*/scripts/statusline.py | sort -V | tail -1)\""
  }
}
```

An installed plugin lives at
`~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/` - the
version is in the path, so the glob keeps the line working across an
upgrade, and `sort -V` keeps 0.10.0 ahead of 0.9.0. Running the plugin
from a git clone instead? Point the command at
`/path/to/clone/scripts/statusline.py`. The status line reads the growth rate
and target from a file the hook writes each turn, so the two never
disagree; without the hook it falls back to a default rate and still
works. The directory it names is the one Claude Code started in, so a
`cd` deeper in the tree - into the handoff folder itself, say - leaves
the field alone. Once the session enters a handoff thread - `hq
adopt`, `hq begin`, or `hq open`, each counted only when it succeeds -
the line names that thread's slug after the directory,
`myproject:auth-token`. It holds until the session enters another
thread or `hq done` finishes that one. The slug is keyed by session
id, so two sessions in one checkout each show their own thread, and it
sits last on the line because it is the field a narrow pane can most
afford to cut.

## Update

```
/plugin marketplace update claude-handoff
```

That updates the plugin source. The plugin itself runs from a copy
taken at install time under `~/.claude/plugins/cache/`, so the clone is
the source and the copy is what executes. There is no migration either
way: the state under `~/.claude/cache/claude-handoff/` is per session
and disposable, and handoff folders are plain markdown and tab-separated
text.

## Uninstall

```
/plugin uninstall claude-handoff@claude-handoff
/plugin marketplace remove claude-handoff
```

Two things outlive it: the `statusLine` block above (remove it from
settings.json) and the cache directory
(`rm -rf ~/.claude/cache/claude-handoff`). Handoffs under `.handoff/`
belong to the project, not the plugin.

## Development

```
python3 -m pytest -q
poetry run mutmut run
```

The suite runs under python3.11 and python3; mutmut mutates `hq.py` and
the two handoff hooks against the tests `pyproject.toml` selects, with
results under `mutants/`. State lives under
`~/.claude/cache/claude-handoff/`: `<session>.json` for the gauge,
`<session>.handoff.json` for the two handoff hooks, and
`hq-reads-<session>.txt` for read receipts; all of it is safe to delete.
The README's charts are generated by `python3 docs/charts.py`.

## License

MIT
