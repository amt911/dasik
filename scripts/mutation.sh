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

report_survivors() {
  echo
  echo ">> Survivors (empty == every mutant was caught):"
  if mutmut results | grep -q ": survived"; then
    mutmut results | grep ": survived"
    echo
    echo "!! Surviving mutants above are covered-but-unverified code." >&2
    echo "   Inspect one with:  mutmut show <mutant-name>" >&2
    echo "   Then add/strengthen a test assert to kill it." >&2
    return 1
  fi
  echo "   none -- the mutated code is mutation-clean."
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
mutmut run
report_survivors
