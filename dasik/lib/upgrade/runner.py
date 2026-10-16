"""Run upgrade steps and summarise them; one failure never stops the run."""
from typing import List, Sequence

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


def _run_one(step: UpgradeStep, target) -> UpgradeOutcome:
    if step.skip is not None:
        return UpgradeOutcome(step.item, "skipped", None, None, step.skip)
    before = step.version()
    for script, args in step.commands:
        result = Command.execute(
            "su", su_argv(step.user, script, *args), target=target,
            check=False, stream=True, label=f"upgrade: {step.item}")
        if getattr(result, "returncode", 1) != 0:
            return UpgradeOutcome(step.item, "failed", before, None,
                                  _output_of(result))
    after = step.version()
    if before is None or after is None:
        status = "refreshed"
    else:
        status = "current" if before == after else "updated"
    return UpgradeOutcome(step.item, status, before, after)


def run_steps(steps: Sequence[UpgradeStep], target) -> List[UpgradeOutcome]:
    return [_run_one(step, target) for step in steps]


def _line(o: UpgradeOutcome) -> str:
    if o.status == "updated":
        return f"{o.item}: updated {o.before} → {o.after}"
    if o.status == "current":
        return f"{o.item}: already latest ({o.after})"
    if o.status == "refreshed":
        return f"{o.item}: refreshed (version unknown)"
    if o.status == "skipped":
        return f"{o.item}: skipped ({o.detail})"
    first = o.detail.splitlines()[0] if o.detail else ""
    return f"{o.item}: FAILED — {first}"


def render(outcomes: Sequence[UpgradeOutcome]) -> str:
    lines = [_line(o) for o in outcomes]
    counts = [sum(o.status == key for o in outcomes) for key, _ in _ORDER]
    lines.append(", ".join(f"{n} {label}"
                           for n, (_, label) in zip(counts, _ORDER)))
    return "\n".join(lines)


def exit_code(outcomes: Sequence[UpgradeOutcome]) -> int:
    return 1 if any(o.status == "failed" for o in outcomes) else 0
