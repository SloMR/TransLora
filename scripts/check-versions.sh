#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
#  check-versions.sh — keeps every file recording a component's version in
#  agreement, and verifies a release tag against them.
#
#  Version sources:
#    web   web/package.json, web/package-lock.json (top level + root package entry)
#    cli   cli/pyproject.toml, cli/translora.py (__version__)
# ─────────────────────────────────────────────────────────────

set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

COMPONENTS="web cli"

usage() {
    cat <<EOF
Usage: $(basename "$0") [COMPONENT/VERSION]

  No argument   Check that every version file of every component agrees.
  COMPONENT/VERSION
                Also check that the tag's version matches (e.g. web/0.5.0).
EOF
}

# Prints one "<file><TAB><version>" line per file recording $1's version;
# an empty version means the marker was not found.
versions_for() {
    case "$1" in
    web)
        python3 - <<'PY'
import json

pkg = json.load(open("web/package.json", encoding="utf-8"))
lock = json.load(open("web/package-lock.json", encoding="utf-8"))
print("web/package.json\t%s" % pkg.get("version", ""))
print("web/package-lock.json\t%s" % lock.get("version", ""))
print('web/package-lock.json packages[""]\t%s'
      % lock.get("packages", {}).get("", {}).get("version", ""))
PY
        ;;
    cli)
        python3 - <<'PY'
import re
from pathlib import Path

sources = (
    ("cli/pyproject.toml", r'^version\s*=\s*["\'](.+?)["\']'),
    ("cli/translora.py", r'^__version__\s*=\s*["\'](.+?)["\']'),
)
for path, pattern in sources:
    found = re.search(pattern, Path(path).read_text(encoding="utf-8"), re.M)
    print("%s\t%s" % (path, found.group(1) if found else ""))
PY
        ;;
    *)
        echo "❌ Unknown component '$1' (expected one of: $COMPONENTS)" >&2
        return 1
        ;;
    esac
}

# check_component COMPONENT [EXPECTED]
# Without EXPECTED, the first version file sets the expectation.
check_component() {
    local component="$1" expected="${2:-}"
    local entries file version failed=0

    entries=$(versions_for "$component") || return 1

    while IFS=$'\t' read -r file version; do
        [[ -z "$file" ]] && continue
        if [[ -z "$version" ]]; then
            echo "❌ $component: no version found in $file" >&2
            failed=1
        elif [[ -z "$expected" ]]; then
            expected="$version"
        elif [[ "$version" != "$expected" ]]; then
            echo "❌ $component: $file is $version, expected $expected" >&2
            failed=1
        fi
    done <<<"$entries"

    if [[ -z "$expected" ]]; then
        echo "❌ $component: no version files found" >&2
        return 1
    fi

    [[ "$failed" -eq 0 ]] && echo "✅ $component $expected"
    return "$failed"
}

case "${1:-}" in
-h | --help)
    usage
    exit 0
    ;;
esac

if [[ $# -gt 1 ]]; then
    usage >&2
    exit 2
fi

STATUS=0

if [[ $# -eq 1 ]]; then
    TAG="$1"
    if [[ ! "$TAG" =~ ^[a-z]+/[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "❌ Tag '$TAG' is not COMPONENT/MAJOR.MINOR.PATCH" >&2
        exit 1
    fi
    check_component "${TAG%%/*}" "${TAG#*/}" || STATUS=1
else
    for component in $COMPONENTS; do
        check_component "$component" || STATUS=1
    done
fi

exit "$STATUS"
