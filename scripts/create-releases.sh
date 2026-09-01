#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
#  create-releases.sh
#  Creates GitHub Releases with categorized changelogs.
#  Tag format: component/version  (e.g. web/0.1.0, cli/0.1.0)
#
#  Usage:
#    ./scripts/create-releases.sh                       # dry-run all tags
#    ./scripts/create-releases.sh --apply               # create all missing releases
#    ./scripts/create-releases.sh --apply --limit 5     # latest 5 tags only
#    ./scripts/create-releases.sh --apply --tag web/0.1.0  # single tag (used by CI)
#
#  Setup:
#    1. Install GitHub CLI: https://cli.github.com
#    2. Authenticate: gh auth login
#    3. Run from the repo root
#
#  How it works:
#    - Commits are filtered by component: only commits whose scope
#      matches the tag's component are included in that release.
#    - Scope mapping:
#        web:  Web:
#        cli:  CLI:
#    - Shared scopes (CI:, Docs:, Script:) are assigned to a component
#      by keyword detection in the description.
#    - Commits are categorized into sections (Features, Fixes, etc.)
#      by matching keywords in the description.
#    - Contributors are deduplicated by email. GitHub usernames are
#      auto-detected from noreply emails. Bots are excluded.
#
#  To add a new component:
#    1. Add its scope(s) to commit_belongs_to_component()
#    2. Add keyword hints for shared scopes (CI:, Docs:, etc.)
#    3. Add keyword hints for unscoped commits
#    4. Optionally add a case to component_emoji()/component_display_name()
# ─────────────────────────────────────────────────────────────

set -euo pipefail

# ═════════════════════════════════════════════════════════════
#  CLI ARGUMENTS
# ═════════════════════════════════════════════════════════════

DRY_RUN=true
TAG_LIMIT=0
SINGLE_TAG=""

usage() {
    cat <<EOF
Usage: $(basename "$0") [--apply] [--limit N] [--tag COMPONENT/VERSION]

  --apply              Create releases (default is dry-run)
  --limit N            Process only the latest N tags (N must be a positive integer)
  --tag TAG            Process a single tag (e.g. web/0.1.0)
  -h, --help           Show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
    --apply)
        DRY_RUN=false
        shift
        ;;
    --limit)
        if [[ $# -lt 2 || -z "${2:-}" ]]; then
            echo "❌ --limit requires a value" >&2
            usage >&2
            exit 2
        fi
        if ! [[ "$2" =~ ^[0-9]+$ ]]; then
            echo "❌ --limit must be a non-negative integer, got: $2" >&2
            exit 2
        fi
        TAG_LIMIT="$2"
        shift 2
        ;;
    --tag)
        if [[ $# -lt 2 || -z "${2:-}" ]]; then
            echo "❌ --tag requires a value" >&2
            usage >&2
            exit 2
        fi
        SINGLE_TAG="$2"
        shift 2
        ;;
    -h | --help)
        usage
        exit 0
        ;;
    *)
        echo "❌ Unknown argument: $1" >&2
        usage >&2
        exit 2
        ;;
    esac
done

# ═════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═════════════════════════════════════════════════════════════

# Repo URL: prefer gh, fallback to git remote
REPO_URL="${REPO_URL:-$(gh repo view --json url --jq '.url' 2>/dev/null ||
    git remote get-url origin 2>/dev/null ||
    git remote | head -1 | xargs git remote get-url 2>/dev/null ||
    echo "")}"
REPO_URL="${REPO_URL%.git}"
REPO_URL="${REPO_URL/git@github.com:/https://github.com/}"

# Display metadata per component (add new components here)
component_emoji() {
    case "$1" in
    web) echo "🌐" ;;
    cli) echo "🐍" ;;
    *) echo "📦" ;;
    esac
}

component_display_name() {
    case "$1" in
    web) echo "Web" ;;
    cli) echo "CLI" ;;
    *) echo "$1" ;;
    esac
}

# Caps on how much history one release scans; both are reported when hit.
MAX_COMMITS=100
MAX_AUTHOR_COMMITS=500

# ═════════════════════════════════════════════════════════════
#  AUTHOR HELPERS
# ═════════════════════════════════════════════════════════════

