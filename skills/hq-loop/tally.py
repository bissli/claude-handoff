"""Known-set tally for an hq loop probe.

Reads one probe run on stdin and prints the remaining and tally tokens
for the summary line:

    python3 "$HQ_TALLY" <state file> <remaining>[@<step>] <tally> < unit lines

Notes
-----
- K, the known set, never shrinks. A unit enters it on its first open
  line, unless a run named it closed first: a unit seen closed and then
  open is a regression, which counts in R and takes one from T.
- A known unit the run does not name keeps its last state, so a crash
  or a masking error never closes it. A grep probe names each clean
  file in its definition as closed.
"""

import decimal
import os
import pathlib
import re
import sys

_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
_STEP = re.compile(r'[0-9]+(?:\.[0-9]+)?')
_LINE = re.compile(r'(open|closed)(?::([0-9]+))?[ \t]+([^\s](?:[^\t]*[^\s])?)')
_HEADER = 'id\tadmitted\tseen_closed\tlast\tsites'


def read_state(state_path: pathlib.Path) -> dict[str, dict]:
    """Units from a state file, keyed by id; empty when the file is absent.

    Parameters
    ----------
    state_path : pathlib.Path
        The tab-separated state file this script writes.

    Returns
    -------
    dict[str, dict]
        Per id: ``admitted`` and ``seen_closed`` as bool, ``last`` as
        ``open`` or ``closed``, ``sites`` as int.

    Raises
    ------
    ValueError
        The header differs, or a row is not five fields in the written
        form. The message names the file and line.
    """
    if not state_path.is_file():
        return {}
    lines = state_path.read_text(encoding='utf-8').splitlines()
    if not lines or lines[0] != _HEADER:
        raise ValueError(f'{state_path}:1 is no tally header')
    unit_by_id: dict[str, dict] = {}
    for line_no, line in enumerate(lines[1:], start=2):
        fields = line.split('\t')
        if (len(fields) != 5 or fields[1] not in {'0', '1'}
            or fields[2] not in {'0', '1'}
            or fields[3] not in {'open', 'closed'}
            or not fields[4].isdigit()):
            raise ValueError(f'{state_path}:{line_no} is no tally row')
        unit_by_id[fields[0]] = {
            'admitted': fields[1] == '1', 'seen_closed': fields[2] == '1',
            'last': fields[3], 'sites': int(fields[4]),
            }
    return unit_by_id


def apply_run(unit_by_id: dict[str, dict], run_text: str) -> None:
    """Fold one probe run into the units, in place.

    Parameters
    ----------
    unit_by_id : dict[str, dict]
        Units as ``read_state`` returns them. Changed only when the run
        is whole.
    run_text : str
        One unit per line: ``open <id>``, ``open:<n> <id>`` for an open
        unit holding n sites, or ``closed <id>``, then a last line
        ``end``. Blank lines are skipped.

    Raises
    ------
    ValueError
        A line of no form, a repeated id, ``closed:<n>``, ``open:0``, or
        no ``end`` line last.
    """
    lines = [line.strip() for line in run_text.splitlines() if line.strip()]
    if not lines or lines[-1] != 'end':
        raise ValueError(
            'the run has no end line - the probe prints end last, only after'
            ' its tool exits cleanly')
    run_by_id: dict[str, tuple[str, int]] = {}
    for line in lines[:-1]:
        line_m = _LINE.fullmatch(line)
        if (not line_m or (line_m.group(1) == 'closed' and line_m.group(2))
            or int(line_m.group(2) or 1) == 0):
            raise ValueError(
                f'line {line!r} matches no form - open <id>, open:<n> <id>'
                ' with n above 0, closed <id>, end')
        unit_id = line_m.group(3)
        if unit_id in run_by_id:
            raise ValueError(f'id {unit_id!r} appears twice in the run')
        run_by_id[unit_id] = (line_m.group(1), int(line_m.group(2) or 1))
    for unit_id, (run_state, sites) in run_by_id.items():
        unit = unit_by_id.setdefault(unit_id, {
            'admitted': False, 'seen_closed': False, 'last': 'closed', 'sites': 0,
            })
        if run_state == 'open':
            if not unit['seen_closed']:
                unit['admitted'] = True
            unit.update(last='open', sites=sites)
        else:
            unit.update(seen_closed=True, last='closed')


def tokens(
        unit_by_id: dict[str, dict], remaining: str, tally: str, step: str) -> str:
    r"""The summary tokens for the units.

    Parameters
    ----------
    unit_by_id : dict[str, dict]
        Units after the run.
    remaining : str
        Name of the counting token; its goal is 0.
    tally : str
        Name of the tally token.
    step : str
        ``@<step>`` put on both tokens, or ``''`` for none.

    Returns
    -------
    str
        ``<remaining>=<R>->0<step> <tally>=+<T><step>``, where::

            R = sites of every unit whose last state is open
            T = |K \ O| - |O \ K|

        K the admitted units and O the open ones, so T can be negative,
        as in ``tally=+-1``.
    """
    open_units = [unit for unit in unit_by_id.values() if unit['last'] == 'open']
    remaining_cnt = sum(unit['sites'] for unit in open_units)
    closed_known_cnt = sum(
        unit['admitted'] and unit['last'] == 'closed' for unit in unit_by_id.values())
    regression_cnt = sum(not unit['admitted'] for unit in open_units)
    tally_cnt = closed_known_cnt - regression_cnt
    return f'{remaining}={remaining_cnt}->0{step} {tally}=+{tally_cnt}{step}'


def main(argv: list[str]) -> int:
    """Update the state file from stdin and print the tokens.

    Parameters
    ----------
    argv : list[str]
        ``<state file> <remaining>[@<step>] <tally>``, the step a decimal
        above 0.

    Returns
    -------
    int
        0 after printing the tokens; 2 on bad arguments, a bad run or a
        bad state file, with the cause printed and nothing written.
    """
    if len(argv) != 3:
        print('tally: takes <state file> <remaining>[@<step>] <tally>')
        return 2
    state_path = pathlib.Path(argv[0])
    remaining, _, step = argv[1].partition('@')
    tally = argv[2]
    names_ok = bool(_NAME.fullmatch(remaining) and _NAME.fullmatch(tally))
    if not names_ok or remaining == tally:
        print('tally: <remaining> and <tally> are two distinct summary names')
        return 2
    if '@' in argv[1] and not (_STEP.fullmatch(step) and decimal.Decimal(step) > 0):
        print(f'tally: step {step!r} is no decimal above 0')
        return 2
    try:
        unit_by_id = read_state(state_path)
        apply_run(unit_by_id, sys.stdin.read())
    except ValueError as exc:
        print(f'tally: {exc}')
        return 2
    rows = [_HEADER] + [
        f'{unit_id}\t{int(unit["admitted"])}\t{int(unit["seen_closed"])}'
        f'\t{unit["last"]}\t{unit["sites"]}'
        for unit_id, unit in sorted(unit_by_id.items())
        ]
    state_path.parent.mkdir(parents=True, exist_ok=True)
    staged = state_path.with_name(state_path.name + '.tmp')
    staged.write_text('\n'.join(rows) + '\n', encoding='utf-8')
    os.replace(staged, state_path)
    print(tokens(unit_by_id, remaining, tally, f'@{step}' if step else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
