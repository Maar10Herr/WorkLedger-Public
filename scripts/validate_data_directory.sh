#!/usr/bin/env sh
set -eu

print_resolved=0
if [ "$#" -eq 3 ] && [ "$3" = --print ]; then
    set -- "$1" "$2" data --print
fi
if [ "$#" -eq 4 ]; then
    [ "$4" = --print ] || {
        printf '%s\n' "Usage: validate_data_directory.sh DIRECTORY PROJECT_ROOT [NAME] [--print]" >&2
        exit 2
    }
    print_resolved=1
    set -- "$1" "$2" "$3"
fi
if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
    printf '%s\n' "Usage: validate_data_directory.sh DIRECTORY PROJECT_ROOT [NAME] [--print]" >&2
    exit 2
fi

data_dir=$1
project_root=$2
name=${3:-data}

unsafe() {
    printf 'Unsafe %s directory: %s\n' "$name" "$1" >&2
    exit 1
}

case "$data_dir" in
    ""|/|.|./|..|../*|*/../*|*/..)
        unsafe "must be a non-root path without '..' components"
        ;;
esac

project_root=$(CDPATH= cd -P -- "$project_root" 2>/dev/null && pwd -P) || unsafe "invalid project root"
case "$data_dir" in
    /*) candidate=$data_dir ;;
    *) candidate=$project_root/$data_dir ;;
esac
while [ "$candidate" != / ] && [ "${candidate%/}" != "$candidate" ]; do
    candidate=${candidate%/}
done

# Reject symlinks in every existing component. A symlink here would make the
# later staging/rollback moves depend on a target outside this path.
probe=$candidate
while [ "$probe" != / ]; do
    [ ! -L "$probe" ] || unsafe "symlink components are not allowed"
    next=$(dirname -- "$probe")
    [ "$next" != "$probe" ] || break
    probe=$next
done

existing=$candidate
while [ ! -e "$existing" ] && [ ! -L "$existing" ]; do
    next=$(dirname -- "$existing")
    [ "$next" != "$existing" ] || unsafe "invalid parent path"
    existing=$next
done
[ -d "$existing" ] || unsafe "parent path is not a directory"

if [ -e "$candidate" ]; then
    [ -d "$candidate" ] || unsafe "path is not a directory"
    resolved=$(CDPATH= cd -P -- "$candidate" && pwd -P) || unsafe "invalid path"
else
    resolved=$(CDPATH= cd -P -- "$existing" && pwd -P) || unsafe "invalid parent path"
    suffix=${candidate#"$existing"}
    while [ -n "$suffix" ]; do
        suffix=${suffix#/}
        component=${suffix%%/*}
        if [ "$suffix" = "$component" ]; then
            suffix=
        else
            suffix=${suffix#*/}
        fi
        case "$component" in
            ""|.) ;;
            *) resolved=$resolved/$component ;;
        esac
    done
fi

[ "$resolved" != / ] || unsafe "must not be a filesystem root"
case "$project_root" in
    "$resolved"|"$resolved"/*)
        unsafe "must not be the project directory or one of its parents"
        ;;
esac

if [ "$print_resolved" -eq 1 ]; then
    printf '%s\n' "$resolved"
fi
