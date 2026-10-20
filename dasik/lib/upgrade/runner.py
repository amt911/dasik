"""Run upgrade steps and summarise them; one failure never stops the run."""
import re
from typing import Callable, List, Optional, Sequence

from dasik.lib.command_worker.command_worker import Command
from dasik.lib.command_worker.su_argv import su_argv
from dasik.lib.upgrade.steps import UpgradeOutcome, UpgradeStep

_ORDER = (("updated", "updated"), ("current", "already latest"),
          ("refreshed", "refreshed"), ("skipped", "skipped"),
          ("failed", "failed"))


def _output_of(result) -> str:
    """stdout or stderr, whichever has text (stream=True merges them)."""
    for attr in ("stderr", "stdout"):
        raw = getattr(result, attr, "") or ""
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        if raw.strip():
            return raw.strip()
    return ""


_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _failure_line(result, needles) -> Optional[str]:
    """First output line (either stream) containing one of *needles*."""
    for attr in ("stdout", "stderr"):
        raw = getattr(result, attr, "") or ""
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        for line in _ANSI.sub("", raw).replace("\r", "\n").splitlines():
            if any(n in line for n in needles):
                return line.strip()
    return None


def _run_one(step: UpgradeStep, target) -> UpgradeOutcome:
    try:
        return _execute(step, target)
    except Exception as exc:  # one broken step never stops the run
        text = str(exc).strip()
        first = text.splitlines()[0] if text else type(exc).__name__
        return UpgradeOutcome(step.item, "failed", None, None, first)


def _execute(step: UpgradeStep, target) -> UpgradeOutcome:
    before = step.version()
    print_before = step.fingerprint()
    for script, args in step.commands:
        result = Command.execute(
            "su", su_argv(step.user, script, *args), target=target,
            check=False, stream=True, label=f"upgrade: {step.item}")
        if getattr(result, "returncode", 1) != 0:
            return UpgradeOutcome(step.item, "failed", before, None,
                                  _output_of(result))
        line = _failure_line(result, step.failure_text)
        if line is not None:
            return UpgradeOutcome(step.item, "failed", before, None, line)
    after = step.version()
    print_after = step.fingerprint()
    versions = before is not None and after is not None
    if versions and before != after:
        return UpgradeOutcome(step.item, "updated", before, after)
    if print_before is not None and print_after is not None:
        changed = print_before != print_after
        return UpgradeOutcome(step.item, "updated" if changed else "current",
                              before, after,
                              contents="changed" if changed else "unchanged")
    status = "current" if versions else "refreshed"
    return UpgradeOutcome(step.item, status, before, after)


def run_steps(steps: Sequence[UpgradeStep], target,
              on_start: Optional[Callable[[UpgradeStep], None]] = None,
              on_done: Optional[Callable[[UpgradeOutcome], None]] = None,
              ) -> List[UpgradeOutcome]:
    """Run every step; *on_start* fires before one that runs commands,
    *on_done* after every step — so a caller can show progress live."""
    outcomes: List[UpgradeOutcome] = []
    failed = set()
    for step in steps:
        if step.after is not None and step.after in failed:
            outcome = UpgradeOutcome(step.item, "skipped", None, None,
                                     "marketplace refresh failed")
        elif step.skip is not None:
            outcome = UpgradeOutcome(step.item, "skipped", None, None, step.skip)
        else:
            if on_start is not None:
                on_start(step)
            outcome = _run_one(step, target)
        if outcome.status == "failed":
            failed.add(step.item)
        if on_done is not None:
            on_done(outcome)
        outcomes.append(outcome)
    return outcomes


_LONG_HASH = re.compile(r"[0-9a-f]{20,}")


def _short(value: Optional[str]) -> Optional[str]:
    """A full hex digest cut to 12 characters, the way git abbreviates."""
    if value is not None and _LONG_HASH.fullmatch(value):
        return value[:12]
    return value


def line(o: UpgradeOutcome) -> str:
    """One outcome, as the user reads it."""
    before, after = _short(o.before), _short(o.after)
    if o.status == "updated":
        if o.contents == "changed":
            if after is None:
                return f"{o.item}: updated (contents changed)"
            if before == after:
                return (f"{o.item}: updated {after} "
                        "(contents changed, same version)")
            return f"{o.item}: updated (contents changed, now {after})"
        return f"{o.item}: updated {before} → {after}"
    if o.status == "current":
        if after is None:
            return f"{o.item}: already latest (contents unchanged)"
        return f"{o.item}: already latest ({after})"
    if o.status == "refreshed":
        return f"{o.item}: refreshed (version unknown)"
    if o.status == "skipped":
        return f"{o.item}: skipped ({o.detail})"
    first = o.detail.splitlines()[0] if o.detail else ""
    return f"{o.item}: FAILED — {first}"


def summary(outcomes: Sequence[UpgradeOutcome]) -> str:
    """The closing count of every status."""
    counts = [sum(o.status == key for o in outcomes) for key, _ in _ORDER]
    return ", ".join(f"{n} {label}" for n, (_, label) in zip(counts, _ORDER))


def exit_code(outcomes: Sequence[UpgradeOutcome]) -> int:
    return 1 if any(o.status == "failed" for o in outcomes) else 0
