#!/usr/bin/env bash
# Mutation testing for dasik's idempotency core (CLAUDE.md § Quality).
#
# Coverage tells you a line RAN; mutation testing tells you a test would have
# NOTICED if that line were wrong. It injects one bug at a time (`>`->`>=`, drop
# a line, swap a set operator, `x`->`None`) into the code under test and re-runs
# the suite. A "survived" mutant = code that is covered but not verified -- the
# exact AI-shaped defect that passes coverage yet breaks idempotency.
#
# Usage:
#   scripts/mutation.sh              # tier 1: set_math.py (fast, gate-worthy)
#   scripts/mutation.sh --reconciler # tier 2: also mutate reconciler.py (slow)
#   scripts/mutation.sh --results    # just print the last run's survivors
#   scripts/mutation.sh --help
#
# Requires the `mut` extra:  pip install -e .[mut]
# Config lives in pyproject.toml under [tool.mutmut]. See docs/mutation-testing.md.
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v mutmut >/dev/null 2>&1; then
  echo "error: mutmut not found. Install it with:  pip install -e .[mut]" >&2
  exit 127
fi

# Documented EQUIVALENT mutants — behaviourally identical to the original, so no
# test can kill them (CLAUDE.md § Quality: document, don't chase). Matched by a
# STABLE diff signature, not the volatile mutant number. One array entry per
# signature (a signature may cover more than one mutant when the same
# reasoning applies verbatim to two functions — e.g. the two `last_type`
# entries below, one per parser). See docs/mutation-testing.md § Equivalent
# mutants for the full reasoning behind each one.
_EQUIVALENT_SIGNATURES=(
  # ScalarV3Action.is_needed/verify call plan(managed=[]); scalar plan()
  # ignores `managed`, so mutating it to plan(managed=None) changes nothing.
  'plan(managed=None)'
  # pacman_repos_state.options_block: `end = len(lines)` -> `end = None`.
  # `lines[start:None] == lines[start:]` in Python slicing, always.
  'end = None'
  # pacman_repos_state.section_of: the two ways mutmut removes the `[]`
  # default from `_field(model, "servers", ...)`. Either way `_field` then
  # returns `None` instead of `[]` for an absent "servers" key, but the
  # surrounding `... or []` immediately turns that `None` back into `[]` —
  # the default only matters when it is itself falsy.
  '_field(model, "servers", None) or []'
  '_field(model, "servers", ) or []'
  # pacman_repos_state.primary_fingerprints / trusted_fingerprints: the
  # `None` sentinel for "last record was not a pub" mutated to `""`. Both
  # functions compare it ONLY via `last_type == "pub"`, never `is None`, so
  # any non-"pub" placeholder is indistinguishable — covers both the initial
  # declaration and the blank-line reset, in both functions.
  'last_type: Optional[str] = ""'
  'last_type = ""'
  # pacman_repos_state.packaged_trusted: `stripped.split(":", 1)[0]` with the
  # maxsplit argument removed or changed to 2. `str.split(sep, n)[0]` is the
  # substring before the FIRST separator for any n >= 1 (or unlimited) — the
  # element at index 0 never depends on how many further splits happen.
  'stripped.split(":", )'
  'stripped.split(":", 2)'
)

_is_equivalent_diff() {
  local diff="$1" sig
  for sig in "${_EQUIVALENT_SIGNATURES[@]}"; do
    if grep -qF "$sig" <<< "$diff"; then
      return 0
    fi
  done
  return 1
}

report_survivors() {
  echo
  echo ">> Survivors:"
  local survivors real=0
  # `|| true`: with `set -euo pipefail`, a `grep` that matches NOTHING (the good
  # case: every mutant killed) exits 1 and would abort the whole script here,
  # falsely failing a mutation-clean run. Swallow that so 0 survivors -> empty.
  survivors="$(mutmut results | grep ': survived' | sed 's/: survived.*//; s/^[[:space:]]*//' || true)"
  if [ -z "$survivors" ]; then
    echo "   none -- the mutated code is mutation-clean."
    return 0
  fi
  while IFS= read -r m; do
    [ -z "$m" ] && continue
    local diff
    diff="$(mutmut show "$m" 2>/dev/null)"
    if _is_equivalent_diff "$diff"; then
      echo "   (equivalent, expected) $m"
    else
      echo "   SURVIVED        $m"
      real=$((real + 1))
    fi
  done <<< "$survivors"
  if [ "$real" -gt 0 ]; then
    echo >&2
    echo "!! $real real surviving mutant(s) — covered but not verified." >&2
    echo "   Inspect: mutmut show <name>; then add/strengthen a test to kill it." >&2
    return 1
  fi
  echo "   all survivors are documented equivalent mutants — OK."
  return 0
}

case "${1:-}" in
  -h|--help)
    sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
  --results)
    mutmut results
    exit 0
    ;;
  --reconciler)
    # Tier 2: widen the mutation set to reconciler.py on top of set_math.py.
    # mutmut reads `only_mutate` only from pyproject.toml, so temporarily patch
    # that one line and restore it on exit (even on Ctrl-C / failure).
    BAK="$(mktemp)"
    cp pyproject.toml "$BAK"
    trap 'cp "$BAK" pyproject.toml; rm -f "$BAK"; echo ">> restored pyproject.toml"' EXIT
    python3 - <<'PY'
import re, pathlib
p = pathlib.Path("pyproject.toml")
t = p.read_text()
new = re.sub(
    r'only_mutate = \[[^\]]*\]',
    'only_mutate = ["dasik/lib/state/set_math.py", "dasik/lib/reconciler/reconciler.py"]',
    t, count=1,
)
assert new != t, "could not find only_mutate line to patch"
p.write_text(new)
PY
    echo ">> Tier 2: mutating set_math.py + reconciler.py (this is slow)..."
    rm -rf mutants .mutmut-cache
    mutmut run
    report_survivors
    exit $?
    ;;
  "")
    ;;
  *)
    echo "error: unknown option '$1' (try --help)" >&2
    exit 2
    ;;
esac

echo ">> Running mutation testing on the idempotency core (set_math.py)..."
# Always start from a fresh copy (tier 2 does the same). mutmut mutates a COPY of
# the tree under mutants/ and does not resync files it already has, so a stale
# mutants/ left by an earlier run keeps serving old source to the tests — which
# then fail to even collect ("cannot import name X from dasik.lib...") and the
# gate reports a failure that has nothing to do with the code being pushed.
rm -rf mutants .mutmut-cache
mutmut run
report_survivors