# Bot detection — pattern-based, no hardcoded names
is_bot_author() {
    local name="$1" email="${2:-}"
    local lc_name lc_email
    lc_name=$(echo "$name" | tr '[:upper:]' '[:lower:]')
    lc_email=$(echo "$email" | tr '[:upper:]' '[:lower:]')

    [[ "$lc_name" == *"[bot]"* ]] && return 0
    [[ "$lc_name" == *"-bot" ]] && return 0
    [[ "$lc_name" == "bot-"* ]] && return 0
    [[ "$lc_name" == "github-actions" ]] && return 0
    [[ "$lc_name" == "dependabot" ]] && return 0
    [[ "$lc_email" == *"[bot]"* ]] && return 0
    [[ "$lc_email" == *"bot@"* ]] && return 0
    [[ "$lc_email" == "action@github.com" ]] && return 0
    [[ "$lc_email" == "noreply@github.com" ]] && return 0
    return 1
}

# macOS ships bash 3.2, which has no associative arrays: each map is instead a
# newline-separated list of "key<TAB>value" records.
# shellcheck disable=SC2034  # read indirectly via ${!1} in map_get/map_set
AUTHOR_CANONICAL="" # email -> canonical name
# shellcheck disable=SC2034
AUTHOR_GH_USER=""   # email -> GitHub username
# shellcheck disable=SC2034
NAME_TO_EMAIL=""    # name  -> email

# map_get <map-var-name> <key> — prints the value, or nothing if absent.
map_get() {
    local key="$2" k v
    while IFS=$'\t' read -r k v; do
        if [[ "$k" == "$key" ]]; then
            printf '%s' "$v"
            return 0
        fi
    done <<<"${!1}"
    return 0
}

# map_set <map-var-name> <key> <value> — inserts or replaces.
map_set() {
    local key="$2" value="$3" out="" k v
    while IFS=$'\t' read -r k v; do
        [[ -z "$k" || "$k" == "$key" ]] && continue
        out+="${k}"$'\t'"${v}"$'\n'
    done <<<"${!1}"
    out+="${key}"$'\t'"${value}"$'\n'
    printf -v "$1" '%s' "$out"
}

