# Unattended runs

`hq loop <slug>` runs a thread over many cycles with no one typing. The `/hq-loop` skill sets a thread up for the loop and audits one whose loop stalls, holds, parks or waits. Any thread runs under the loop as it stands.

## Driver

Inside a tmux pane, `hq loop <slug>` starts claude at the project root and opens a small driver pane below it. The driver types `/handoff read <slug>` at each session start. When the context notice asks for a write, it types `/handoff write <slug>`, then `/clear`, then the read again. The loop needs bash 4 or later, `tmux` and `jq`.

`HQ_CLAUDE_CMD` replaces `claude` as the command `hq loop` and `hq run` start, split as a shell splits it. `hq run` adds `/handoff <slug>` as the last argument, and `hq loop` adds `--settings <file>`. A shell function named `claude` is out of reach of a child process, so a wrapper defined that way takes `HQ_CLAUDE_CMD="bash -ic 'claude \"\$@\"' bash"`, which reads `~/.bashrc` on each run.

## Bounds

The loop counts spend in written cycles. Four bounds catch a runaway session:

| Bound               | Effect                                                              |
| ------------------- | ------------------------------------------------------------------- |
| `--max-cycles`      | ends the run after that many written cycles, default 10             |
| write retries       | a write still unfinished after two retries parks the loop and rings |
| subagent tool calls | ring once when they reach 500 in one cycle                          |
| measure probe       | a thread with a probe holds after four cycles without progress      |

No bound keys on how long a session runs.

## Parks and bells

When the session stops with nothing left to do, the loop parks: it types nothing and names the reason in the driver pane. Any prompt, from the user or a peer session, starts the next turn.

These ring the terminal bell, and tmux shows its `!` flag on a loop window that is not the session's current window:

- a question the session ends on
- a permission dialog the loop left open
- a wait after a denied call
- an API error
- a hold

Any other wait, on a file, a job, the session's own background task or another session's lock, prints a line and rings nothing. A wait on a lock more than two hours old rings once, and the driver never takes that lock.

## Ends

The loop ends when the thread is marked done, after the one more handoff `hq loop stop <slug>` asks for, at `--max-cycles`, or when the driver loses its event stream. Each of these ends closes claude, prints `hq loop: <reason>` and rings. A claude that exits on its own ends the loop with no reason line.

## Permissions

At a permission request for any tool but AskUserQuestion and ExitPlanMode, the loop's hook denies the call and tells the session why, so no dialog waits for someone to answer it. Some dialogs show in every mode, such as an `ask` rule or an `rm` of a critical path, and the hook denies those too.

- Under `bypassPermissions` the session may rewrite a denied call only to make it easier for Claude Code to check, such as one command per Bash call with literal paths.
- A subagent reports the denial to its caller, and the main session works another Plan item or asks the user to run the call.
- After three denials in a session, or a few more when subagents ask at once, the next request shows its dialog, which holds the loop until someone answers it.
- AskUserQuestion and ExitPlanMode always show their dialogs.

The allow list, or a mode set through `HQ_CLAUDE_CMD`, still decides what runs unasked. An unattended run needs every command the thread uses in the allow list, or claude started in a mode that never prompts.

## Questions to the user

The session asks only when a step the user did not ask for would weaken security or stability. Such a step:

- changes who or what can reach or act on a system: a password, key, token, permission, tool approval or network rule
- reveals a secret
- could lose data someone needs, or break a system someone relies on, beyond what a revert restores

A request from another Claude session counts as one the user did not make. A step the thread's Directives ask for counts as one the user asked for, so a grant written there at setup goes ahead, unless it changes who or what can reach or act on a system.

Once nothing else is workable, the session also asks the user to run a call the loop denied that a step cannot go on without. The user runs the call, as `! <command>` in the claude pane or in a shell, and replies. After a question the driver types nothing until a prompt or a new session, so a `!` command is safe in between.

The session decides every other question itself, including a choice the work turns up, by the thread's Directives where one applies and on the evidence where none does. It leaves no such question to the user, and the next handoff files each decision by step 2 of `hq help write`.

## Trace log

The loop appends each permission request, with its tool and full input, and each notification, with its text, to `${XDG_STATE_HOME:-~/.local/state}/claude-handoff/<slug>.log`. The log outlives the loop and sits outside the repo, because a command can carry a token.

## Measure

The measure is an opt-in add-on. The `/hq-loop <slug>` skill writes `.handoff/<slug>/probes/hq-measure` with the user from its templates. `hq finish` runs the probe once per cycle and stores what it measured in the cycle's summary row. After four cycles in a row without measured progress, or when the probe reports the finish line met, the loop holds the thread and rings. A held session takes no Plan step. The next prompt, the user's or a peer's, is the ruling, and the next handoff records it. A thread with no probe never stalls.

## Peers

One thread reaches another by a SendMessage to a live session, or by a request file in the other thread's `.handoff/<slug>/.inbox/`. `hq open` lists each inbox file as a request or a reply. The thread answers a request by doing the work it asks for, or with "cannot", and writes the reply into the requester's `.inbox/`. The driver types a wake line into a thread parked on a hold at a new request, and into one parked on a wait at a new request or reply. A thread that is not running reads the file at its next start. The `/hq-loop` skill holds the file naming and the wake rules.
