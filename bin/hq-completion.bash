# Bash completion for hq: the verb, then an open slug under the
# .handoff/ of HQ_ROOT, else of the git top level, else of the cwd.
_hq() {
    local cur=${COMP_WORDS[COMP_CWORD]}
    if [ "$COMP_CWORD" -eq 1 ]; then
        COMPREPLY=($(compgen -W 'run list read open begin finish done note
            stamp supersede adopt when diff arc artifacts standing work-dir
            help' -- "$cur"))
        return
    fi
    [ "$COMP_CWORD" -eq 2 ] || return
    case ${COMP_WORDS[1]} in list|help) return ;; esac
    local root=${HQ_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}
    local dir slugs=()
    for dir in "$root"/.handoff/*/; do
        [ -d "$dir" ] && [ ! -e "$dir.hq.done" ] || continue
        dir=${dir%/}
        slugs+=("${dir##*/}")
    done
    COMPREPLY=($(compgen -W "${slugs[*]}" -- "$cur"))
}
complete -F _hq hq
