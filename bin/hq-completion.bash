# Bash completion for hq: the verb, then an open slug under the
# .handoff/ of HQ_ROOT, else of the git top level, else of the cwd.
# hq loop also takes stop ahead of the slug, and skips --max-cycles N.
_hq() {
    local cur=${COMP_WORDS[COMP_CWORD]}
    if [ "$COMP_CWORD" -eq 1 ]; then
        COMPREPLY=($(compgen -W 'run loop list read open begin finish done note
            stamp supersede adopt when diff arc artifacts standing work-dir
            help' -- "$cur"))
        return
    fi
    local extra=
    case ${COMP_WORDS[1]}:$COMP_CWORD in
        list:* | help:*) return ;;
        loop:*)
            local i=2 args=()
            while [ "$i" -lt "$COMP_CWORD" ]; do
                if [ "${COMP_WORDS[i]}" = --max-cycles ]; then
                    i=$((i + 2))
                else
                    args+=("${COMP_WORDS[i]}")
                    i=$((i + 1))
                fi
            done
            [ "$i" -eq "$COMP_CWORD" ] || return
            case ${#args[@]}:${args[0]:-} in
                0:) extra=stop ;;
                1:stop) ;;
                *) return ;;
            esac
            ;;
        *:2) ;;
        *) return ;;
    esac
    local root=${HQ_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}
    local dir slugs=()
    for dir in "$root"/.handoff/*/; do
        [ -d "$dir" ] && [ ! -e "$dir.hq.done" ] || continue
        dir=${dir%/}
        slugs+=("${dir##*/}")
    done
    COMPREPLY=($(compgen -W "$extra ${slugs[*]}" -- "$cur"))
}
complete -F _hq hq