build_author_map() {
    local range="$1" fallback="${2:-}"
    local log_data total
    if [[ -n "$range" ]]; then
        log_data=$(git log --format="%an|%ae" --no-merges "$range" 2>/dev/null)
    else
        log_data=$(git log --format="%an|%ae" --no-merges "$fallback" 2>/dev/null)
    fi

    total=$(printf '%s\n' "$log_data" | grep -c '[^[:space:]]' || true)
    if [[ $total -gt $MAX_AUTHOR_COMMITS ]]; then
        echo "ℹ️  ${total} commits in range — scanning the newest ${MAX_AUTHOR_COMMITS} for contributor identities." >&2
        # sed, not `head`: head closes the pipe early and pipefail turns the SIGPIPE into an abort.
        log_data=$(printf '%s\n' "$log_data" | sed -n "1,${MAX_AUTHOR_COMMITS}p")
    fi

    while IFS= read -r entry; do
        [[ -z "$entry" ]] && continue
        local name email
        name=$(echo "$entry" | cut -d'|' -f1)
        email=$(echo "$entry" | cut -d'|' -f2)
        is_bot_author "$name" "$email" && continue

        # Keep the longest name per email as canonical
        local existing
        existing=$(map_get AUTHOR_CANONICAL "$email")
        if [[ -z "$existing" ]] || [[ ${#name} -gt ${#existing} ]]; then
            map_set AUTHOR_CANONICAL "$email" "$name"
        fi

        # Extract GitHub username from noreply: 12345+user@users.noreply.github.com
        if [[ "$email" == *"@users.noreply.github.com" ]]; then
            local gh_user
            gh_user=$(echo "$email" | sed -E 's/^[0-9]+\+//' | sed 's/@users\.noreply\.github\.com$//')
            [[ -n "$gh_user" ]] && map_set AUTHOR_GH_USER "$email" "$gh_user"
        fi

        map_set NAME_TO_EMAIL "$name" "$email"
    done <<<"$log_data"
}

normalize_author() {
    local author="$1" email canonical
    email=$(map_get NAME_TO_EMAIL "$author")
    is_bot_author "$author" "$email" && {
        echo ""
        return
    }
    if [[ -n "$email" ]]; then
        canonical=$(map_get AUTHOR_CANONICAL "$email")
        echo "${canonical:-$author}"
    else
        echo "$author"
    fi
}

author_github_username() {
    local email
    email=$(map_get NAME_TO_EMAIL "$1")
    [[ -z "$email" ]] && return 0
    map_get AUTHOR_GH_USER "$email"
}

# ═════════════════════════════════════════════════════════════
#  COMPONENT FILTERING
# ═════════════════════════════════════════════════════════════

# Keywords that identify a commit as belonging to a specific component.
# Used for shared scopes (CI:, Docs:, etc.) and unscoped commits.
# Word boundaries, not substrings: "cli" matched "client", "pip" matched "pipeline".
WEB_KEYWORDS_RE='(^|[^a-z])(node|npm|angular|web|webapp|client|typescript|ts|scss|css|html|browser|chrome|eslint|prettier|jszip|favicon|component|service|signal|inter font)([^a-z]|$)'
CLI_KEYWORDS_RE='(^|[^a-z])(python|cli|translora|pip|httpx|terminal|asyncio|srt_parser|translator\.py|batch_runner|live_status|time_tracker|languages)([^a-z]|$)'

has_web_keywords() { [[ "$1" =~ $WEB_KEYWORDS_RE ]]; }
has_cli_keywords() { [[ "$1" =~ $CLI_KEYWORDS_RE ]]; }

# Returns 0 if commit belongs to the component, 1 if not
commit_belongs_to_component() {
    local msg="$1" component="$2"
    local lc
    lc=$(echo "$msg" | tr '[:upper:]' '[:lower:]')

    # Extract scope prefix (e.g. "Web" from "Web: Fix bug")
    local scope=""
    if [[ "$msg" =~ ^([a-zA-Z][a-zA-Z0-9/]*):\ ? ]]; then
        scope=$(echo "${BASH_REMATCH[1]}" | tr '[:upper:]' '[:lower:]')
    fi

    # Direct scope → component
    case "$scope" in
    web) [[ "$component" == "web" ]] && return 0 || return 1 ;;
    cli) [[ "$component" == "cli" ]] && return 0 || return 1 ;;
    esac

    # Shared scopes — route by keyword hints in description
    case "$scope" in
    ci | ci/cd | docker | docs | doc | script | scripts | all | ide | vscode | poc | pr)
        local desc
        desc=$(echo "$lc" | sed -E 's/^[a-z][a-z0-9/]*:\s*//')

        local hint_web=false hint_cli=false
        has_web_keywords "$desc" && hint_web=true
        has_cli_keywords "$desc" && hint_cli=true

        if $hint_web || $hint_cli; then
            case "$component" in
            web) $hint_web && return 0 || return 1 ;;
            cli) $hint_cli && return 0 || return 1 ;;
            esac
            return 1
        fi
        return 0 # No hints — truly shared
        ;;
    esac

    # No recognized scope — match by keywords in full message
    case "$component" in
    web) has_web_keywords "$lc" && return 0 ;;
    cli) has_cli_keywords "$lc" && return 0 ;;
    esac
    return 1
}

# ═════════════════════════════════════════════════════════════
#  COMMIT CATEGORIZATION
# ═════════════════════════════════════════════════════════════

