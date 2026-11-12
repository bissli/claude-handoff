# Measure Probe Templates

Each template is a starting `probes/hq-measure`, adapted to the thread
with the user. `hq finish` runs the probe once per cycle, in the
thread's work dir else the root, with stdin `/dev/null`, `HQ_MEASURE=1`
and `HQ_TALLY` set, under a cap of 60 seconds. The last non-blank line
of stdout is the token line.

Rules every probe keeps:

- The probe holds every goal and pattern it reads. Nothing outside it
  moves a goal.
- It prints the same names every run, 0 included, since a row of new
  names counts as no progress.
- A tool that fails makes the probe exit nonzero, with the cause as the
  last line of stderr. Exit 3 marks the finish line met.
- State the probe keeps changes only under `HQ_MEASURE=1`.

## Tally

For work whose open count can rise as the work finds more of it. Each
unit prints one line, `open:<n> <id>` for an open unit holding n sites,
`open <id>` for one site, or `closed <id>`, then `end` once the tool has
exited cleanly. A unit the state file holds and the listing lacks, such
as a deleted file, prints as closed. `tally.py` keeps the known set and
prints the remaining count and the tally of closed units, with the step
on both.

```sh
#!/bin/sh
set -e
state="$(dirname "$0")/tally.tsv"
if [ "$HQ_MEASURE" != 1 ]; then
    scratch=$(mktemp -d)
    trap 'rm -rf "$scratch"' EXIT
    [ ! -f "$state" ] || cp "$state" "$scratch/tally.tsv"
    state="$scratch/tally.tsv"
fi
files=$(git ls-files 'src/*.py' | while IFS= read -r file; do
    [ ! -f "$file" ] || printf '%s\n' "$file"
done)
units=$(printf '%s\n' "$files" | while IFS= read -r file; do
    [ -n "$file" ] || continue
    sites=$(grep -c 'TODO' "$file") || [ $? -eq 1 ] || exit 1
    if [ "$sites" -gt 0 ]; then
        echo "open:$sites $file"
    else
        echo "closed $file"
    fi
done)
gone=$([ ! -f "$state" ] || tail -n +2 "$state" | cut -f1 |
    grep -vxF -e "$files" | sed 's/^/closed /')
line=$(printf '%s\n%s\nend\n' "$units" "$gone" |
    python3 "$HQ_TALLY" "$state" open_sites@1 sites_closed) ||
    { echo "$line" >&2; exit 1; }
echo "$line"
```

## Pass or Fail

For a suite whose failing count is the measure. The suite runs once,
and only when it fails, its failed and erroring tests run once more. A
test counts as failing only when it fails both times. The rerun takes
its list from pytest's cache, so the cache plugin stays on. A run that
does not complete exits nonzero, which stores a failed row.

```sh
#!/bin/sh
out=$(mktemp)
trap 'rm -f "$out"' EXIT
python3 -m pytest -q -rfE >"$out" 2>&1
rc=$?
[ "$rc" -eq 0 ] && { echo 'failing=0->0'; exit 0; }
[ "$rc" -eq 1 ] || { tail -1 "$out" >&2; exit 1; }
python3 -m pytest -q -rfE --lf >"$out" 2>&1
rc=$?
[ "$rc" -le 1 ] || { tail -1 "$out" >&2; exit 1; }
echo "failing=$(grep -cE '^(FAILED|ERROR) ' "$out")->0"
```

## Custom Score

For a score that rises toward a goal, such as coverage. The step is the
smallest gain that counts, and the value is capped at its goal.

```sh
#!/bin/sh
value=$(python3 -m coverage report --format=total --fail-under=0) ||
    { [ -z "$value" ] || printf '%s\n' "$value" | tail -1 >&2; exit 1; }
awk -v value="$value" -v goal=90 'BEGIN {
    if (value > goal) value = goal
    printf "score=%s->%s@1\n", value, goal
}'
```

## Slow Measure

For a measure that runs past the cap. When no run is going, the probe
starts one detached in its own session, with every stream sent to a
file. It prints each finished run's result once, at the first probe
after the run finishes, and prints nothing otherwise, which stores an
unmeasured row. A run that fails makes that probe exit nonzero with the
run's last stderr line. The stall then counts finished runs, however
long each takes.

```sh
#!/bin/sh
[ "$HQ_MEASURE" = 1 ] || exit 0
dir="$(dirname "$0")/slow"
mkdir -p "$dir"
running=0
[ -f "$dir/pid" ] && kill -0 "$(cat "$dir/pid")" 2>/dev/null && running=1
if [ "$running" -eq 0 ]; then
    setsid sh -c '
        echo $$ >"$1/pid"
        if ./run-benchmark --summary >"$1/result.new" 2>"$1/stderr"; then
            mv "$1/result.new" "$1/result"
        else
            mv "$1/stderr" "$1/failed"
        fi
        touch "$1/done"
        rm -f "$1/pid"
    ' sh "$dir" </dev/null >/dev/null 2>&1 &
fi
[ -f "$dir/done" ] || exit 0
rm -f "$dir/done"
if [ -f "$dir/failed" ]; then
    tail -1 "$dir/failed" >&2
    rm -f "$dir/failed"
    exit 1
fi
cat "$dir/result"
```

`./run-benchmark --summary` stands for the slow command. It prints the
token line, such as `p95_ms=340->250@5`.