# Shared keyword classifier for descriptions
classify_description() {
    local d="$1"
    case "$d" in
    add\ * | add[es]\ * | added\ * | adding\ * | implement* | introduc* | creat* | enabl* | support* | allow*) echo "FEATURE" ;;
    feat* | new\ * | enhance* | extend* | provid* | integrat*) echo "FEATURE" ;;
    fix* | bug* | patch* | resolv* | correct* | repair* | address*) echo "FIX" ;;
    handl* | close\ * | closes\ * | workaround* | hotfix*) echo "FIX" ;;
    improv* | optim* | speed* | perf* | fast* | cache* | batch* | async*) echo "PERF" ;;
    refactor* | clean* | restructur* | simplif* | split* | extract*) echo "REFACTOR" ;;
    convert* | reorganiz* | modulariz* | mov* | renam* | replac* | merg*) echo "REFACTOR" ;;
    rewrit* | rework* | decouple* | abstract*) echo "REFACTOR" ;;
    break* | remov* | delet* | deprecat* | drop* | disabl* | sunset*) echo "REFACTOR" ;;
    dock*) echo "CI" ;;
    doc* | readme* | comment* | typo* | changelog* | licens* | contribut*) echo "DOCS" ;;
    spell* | grammar* | translat* | i18n* | l10n*) echo "DOCS" ;;
    test* | spec* | coverage* | assert* | mock* | stub* | e2e* | unit\ *) echo "TEST" ;;
    ci\ * | ci:* | cd\ * | action* | workflow* | deploy*) echo "CI" ;;
    pipeline* | container* | k8s* | kubernetes* | helm* | terraform*) echo "CI" ;;
    build* | dep* | bump* | upgrad* | updat* | chore* | migrat* | pin\ *) echo "CHORE" ;;
    lockfile* | vendor* | npm* | cargo* | pod* | packag* | modul*) echo "CHORE" ;;
    releas* | version* | tag* | publish* | config* | setup* | init*) echo "CHORE" ;;
    style* | lint* | format* | prettier* | indent* | whitespace*) echo "STYLE" ;;
    eslint* | clippy* | rustfmt* | gofmt* | black*) echo "STYLE" ;;
    ui\ * | ui:* | design* | layout* | css* | scss* | animat* | theme*) echo "UI" ;;
    responsive* | accessib* | a11y* | color* | font* | icon* | visual*) echo "UI" ;;
    security* | cve* | vuln* | encrypt* | sanitiz* | harden* | block*) echo "SECURITY" ;;
    auth* | permiss* | restrict* | ssl* | tls* | cert* | token* | secret*) echo "SECURITY" ;;
    csrf* | xss* | inject* | escap*) echo "SECURITY" ;;
    revert*) echo "REVERT" ;;
    *) echo "OTHER" ;;
    esac
}

categorize_commit() {
    local msg="$1"
    local lc
    lc=$(echo "$msg" | tr '[:upper:]' '[:lower:]')

    # Skip merge commits
    [[ "$lc" == merge\ pull\ request* || "$lc" == merge\ branch* ||
        "$lc" == merge\ remote* || "$lc" == merge\ tag* ]] && {
        echo "SKIP"
        return
    }

    # Layer 1: Conventional Commits — type(scope)!: description
    local cc_regex='^([a-z]+)(\([^)]*\))?(!)?\:\ ?(.*)'
    if [[ "$lc" =~ $cc_regex ]]; then
        case "${BASH_REMATCH[1]}" in
        feat | feature)
            echo "FEATURE"
            return
            ;;
        fix | bugfix | hotfix | patch)
            echo "FIX"
            return
            ;;
        perf | performance)
            echo "PERF"
            return
            ;;
        refactor | breaking | remove | drop)
            echo "REFACTOR"
            return
            ;;
        docs | doc | documentation)
            echo "DOCS"
            return
            ;;
        test | tests | spec)
            echo "TEST"
            return
            ;;
        ci | cd | pipeline)
            echo "CI"
            return
            ;;
        build | deps | dep | chore)
            echo "CHORE"
            return
            ;;
        style | lint | format)
            echo "STYLE"
            return
            ;;
        ui | design | ux)
            echo "UI"
            return
            ;;
        security | sec | auth)
            echo "SECURITY"
            return
            ;;
        revert)
            echo "REVERT"
            return
            ;;
        release | version | bump)
            echo "CHORE"
            return
            ;;
        esac
    fi

    # Layer 2: Scoped format — AnyWord: description
    if [[ "$msg" =~ ^[a-zA-Z][a-zA-Z0-9/_-]*:\ ? ]]; then
        local desc
        desc=$(echo "$lc" | sed -E 's/^[a-z][a-z0-9/_-]*:\s*//')
        # "ci" scope is always CI
        [[ "$lc" =~ ^ci: ]] && {
            echo "CI"
            return
        }
        classify_description "$desc"
        return
    fi

    # Layer 3: Free-form fallback
    classify_description "$lc"
}

# Strip any "Prefix:" or "type(scope)!:" from display
clean_message() {
    echo "$1" | sed -E 's/^[a-zA-Z][a-zA-Z0-9/_-]*(\([^)]*\))?!?:\s*//'
}

# ═════════════════════════════════════════════════════════════
#  RELEASE BODY BUILDER
# ═════════════════════════════════════════════════════════════

build_release_body() {
    local TAG="$1" PREV_TAG="$2" COMPONENT="$3" VERSION="$4"
    local NAME
    NAME=$(component_display_name "$COMPONENT")

    # Build author identity map
    if [[ -n "$PREV_TAG" ]]; then
        build_author_map "${PREV_TAG}..${TAG}"
    else
        build_author_map "" "$TAG"
    fi

    # Collect commits
    local COMMITS COMMIT_TOTAL OMITTED=0
    if [[ -n "$PREV_TAG" ]]; then
        COMMITS=$(git log --format="%H|%h|%s|%an" --no-merges "${PREV_TAG}..${TAG}" 2>/dev/null)
    else
        COMMITS=$(git log --format="%H|%h|%s|%an" --no-merges "$TAG" 2>/dev/null)
    fi
    COMMIT_TOTAL=$(printf '%s\n' "$COMMITS" | grep -c '[^[:space:]]' || true)
    if [[ $COMMIT_TOTAL -gt $MAX_COMMITS ]]; then
        OMITTED=$((COMMIT_TOTAL - MAX_COMMITS))
        echo "ℹ️  ${TAG}: ${COMMIT_TOTAL} commits in range — listing the newest ${MAX_COMMITS}, ${OMITTED} omitted." >&2
        COMMITS=$(printf '%s\n' "$COMMITS" | sed -n "1,${MAX_COMMITS}p")
    fi

    # Category arrays
    local -a FEATURES=() FIXES=() PERF=() SECURITY=() UI_CHANGES=()
    local -a REFACTORS=() DOCS=() TESTS=() CI_CD=() CHORES=() STYLE=() REVERT=() OTHER=()
    local CONTRIBUTORS=""

    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        local FULL_HASH SHORT_HASH MSG AUTHOR
        FULL_HASH=$(echo "$line" | cut -d'|' -f1)
        SHORT_HASH=$(echo "$line" | cut -d'|' -f2)
        MSG=$(echo "$line" | cut -d'|' -f3)
        AUTHOR=$(echo "$line" | cut -d'|' -f4-)

        commit_belongs_to_component "$MSG" "$COMPONENT" || continue

        local CAT
        CAT=$(categorize_commit "$MSG")
        [[ "$CAT" == "SKIP" ]] && continue

        local ENTRY
        ENTRY="* $(clean_message "$MSG") [\`${SHORT_HASH}\`](${REPO_URL}/commit/${FULL_HASH})"

        case "$CAT" in
        FEATURE) FEATURES+=("$ENTRY") ;;
        FIX) FIXES+=("$ENTRY") ;;
        PERF) PERF+=("$ENTRY") ;;
        SECURITY) SECURITY+=("$ENTRY") ;;
        UI) UI_CHANGES+=("$ENTRY") ;;
        REFACTOR) REFACTORS+=("$ENTRY") ;;
        DOCS) DOCS+=("$ENTRY") ;;
        TEST) TESTS+=("$ENTRY") ;;
        CI) CI_CD+=("$ENTRY") ;;
        CHORE) CHORES+=("$ENTRY") ;;
        STYLE) STYLE+=("$ENTRY") ;;
        REVERT) REVERT+=("$ENTRY") ;;
        *) OTHER+=("$ENTRY") ;;
        esac

        local NORM_AUTHOR
        NORM_AUTHOR=$(normalize_author "$AUTHOR")
        [[ -n "$NORM_AUTHOR" ]] && CONTRIBUTORS+="${NORM_AUTHOR}"$'\n'
    done <<<"$COMMITS"

    # Assemble markdown
    local BODY=""

    emit_section() {
        local title="$1"
        shift
        if [[ $# -eq 0 ]]; then return 0; fi
        BODY+="### ${title}"$'\n\n'
        local entry
        for entry in "$@"; do BODY+="${entry}"$'\n'; done
        BODY+=$'\n'
    }

    # ${arr[@]+...}: a plain "${arr[@]}" on an empty array aborts under set -u on bash 3.2.
    emit_section "🚀 Features" ${FEATURES[@]+"${FEATURES[@]}"}
    emit_section "🐛 Bug Fixes" ${FIXES[@]+"${FIXES[@]}"}
    emit_section "⚡ Performance" ${PERF[@]+"${PERF[@]}"}
    emit_section "🔒 Security" ${SECURITY[@]+"${SECURITY[@]}"}
    emit_section "🎨 UI & Design" ${UI_CHANGES[@]+"${UI_CHANGES[@]}"}
    emit_section "♻️ Refactoring" ${REFACTORS[@]+"${REFACTORS[@]}"}
    emit_section "📝 Documentation" ${DOCS[@]+"${DOCS[@]}"}
    emit_section "🧪 Tests" ${TESTS[@]+"${TESTS[@]}"}
    emit_section "🔧 CI/CD" ${CI_CD[@]+"${CI_CD[@]}"}
    emit_section "📦 Dependencies" ${CHORES[@]+"${CHORES[@]}"}
    emit_section "💅 Code Style" ${STYLE[@]+"${STYLE[@]}"}
    emit_section "⏪ Reverts" ${REVERT[@]+"${REVERT[@]}"}
    emit_section "📌 Other" ${OTHER[@]+"${OTHER[@]}"}

    local TOTAL=$((${#FEATURES[@]} + ${#FIXES[@]} + ${#PERF[@]} + ${#SECURITY[@]} + \
        ${#UI_CHANGES[@]} + ${#REFACTORS[@]} + ${#DOCS[@]} + ${#TESTS[@]} + ${#CI_CD[@]} + \
        ${#CHORES[@]} + ${#STYLE[@]} + ${#REVERT[@]} + ${#OTHER[@]}))

    if [[ $TOTAL -eq 0 ]]; then
        BODY+="*Initial release of ${NAME}.*"$'\n\n'
    fi

    if [[ $OMITTED -gt 0 ]]; then
        BODY+="_${OMITTED} older commit(s) are not listed above — see the full changelog below._"$'\n\n'
    fi

    # Contributors
    local sorted_authors author gh_user
    sorted_authors=$(printf '%s' "$CONTRIBUTORS" | grep -v '^[[:space:]]*$' | sort -u || true)
    if [[ -n "$sorted_authors" ]]; then
        BODY+="---"$'\n\n'
        BODY+="### 👥 Contributors"$'\n\n'
        while IFS= read -r author; do
            gh_user=$(author_github_username "$author")
            if [[ -n "$gh_user" ]]; then
                BODY+="* **${author}** (@${gh_user})"$'\n'
            else
                BODY+="* **${author}**"$'\n'
            fi
        done <<<"$sorted_authors"
        BODY+=$'\n'
    fi

    # Full Changelog link
    BODY+="---"$'\n\n'
    if [[ -n "$PREV_TAG" ]]; then
        BODY+="**Full Changelog**: [\`${PREV_TAG}...${TAG}\`](${REPO_URL}/compare/$(urlencode "$PREV_TAG")...$(urlencode "$TAG"))"$'\n'
    else
        BODY+="**Full Changelog**: [\`${TAG}\`](${REPO_URL}/commits/$(urlencode "$TAG"))"$'\n'
    fi

    echo "$BODY"
}

# Percent-encode everything outside the RFC 3986 unreserved set, not just '/'.
# LC_ALL=C keeps the loop byte-wise so non-ASCII tags encode as UTF-8.
urlencode() {
    local LC_ALL=C
    local s="$1" out="" i c n
    for ((i = 0; i < ${#s}; i++)); do
        c="${s:i:1}"
        case "$c" in
        [a-zA-Z0-9.~_-]) out+="$c" ;;
        *)
            printf -v n '%d' "'$c"
            printf -v c '%%%02X' "$((n & 0xFF))"
            out+="$c"
            ;;
        esac
    done
    printf '%s' "$out"
}

# ═════════════════════════════════════════════════════════════
#  PROCESS A SINGLE TAG
# ═════════════════════════════════════════════════════════════

# All tags version-sorted (needed for prev-tag lookup).
# Populated by load_all_tags, which must be called AFTER `git fetch --tags`
# so PREV_TAG lookup never operates on a stale snapshot.
ALL_TAGS=""

load_all_tags() {
    ALL_TAGS=$(git tag --sort=version:refname)
}

process_tag() {
    local TAG="$1"
    local COMPONENT="${TAG%%/*}"
    local VERSION="${TAG#*/}"
    local EMOJI_CHAR NAME
    EMOJI_CHAR=$(component_emoji "$COMPONENT")
    NAME=$(component_display_name "$COMPONENT")
    local TITLE="${EMOJI_CHAR} ${NAME} v${VERSION}"

    # Find previous tag of same component (literal matching, no regex pitfalls)
    local PREV_TAG=""
    local -a component_tags=()
    local t
    while IFS= read -r t; do
        [[ -z "$t" ]] && continue
        [[ "$t" == "${COMPONENT}/"* ]] && component_tags+=("$t")
    done <<<"$ALL_TAGS"
    local i
    for i in "${!component_tags[@]}"; do
        if [[ "${component_tags[$i]}" == "$TAG" ]]; then
            [[ $i -gt 0 ]] && PREV_TAG="${component_tags[$((i - 1))]}"
            break
        fi
    done

    if $DRY_RUN; then
        echo ""
        echo "┌─────────────────────────────────────────────────────"
        echo "│  🏷️  $TITLE"
        echo "│  Tag:      $TAG"
        echo "│  Previous: ${PREV_TAG:-none (initial release)}"
        echo "└─────────────────────────────────────────────────────"
        echo ""
        build_release_body "$TAG" "$PREV_TAG" "$COMPONENT" "$VERSION"
    else
        echo "🚀 Creating: $TITLE ..."
        local BODY notes_file gh_stderr rc
        BODY=$(build_release_body "$TAG" "$PREV_TAG" "$COMPONENT" "$VERSION")

        # Use a temp notes file: avoids argv length limits on large changelogs
        # and keeps stderr available for diagnostics.
        notes_file=$(mktemp)
        gh_stderr=$(mktemp)
        # shellcheck disable=SC2064
        trap "rm -f '$notes_file' '$gh_stderr'" RETURN
        printf '%s' "$BODY" >"$notes_file"

        set +e
        gh release create "$TAG" --title "$TITLE" --notes-file "$notes_file" --verify-tag 2>"$gh_stderr"
        rc=$?
        set -e

        if [[ $rc -eq 0 ]]; then
            echo "   ✅ Done"
            return 0
        else
            echo "   ❌ Failed (gh exit $rc)"
            if [[ -s "$gh_stderr" ]]; then
                echo "   --- gh stderr ---"
                sed 's/^/   /' "$gh_stderr" >&2
            fi
            return 1
        fi
    fi
}

# ═════════════════════════════════════════════════════════════
#  MAIN
# ═════════════════════════════════════════════════════════════

# Single-tag mode (used by CI)
if [[ -n "$SINGLE_TAG" ]]; then
    load_all_tags
    # Idempotency: if the release already exists, succeed without re-creating.
    # This makes the CI job safe to re-run on the same tag.
    if ! $DRY_RUN && gh release view "$SINGLE_TAG" >/dev/null 2>&1; then
        echo "⏭️  Release for '$SINGLE_TAG' already exists — nothing to do."
        exit 0
    fi
    process_tag "$SINGLE_TAG"
    exit $?
fi

# Batch mode
echo "📦 Fetching tags..."
git fetch --tags --quiet 2>/dev/null || true
# Load tags AFTER fetch so we see newly pushed tags.
load_all_tags

if [[ -z "$ALL_TAGS" ]]; then
    echo "❌ No tags found."
    exit 1
fi

TAGS_BY_DATE=$(git tag --sort=-creatordate)

if [[ "$TAG_LIMIT" -gt 0 ]]; then
    TAGS=$(echo "$TAGS_BY_DATE" | head -n "$TAG_LIMIT")
    echo "🔢 Limited to latest $TAG_LIMIT tags"
else
    TAGS="$TAGS_BY_DATE"
fi

echo "🔍 Checking existing releases..."
EXISTING=$(gh release list --limit 200 --json tagName --jq '.[].tagName' 2>/dev/null || echo "")

CREATED=0
SKIPPED=0
FAILED=0

while IFS= read -r TAG; do
    [[ -z "$TAG" ]] && continue
    [[ "$TAG" != */* ]] && {
        SKIPPED=$((SKIPPED + 1))
        continue
    }

    if echo "$EXISTING" | grep -qxF "$TAG"; then
        echo "⏭️  Skipping '$TAG' (release exists)"
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    if process_tag "$TAG"; then
        CREATED=$((CREATED + 1))
    else
        FAILED=$((FAILED + 1))
    fi
done <<<"$TAGS"

echo ""
echo "═══════════════════════════════════════════════════════"
if $DRY_RUN; then
    echo "  🔍 DRY RUN — nothing was created"
    echo "  → Run with --apply to publish"
else
    echo "  ✅ Created: $CREATED | ⏭️ Skipped: $SKIPPED | ❌ Failed: $FAILED"
fi
echo "═══════════════════════════════════════════════════════"
