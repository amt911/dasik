# `apply` verb + AUR write-path — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the destructive convergence path for the `packages` domain. `dasik apply <config> [--target / | /mnt] [--yes]` plans the diff, requires confirmation for destructive changes, then runs `pacman -S` / `pacman -Rns` / AUR makepkg flows to converge — and records a new generation + updated manifest after success. Same JSON re-applied is a no-op (the read-only `plan` from Plan 3 keeps working unchanged).

**Architecture:** `PackagesAction.apply(changes)` becomes the first v3 destructive implementation: it routes INSTALL changes through pacman vs the AUR makepkg dance based on the `aur-` prefix the action already tracks (`self.aur_pkgs`), and REMOVE changes through `pacman -Rns` regardless. The Reconciler grows `apply(plan, results, *, assume_yes)` that (a) gates destructive plans behind a prompt unless `--yes`, (b) drives each v3 action's `apply()` in registry order, (c) merges every action's `managed_keys()` into the manifest, (d) writes the manifest via `StateStore` and records a generation via `GenerationStore`. The CLI grows an `apply` verb wired exactly like `plan` plus the persistence/confirmation steps.

**Tech Stack:** Python ≥3.10 stdlib (`argparse`, `pathlib`, `subprocess`, `hashlib`, `datetime`), pytest + `unittest.mock`. No new runtime deps.

**Spec:** [`docs/superpowers/specs/2026-05-27-declarative-convergence-and-sync-design.md`](../specs/2026-05-27-declarative-convergence-and-sync-design.md) — §3.5 (action contract v3), §3.6 (Reconciler `apply`), §3.8 (CLI `apply`), §4 (`apply` flow), §5 (safety), §6 (storage wiring).

**Base branch:** `main` (Plan 3 merged via PR #60 → commit `853e84c`).

**Out of scope (later plans):**
- `sync` / `rollback` / `generations` CLI verbs (they read the same StateStore/GenerationStore plumbing this plan installs, but each needs its own UX/flow plan).
- Migrating systemd / files / users / sysctl / etc. to v3 — packages stays the only v3 domain after this plan.
- `ConfigWriter` (writes JSON back from `sync`).
- Disk convergence and bootloader generation entries (gated separately per spec).
- Replacing the legacy `dasik <config>` no-verb form (still works as a deprecated alias).

---

## File Structure

| File | Responsibility |
| --- | --- |
| `dasik/lib/actions/packages_action.py` (modify) | Extend v3: `plan()` now includes AUR, add `apply(changes)` that routes pacman vs AUR. Legacy `is_needed`/`execute` untouched. |
| `dasik/lib/reconciler/reconciler.py` (modify) | Add `Reconciler.apply(plan, results, *, assume_yes, input_fn=input)` and the manifest-update / generation-recording helpers. |
| `dasik/__main__.py` (modify) | Add `apply` subcommand; wire StateStore + GenerationStore; pass them through. |
| `tests/lib/actions/test_packages_action_v3.py` (modify) | Update the two AUR-filter tests (semantics change in Plan 4) and add `apply()` tests covering pacman INSTALL, pacman REMOVE, AUR INSTALL via helper, AUR INSTALL via makepkg fallback, no-op when no changes. |
| `tests/lib/reconciler/test_reconciler_apply.py` (new) | `Reconciler.apply` orchestration: empty plan no-op, destructive prompt path (yes/no), `--yes` bypass, action ordering, manifest+generation persistence. |
| `tests/test_cli_apply.py` (new) | CLI smoke: `apply` verb invokes Reconciler.apply with the right args, prints rendered plan + result, exit codes, `--yes` flag, missing-config error path. |

---

## Task 1: `PackagesAction.apply()` + extend `plan()` to include AUR

**Files:**
- Modify: `dasik/lib/actions/packages_action.py`
- Modify: `tests/lib/actions/test_packages_action_v3.py`

In Plan 3, `plan()` filtered out `aur-` entries on purpose (we didn't have an `apply()` path that could honor them). Now we own the apply path, so AUR comes back into the v3 flow:

- `plan()` desired set becomes `self.pacman_pkgs + self.aur_pkgs` (the prefix-stripped names — same names that `pacman -Qqe` would emit for AUR packages installed via makepkg, since they land in the pacman DB).
- `managed_keys()` returns the union too (so the manifest tracks every package this action owns).
- `apply(changes)` walks the changes: INSTALL routes via pacman (`pacman -S --noconfirm --needed`) for items in `self.pacman_pkgs`, via the AUR makepkg dance for items in `self.aur_pkgs`. REMOVE routes via `pacman -Rns --noconfirm` regardless (works for AUR pkgs too — they live in the pacman DB once installed).
- All `Command.execute(..., target=self.context.target)` — destructive runs against the live host or `/mnt` depending on the Target. The legacy `subprocess.run(["arch-chroot", "/mnt", ...])` helpers stay only behind the legacy `is_needed`/`execute` path; they are not reachable from `apply()`.

The two Plan-3 tests that asserted AUR filtering (`test_plan_ignores_aur_prefixed_entries_in_config`, `test_managed_keys_ignores_aur_prefix_entries`) get updated in-place to assert the new behavior (AUR included).

- [ ] **Step 1: Update the failing AUR-filter tests + add new `apply()` tests**

Open `tests/lib/actions/test_packages_action_v3.py`. Replace the two tests below and append the new `apply()` tests at the end of the file.

Replace `test_plan_ignores_aur_prefixed_entries_in_config` with:

```python
def test_plan_includes_aur_pkgs_as_install_changes():
    """Plan 4: AUR packages participate in plan()/apply() (stripped of aur- prefix)."""
    fake = _fake_command_run(stdout=b"")  # nothing installed
    with patch("dasik.lib.actions.packages_action.Command.execute", fake):
        a = PackagesAction(config=["git", "aur-yay"], context=_ctx("/"))
        changes = a.plan(managed=[])
    items = sorted((c.op, c.item) for c in changes)
    assert items == [(Op.INSTALL, "git"), (Op.INSTALL, "yay")]
```

Replace `test_managed_keys_ignores_aur_prefix_entries` with:

```python
def test_managed_keys_includes_aur_pkgs_stripped_of_prefix():
    """Plan 4: manifest tracks AUR packages too (under the 'packages' domain)."""
    a = PackagesAction(config=["git", "aur-yay"], context=_ctx("/"))
    assert a.managed_keys() == {"packages": ["git", "yay"]}
```

Append at the bottom of the file:

```python
# ---------------------------------------------------------------------- #
#  Plan 4: apply() — destructive path (pacman + AUR)                     #
# ---------------------------------------------------------------------- #

from dasik.lib.state.change import Change


def test_apply_no_changes_is_noop():
    a = PackagesAction(config=["git"], context=_ctx("/"))
    with patch("dasik.lib.actions.packages_action.Command.execute") as run:
        a.apply([])
    run.assert_not_called()


def test_apply_install_routes_pacman_pkgs_through_pacman_S():
    a = PackagesAction(config=["git", "htop"], context=_ctx("/"))
    changes = [
        Change("packages", Op.INSTALL, "git"),
        Change("packages", Op.INSTALL, "htop"),
    ]
    with patch("dasik.lib.actions.packages_action.Command.execute") as run:
        a.apply(changes)
    # One pacman -S call with both names + --noconfirm --needed
    assert run.call_count == 1
    args = run.call_args
    assert args.args[0] == "pacman"
    pacman_args = args.args[1]
    assert "-S" in pacman_args
    assert "--noconfirm" in pacman_args
    assert "--needed" in pacman_args
    assert "git" in pacman_args
    assert "htop" in pacman_args
    assert args.kwargs["target"].root == "/"


def test_apply_remove_routes_through_pacman_Rns():
    a = PackagesAction(config=[], context=_ctx("/"))
    changes = [Change("packages", Op.REMOVE, "vim")]
    with patch("dasik.lib.actions.packages_action.Command.execute") as run:
        a.apply(changes)
    assert run.call_count == 1
    args = run.call_args
    assert args.args[0] == "pacman"
    pacman_args = args.args[1]
    assert "-Rns" in pacman_args
    assert "--noconfirm" in pacman_args
    assert "vim" in pacman_args


def test_apply_mixes_install_and_remove_in_correct_order():
    """Install BEFORE remove (additive first reduces breakage if remove fails)."""
    a = PackagesAction(config=["git"], context=_ctx("/"))
    changes = [
        Change("packages", Op.REMOVE, "vim"),
        Change("packages", Op.INSTALL, "git"),
    ]
    with patch("dasik.lib.actions.packages_action.Command.execute") as run:
        a.apply(changes)
    # Two calls: pacman -S first, then pacman -Rns
    assert run.call_count == 2
    first_args = run.call_args_list[0].args
    second_args = run.call_args_list[1].args
    assert "-S" in first_args[1]
    assert "-Rns" in second_args[1]


def test_apply_aur_install_uses_makepkg_path():
    """AUR INSTALL: pkg in self.aur_pkgs goes through the makepkg dance."""
    a = PackagesAction(config=["aur-yay"], context=_ctx("/"))
    changes = [Change("packages", Op.INSTALL, "yay")]
    with patch.object(PackagesAction, "_apply_aur_install") as aur_install, \
         patch("dasik.lib.actions.packages_action.Command.execute") as run:
        a.apply(changes)
    aur_install.assert_called_once_with(["yay"])
    # No pacman -S call (no pacman pkgs to install)
    for call in run.call_args_list:
        assert "-S" not in call.args[1] or "base-devel" in call.args[1]
        # _apply_aur_install is mocked, so any Command.execute here would be
        # incidental setup we did not stub. Assert it is not a bulk pacman -S
        # of the AUR pkg list:
        assert "yay" not in call.args[1]


def test_apply_separates_pacman_install_from_aur_install():
    """Mixed config: pacman pkg → pacman -S; AUR pkg → AUR path."""
    a = PackagesAction(config=["git", "aur-yay"], context=_ctx("/"))
    changes = [
        Change("packages", Op.INSTALL, "git"),
        Change("packages", Op.INSTALL, "yay"),
    ]
    with patch.object(PackagesAction, "_apply_aur_install") as aur_install, \
         patch("dasik.lib.actions.packages_action.Command.execute") as run:
        a.apply(changes)
    aur_install.assert_called_once_with(["yay"])
    # Exactly one pacman -S call for the pacman items
    pacman_S_calls = [
        c for c in run.call_args_list
        if c.args[0] == "pacman" and "-S" in c.args[1] and "git" in c.args[1]
    ]
    assert len(pacman_S_calls) == 1


def test_apply_skips_when_context_target_missing():
    """Defensive: no target → apply is a no-op (cannot run pacman)."""
    a = PackagesAction(config=["git"], context=None)
    with patch("dasik.lib.actions.packages_action.Command.execute") as run:
        a.apply([Change("packages", Op.INSTALL, "git")])
    run.assert_not_called()


def test_apply_aur_install_helper_runs_makepkg_dance():
    """The private _apply_aur_install helper: prerequisites + per-pkg makepkg."""
    a = PackagesAction(config=["aur-yay"], context=_ctx("/"))
    with patch("dasik.lib.actions.packages_action.Command.execute") as run, \
         patch("dasik.lib.actions.packages_action.subprocess.run") as sp_run, \
         patch("builtins.open", create=True):
        sp_run.return_value = MagicMock(returncode=0, stdout=b"", stderr=b"")
        a._apply_aur_install(["yay"])
    # At minimum: pacman -S base-devel git was called via Command.execute
    pacman_calls = [c for c in run.call_args_list if c.args[0] == "pacman"]
    assert any(
        "base-devel" in c.args[1] and "git" in c.args[1]
        for c in pacman_calls
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/lib/actions/test_packages_action_v3.py -v`
Expected: FAIL — the renamed AUR tests fail because `plan()` still filters; the new `apply()` tests fail because `PackagesAction.apply` is still the no-op inherited from `AbstractAction`.

- [ ] **Step 3: Update `plan()` and `managed_keys()` to include AUR; add `apply()`**

In `dasik/lib/actions/packages_action.py`:

(a) Replace the body of `plan` (around line 217-227) with:

```python
    def plan(self, managed):
        """Compute INSTALL/REMOVE for both pacman and AUR packages.

        Both kinds land in the pacman DB once installed, so ``pacman -Qqe``
        (which ``actual()`` parses) sees them together. The action carries
        the original split via ``self.pacman_pkgs`` / ``self.aur_pkgs`` so
        ``apply()`` can route INSTALLs to the right tool.
        """
        from ..state.set_math import compute_changes
        desired = list(self.pacman_pkgs) + list(self.aur_pkgs)
        changes, _drift = compute_changes(
            self._PACMAN_DOMAIN,
            desired=desired,
            managed=managed,
            actual=self.actual(),
        )
        return changes
```

(b) Replace the body of `managed_keys` (around line 229-231) with:

```python
    def managed_keys(self) -> dict:
        """The full set of packages this action owns after apply
        (pacman + AUR, both under the ``packages`` domain).
        """
        return {self._PACMAN_DOMAIN: list(self.pacman_pkgs) + list(self.aur_pkgs)}
```

(c) Append, at the very end of the class:

```python

    # ------------------------------------------------------------------ #
    #  v3 apply() — destructive (Plan 4)                                 #
    # ------------------------------------------------------------------ #

    def apply(self, changes) -> None:
        """Execute a list of ``Change`` objects against the target.

        Routing rules:
        - ``Op.INSTALL`` and item in ``self.pacman_pkgs`` → ``pacman -S``.
        - ``Op.INSTALL`` and item in ``self.aur_pkgs``    → ``_apply_aur_install``.
        - ``Op.REMOVE`` → ``pacman -Rns`` (handles both pacman + AUR pkgs).

        INSTALLs run before REMOVEs (additive first; keeps the system in a
        working state if the destructive step fails midway).
        """
        from ..state.change import Op

        target = getattr(self.context, "target", None) if self.context else None
        if target is None:
            return

        if not changes:
            return

        pacman_installs: list[str] = []
        aur_installs: list[str] = []
        removes: list[str] = []
        aur_set = set(self.aur_pkgs)
        pacman_set = set(self.pacman_pkgs)

        for change in changes:
            if change.op is Op.INSTALL:
                if change.item in pacman_set:
                    pacman_installs.append(change.item)
                elif change.item in aur_set:
                    aur_installs.append(change.item)
                else:
                    # Defensive: unknown item — treat as pacman install.
                    pacman_installs.append(change.item)
            elif change.op is Op.REMOVE:
                removes.append(change.item)

        if pacman_installs:
            Command.execute(
                "pacman",
                ["--noconfirm", "--needed", "-S", *pacman_installs],
                target=target,
            )

        if aur_installs:
            self._apply_aur_install(aur_installs)

        if removes:
            Command.execute(
                "pacman",
                ["--noconfirm", "-Rns", *removes],
                target=target,
            )

    def _apply_aur_install(self, pkgs: list[str]) -> None:
        """Install AUR packages via the makepkg dance (target-aware).

        Steps:
          1. Ensure base-devel + git installed on the target.
          2. Ensure the temp build user exists (passwordless sudo via sudoers.d).
          3. For each pkg: clone + makepkg -sri as the build user.
          4. Remove the temp build user + sudoers fragment.
        """
        import os
        target = self.context.target

        # 1. Prerequisites
        Command.execute(
            "pacman",
            ["--noconfirm", "--needed", "-S", "base-devel", "git"],
            target=target,
        )

        # 2. Build user
        id_check = subprocess.run(
            self._target_argv(target, ["id", self._AUR_USER]),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if id_check.returncode != 0:
            Command.execute(
                "useradd",
                ["-m", "-r", "-s", "/bin/bash", self._AUR_USER],
                target=target,
            )

        sudoers_path = target.path(f"/etc/sudoers.d/{self._AUR_USER}")
        with open(sudoers_path, "w") as f:
            f.write(f"{self._AUR_USER} ALL=(ALL) NOPASSWD: ALL\n")

        # 3. Build each
        for pkg in pkgs:
            build_dir = f"/home/{self._AUR_USER}/{pkg}"
            subprocess.run(
                self._target_argv(target, ["rm", "-rf", build_dir]),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                self._target_argv(target, [
                    "su", "-", self._AUR_USER, "-c",
                    f"git clone https://aur.archlinux.org/{pkg}.git {build_dir}",
                ]),
                check=True,
            )
            subprocess.run(
                self._target_argv(target, [
                    "su", "-", self._AUR_USER, "-c",
                    f"cd {build_dir} && makepkg -sri --noconfirm",
                ]),
                check=True,
            )

        # 4. Cleanup
        subprocess.run(
            self._target_argv(target, ["userdel", "-r", self._AUR_USER]),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if os.path.exists(sudoers_path):
            os.remove(sudoers_path)

    @staticmethod
    def _target_argv(target, cmd: list[str]) -> list[str]:
        """Prefix ``arch-chroot <root>`` when target is a chroot, else passthrough."""
        if getattr(target, "is_chroot", lambda: False)():
            return ["arch-chroot", target.root, *cmd]
        return list(cmd)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/lib/actions/test_packages_action_v3.py -v`
Expected: PASS (all updated + new tests green).

- [ ] **Step 5: Run the full suite**

Run: `PYTHONPATH=. pytest -v`
Expected: PASS — everything still green.

- [ ] **Step 6: Commit**

```bash
git add dasik/lib/actions/packages_action.py tests/lib/actions/test_packages_action_v3.py
git commit -m "feat(packages): v3 apply() + plan() includes AUR

apply() routes INSTALL through pacman -S vs the makepkg dance based on
the aur- prefix the action already tracks; REMOVE always goes through
pacman -Rns. plan()/managed_keys() now include AUR packages so the
manifest tracks the full set this action owns.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 2: `Reconciler.apply()` + manifest/generation persistence

**Files:**
- Modify: `dasik/lib/reconciler/reconciler.py`
- Create: `tests/lib/reconciler/test_reconciler_apply.py`

Add `Reconciler.apply(plan, results, *, assume_yes=False, input_fn=input)`:

1. If `plan.is_empty()` → return early with no side effects.
2. If `plan.destructive()` is non-empty AND `assume_yes is False` → call `input_fn(prompt)`; bail out if the answer is not `y`/`yes` (case-insensitive). The prompt names the destructive count.
3. For each `ActionPlanResult` in `results`: call `result.action.apply(result.changes)`. Registry order is preserved (Reconciler already walks the registry in order in `build_plan`).
4. Build the new manifest:
   - `managed = union of every action.managed_keys() for action in results`
   - `generation = old_generation + 1` (or 1 if missing)
   - `applied_at = ISO-8601 UTC timestamp` (`datetime.now(timezone.utc).isoformat()`)
   - `config_hash = sha256(json.dumps(config, sort_keys=True)).hexdigest()`
5. Persist via `StateStore` + `GenerationStore` (both passed in by the caller — Reconciler stays decoupled from filesystem layout; tests mock them).
6. Return the new `Manifest` so the CLI can report the generation number.

The Reconciler does NOT call StateStore/GenerationStore directly — they are injected through the constructor so tests can supply fakes without touching the filesystem.

- [ ] **Step 1: Write the failing tests**

Create `tests/lib/reconciler/test_reconciler_apply.py`:

```python
import json
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from dasik.lib.actions.abstract_action import AbstractAction
from dasik.lib.reconciler.reconciler import ActionPlanResult, Reconciler
from dasik.lib.state.change import Change, Op, Plan
from dasik.lib.target.target import Target


class _RecordingV3(AbstractAction):
    """Stub v3 action that records apply() calls + owns one domain."""

    last_applied: list = []

    @property
    def name(self) -> str: return "rec"
    def is_needed(self) -> bool: return False
    def execute(self) -> None: pass

    def plan(self, managed):
        return []

    def apply(self, changes):
        type(self).last_applied = list(changes)

    def managed_keys(self):
        return {"packages": list(self.config) if isinstance(self.config, list) else []}


def _make_reconciler(*, config=None, manifest=None, store=None, gen_store=None):
    return Reconciler(
        config=config or {"packages": []},
        target=Target(root="/"),
        manifest=manifest or {"managed": {}},
        action_metas=[],
        state_store=store,
        generation_store=gen_store,
    )


def test_apply_noop_when_plan_is_empty():
    store = MagicMock()
    gen = MagicMock()
    r = _make_reconciler(store=store, gen_store=gen)
    new_manifest = r.apply(Plan(), [], assume_yes=True)
    store.save.assert_not_called()
    gen.new.assert_not_called()
    assert new_manifest is None


def test_apply_runs_each_action_apply_in_order():
    store = MagicMock()
    gen = MagicMock()
    r = _make_reconciler(store=store, gen_store=gen)

    a1 = _RecordingV3(config=["git"], context=None)
    a2 = _RecordingV3(config=["htop"], context=None)
    plan = Plan()
    c1 = Change("packages", Op.INSTALL, "git")
    c2 = Change("packages", Op.INSTALL, "htop")
    plan.add(c1)
    plan.add(c2)
    results = [
        ActionPlanResult(action=a1, changes=[c1]),
        ActionPlanResult(action=a2, changes=[c2]),
    ]
    _RecordingV3.last_applied = []
    r.apply(plan, results, assume_yes=True)
    # Both actions' apply() called with their slice
    # (because they share the class attr, last_applied reflects last call)
    assert _RecordingV3.last_applied == [c2]


def test_apply_destructive_plan_prompts_user_and_aborts_on_no():
    store = MagicMock()
    gen = MagicMock()
    r = _make_reconciler(store=store, gen_store=gen)
    a = _RecordingV3(config=[], context=None)
    plan = Plan()
    plan.add(Change("packages", Op.REMOVE, "vim"))
    results = [ActionPlanResult(action=a, changes=list(plan.changes))]

    answers = iter(["n"])
    new_manifest = r.apply(
        plan, results,
        assume_yes=False,
        input_fn=lambda _: next(answers),
    )
    # No persistence on abort
    store.save.assert_not_called()
    gen.new.assert_not_called()
    assert new_manifest is None


def test_apply_destructive_plan_proceeds_when_user_confirms():
    store = MagicMock()
    gen = MagicMock()
    gen.new.return_value = 3
    r = _make_reconciler(store=store, gen_store=gen)
    a = _RecordingV3(config=[], context=None)
    plan = Plan()
    plan.add(Change("packages", Op.REMOVE, "vim"))
    results = [ActionPlanResult(action=a, changes=list(plan.changes))]

    answers = iter(["y"])
    new_manifest = r.apply(
        plan, results,
        assume_yes=False,
        input_fn=lambda _: next(answers),
    )
    assert new_manifest is not None
    store.save.assert_called_once()
    gen.new.assert_called_once()


def test_apply_with_assume_yes_skips_prompt_even_for_destructive():
    store = MagicMock()
    gen = MagicMock()
    gen.new.return_value = 1
    r = _make_reconciler(store=store, gen_store=gen)
    a = _RecordingV3(config=[], context=None)
    plan = Plan()
    plan.add(Change("packages", Op.REMOVE, "vim"))
    results = [ActionPlanResult(action=a, changes=list(plan.changes))]

    sentinel = MagicMock(side_effect=AssertionError("prompt called"))
    new_manifest = r.apply(plan, results, assume_yes=True, input_fn=sentinel)
    assert new_manifest is not None
    sentinel.assert_not_called()


def test_apply_merges_managed_keys_into_new_manifest():
    store = MagicMock()
    gen = MagicMock()
    gen.new.return_value = 2
    r = _make_reconciler(
        config={"packages": ["git", "htop"]},
        manifest={"managed": {"packages": ["vim"]}, "generation": 1},
        store=store,
        gen_store=gen,
    )
    a = _RecordingV3(config=["git", "htop"], context=None)
    plan = Plan()
    plan.add(Change("packages", Op.INSTALL, "git"))
    results = [ActionPlanResult(action=a, changes=list(plan.changes))]

    new_manifest = r.apply(plan, results, assume_yes=True)
    assert new_manifest.managed == {"packages": ["git", "htop"]}
    assert new_manifest.generation == 2  # bumped from 1
    assert new_manifest.config_hash is not None
    assert new_manifest.applied_at is not None
    # Persisted: StateStore.save received the new Manifest;
    # GenerationStore.new received (config, manifest_dict).
    saved = store.save.call_args.args[0]
    assert saved.generation == 2
    gen_args = gen.new.call_args.args
    assert gen_args[0] == {"packages": ["git", "htop"]}  # config
    assert gen_args[1]["generation"] == 2  # manifest dict


def test_apply_generation_starts_at_one_when_manifest_is_none():
    store = MagicMock()
    gen = MagicMock()
    gen.new.return_value = 1
    r = _make_reconciler(
        config={"packages": ["git"]},
        manifest=None,
        store=store,
        gen_store=gen,
    )
    a = _RecordingV3(config=["git"], context=None)
    plan = Plan()
    plan.add(Change("packages", Op.INSTALL, "git"))
    results = [ActionPlanResult(action=a, changes=list(plan.changes))]

    new_manifest = r.apply(plan, results, assume_yes=True)
    assert new_manifest.generation == 1


def test_apply_without_stores_runs_actions_but_skips_persistence():
    """When state_store/generation_store are None (e.g., dry tests), the
    actions still run but persistence is skipped."""
    r = _make_reconciler(store=None, gen_store=None)
    a = _RecordingV3(config=["git"], context=None)
    plan = Plan()
    c = Change("packages", Op.INSTALL, "git")
    plan.add(c)
    results = [ActionPlanResult(action=a, changes=[c])]
    _RecordingV3.last_applied = []
    new_manifest = r.apply(plan, results, assume_yes=True)
    assert _RecordingV3.last_applied == [c]
    assert new_manifest is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/lib/reconciler/test_reconciler_apply.py -v`
Expected: FAIL — `Reconciler.__init__` does not accept `state_store`/`generation_store`; `Reconciler.apply` does not exist.

- [ ] **Step 3: Extend `Reconciler`**

In `dasik/lib/reconciler/reconciler.py`:

(a) Update the imports near the top:

```python
import hashlib
import json
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from ..actions.abstract_action import AbstractAction
from ..actions.action_context import ActionContext
from ..state.change import Change, Plan
from ..state.manifest import Manifest
from ..target.target import Target
```

(b) Extend `__init__` to accept optional stores:

```python
    def __init__(
        self,
        config: dict[str, Any],
        target: Target,
        manifest: Optional[dict[str, Any]],
        action_metas: Iterable[dict[str, Any]],
        state_store: Optional[Any] = None,
        generation_store: Optional[Any] = None,
    ):
        self._config = config
        self._target = target
        self._manifest = manifest
        self._metas = list(action_metas)
        self._state_store = state_store
        self._generation_store = generation_store
```

(c) Append the `apply` method at the end of the class (after the helpers):

```python
    def apply(
        self,
        plan: Plan,
        results: list[ActionPlanResult],
        *,
        assume_yes: bool = False,
        input_fn: Callable[[str], str] = input,
    ) -> Optional[Manifest]:
        """Execute a built plan: gate destructive ops, run each action's
        apply(), then persist the new manifest + generation.

        Args:
            plan: aggregate Plan from build_plan().
            results: per-action breakdown from build_plan().
            assume_yes: if True, skip the destructive-change prompt.
            input_fn: stdin reader (injectable for tests).

        Returns:
            The new ``Manifest`` if anything was applied, else ``None``.
        """
        if plan.is_empty():
            return None

        destructive = plan.destructive()
        if destructive and not assume_yes:
            answer = input_fn(
                f"Apply {len(destructive)} destructive change(s)? [y/N] "
            ).strip().lower()
            if answer not in ("y", "yes"):
                return None

        for result in results:
            result.action.apply(result.changes)

        new_manifest = self._build_new_manifest(results)

        if self._state_store is not None:
            self._state_store.save(new_manifest)
        if self._generation_store is not None:
            self._generation_store.new(self._config, new_manifest.to_dict())

        return new_manifest

    def _build_new_manifest(
        self, results: list[ActionPlanResult]
    ) -> Manifest:
        managed: dict[str, Any] = {}
        for result in results:
            try:
                keys = result.action.managed_keys()
            except Exception:
                keys = {}
            if isinstance(keys, dict):
                managed.update(keys)

        prev_generation = 0
        if isinstance(self._manifest, dict):
            prev_generation = int(self._manifest.get("generation", 0))

        config_hash = hashlib.sha256(
            json.dumps(self._config, sort_keys=True).encode("utf-8")
        ).hexdigest()

        return Manifest(
            generation=prev_generation + 1,
            applied_at=datetime.now(timezone.utc).isoformat(),
            config_hash=config_hash,
            managed=managed,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/lib/reconciler/ -v`
Expected: PASS — Plan-3 Reconciler tests + new apply tests all green.

- [ ] **Step 5: Run the full suite**

Run: `PYTHONPATH=. pytest -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add dasik/lib/reconciler/reconciler.py tests/lib/reconciler/test_reconciler_apply.py
git commit -m "feat(reconciler): add apply() with destructive gating + persistence

Reconciler.apply() now drives each ActionPlanResult.action.apply(),
merges every action's managed_keys() into a new Manifest, and persists
via injected StateStore + GenerationStore. Destructive plans require
either --yes (CLI) or an interactive y/yes confirmation.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 3: CLI `apply` verb

**Files:**
- Modify: `dasik/__main__.py`
- Create: `tests/test_cli_apply.py`

Add `apply <config> [--target / | /mnt] [--yes]`:

- Reuses the same config-loading + `setup_actions()` + `Reconciler(...)` plumbing as `plan`.
- Builds the StateStore + GenerationStore against the resolved Target and passes them to Reconciler.
- Loads the active manifest (`StateStore.load().to_dict()`) and passes it through (so REMOVEs work after the first apply).
- Calls `Reconciler.build_plan()` → prints the rendered plan → calls `Reconciler.apply(plan, results, assume_yes=args.yes)`.
- On success: prints the new generation number. On user-cancel (destructive plan, answered `n`): prints "Aborted." and exits with code 1.
- Errors loading config / running plan: stderr message, non-zero exit.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_apply.py`:

```python
import json
from unittest.mock import patch, MagicMock

import pytest

from dasik import __main__ as cli


def _write_config(tmp_path, payload):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(payload))
    return p


def _empty_plan_pair():
    from dasik.lib.state.change import Plan
    return Plan(), []


def _nonempty_plan_pair():
    from dasik.lib.state.change import Plan, Change, Op
    p = Plan()
    p.add(Change("packages", Op.INSTALL, "git"))
    return p, []


def _patches():
    """Patch the CLI's external collaborators with one context-manager-friendly
    bundle. Returns the patchers; callers use `with`-stack."""
    return (
        patch("dasik.__main__.Reconciler"),
        patch("dasik.__main__.setup_actions", lambda: None),
        patch("dasik.__main__.get_default_registry"),
        patch("dasik.__main__.StateStore"),
        patch("dasik.__main__.GenerationStore"),
    )


def test_apply_verb_invokes_reconciler_build_and_apply(tmp_path, capsys):
    cfg = _write_config(tmp_path, {"packages": ["git"]})
    p_recon, _, p_reg, p_store, p_gen = _patches()
    with p_recon as Recon, p_reg as Reg, p_store as Store, p_gen as Gen:
        Reg.return_value.get_all_actions.return_value = []
        recon_inst = Recon.return_value
        recon_inst.build_plan.return_value = _nonempty_plan_pair()
        new_manifest = MagicMock()
        new_manifest.generation = 1
        recon_inst.apply.return_value = new_manifest
        Store.return_value.load.return_value.to_dict.return_value = {"managed": {}}

        rc = cli.main(["apply", str(cfg), "--yes"])

    assert rc == 0
    recon_inst.build_plan.assert_called_once()
    recon_inst.apply.assert_called_once()
    # assume_yes=True was passed because of --yes
    assert recon_inst.apply.call_args.kwargs.get("assume_yes") is True
    out = capsys.readouterr().out
    assert "git" in out  # plan was rendered
    assert "generation" in out.lower()


def test_apply_verb_empty_plan_no_apply_no_generation_printed(tmp_path, capsys):
    cfg = _write_config(tmp_path, {"packages": []})
    p_recon, _, p_reg, p_store, p_gen = _patches()
    with p_recon as Recon, p_reg as Reg, p_store as Store, p_gen as Gen:
        Reg.return_value.get_all_actions.return_value = []
        recon_inst = Recon.return_value
        recon_inst.build_plan.return_value = _empty_plan_pair()
        recon_inst.apply.return_value = None
        Store.return_value.load.return_value.to_dict.return_value = {"managed": {}}

        rc = cli.main(["apply", str(cfg), "--yes"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "No changes" in out
    # apply() may or may not be called for an empty plan — we don't care.


def test_apply_verb_passes_target_root_to_stores(tmp_path):
    cfg = _write_config(tmp_path, {"packages": []})
    p_recon, _, p_reg, p_store, p_gen = _patches()
    with p_recon as Recon, p_reg as Reg, p_store as Store, p_gen as Gen:
        Reg.return_value.get_all_actions.return_value = []
        recon_inst = Recon.return_value
        recon_inst.build_plan.return_value = _empty_plan_pair()
        Store.return_value.load.return_value.to_dict.return_value = {"managed": {}}
        cli.main(["apply", str(cfg), "--target", "/", "--yes"])

    store_target = Store.call_args.args[0]
    gen_target = Gen.call_args.args[0]
    assert store_target.root == "/"
    assert gen_target.root == "/"


def test_apply_verb_default_target_is_mnt(tmp_path):
    cfg = _write_config(tmp_path, {"packages": []})
    p_recon, _, p_reg, p_store, p_gen = _patches()
    with p_recon as Recon, p_reg as Reg, p_store as Store, p_gen as Gen:
        Reg.return_value.get_all_actions.return_value = []
        recon_inst = Recon.return_value
        recon_inst.build_plan.return_value = _empty_plan_pair()
        Store.return_value.load.return_value.to_dict.return_value = {"managed": {}}
        cli.main(["apply", str(cfg), "--yes"])

    assert Recon.call_args.kwargs["target"].root == "/mnt"


def test_apply_verb_without_yes_defaults_assume_yes_false(tmp_path):
    cfg = _write_config(tmp_path, {"packages": ["git"]})
    p_recon, _, p_reg, p_store, p_gen = _patches()
    with p_recon as Recon, p_reg as Reg, p_store as Store, p_gen as Gen:
        Reg.return_value.get_all_actions.return_value = []
        recon_inst = Recon.return_value
        recon_inst.build_plan.return_value = _nonempty_plan_pair()
        recon_inst.apply.return_value = MagicMock(generation=1)
        Store.return_value.load.return_value.to_dict.return_value = {"managed": {}}
        cli.main(["apply", str(cfg)])

    assert recon_inst.apply.call_args.kwargs.get("assume_yes") is False


def test_apply_verb_user_aborts_returns_nonzero(tmp_path, capsys):
    """When Reconciler.apply returns None on a non-empty plan, treat as cancel."""
    cfg = _write_config(tmp_path, {"packages": []})
    p_recon, _, p_reg, p_store, p_gen = _patches()
    with p_recon as Recon, p_reg as Reg, p_store as Store, p_gen as Gen:
        Reg.return_value.get_all_actions.return_value = []
        recon_inst = Recon.return_value
        recon_inst.build_plan.return_value = _nonempty_plan_pair()
        recon_inst.apply.return_value = None  # user said no
        Store.return_value.load.return_value.to_dict.return_value = {"managed": {}}

        rc = cli.main(["apply", str(cfg)])

    assert rc != 0
    err = capsys.readouterr().err
    assert "aborted" in err.lower() or "cancel" in err.lower()


def test_apply_verb_missing_config_exits_nonzero(tmp_path, capsys):
    missing = tmp_path / "nope.json"
    rc = cli.main(["apply", str(missing)])
    assert rc != 0
    assert "does not exist" in capsys.readouterr().err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/test_cli_apply.py -v`
Expected: FAIL — `dasik.__main__` has no `apply` verb, does not import `StateStore` / `GenerationStore`.

- [ ] **Step 3: Wire the `apply` verb**

In `dasik/__main__.py`:

(a) Update the import block at the top — add StateStore + GenerationStore:

```python
from dasik.lib.actions.actions_handler import ActionsHandler
from dasik.lib.actions.actions_handler_v2 import setup_actions
from dasik.lib.actions.action_registry import get_default_registry
from dasik.lib.reconciler.reconciler import Reconciler
from dasik.lib.state.generation_store import GenerationStore
from dasik.lib.state.state_store import StateStore
from dasik.lib.target.target import Target
```

(b) Update `_KNOWN_VERBS`:

```python
_KNOWN_VERBS = {"plan", "apply"}
```

(c) Inside `_build_parser`, after the `plan_p` block and before `return parser`, add the `apply` subparser:

```python
    apply_p = sub.add_parser(
        "apply",
        help="Converge the system to the config (DESTRUCTIVE)",
    )
    apply_p.add_argument("config", help="Path to the JSON configuration file")
    apply_p.add_argument(
        "--target",
        default="/mnt",
        help="Root commands run against (/ for the live host, /mnt for an "
             "install target). Default: /mnt.",
    )
    apply_p.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip the destructive-change confirmation prompt.",
    )
```

(d) Add `_cmd_apply` next to `_cmd_plan`:

```python
def _cmd_apply(config_path: Path, target_root: str, assume_yes: bool) -> int:
    """Run the destructive convergence flow."""
    try:
        config = json.loads(config_path.read_text())
    except Exception as e:
        print(f"Error loading config: {e}", file=sys.stderr)
        return 1

    setup_actions()
    registry = get_default_registry()
    target = Target(root=target_root)
    state_store = StateStore(target)
    gen_store = GenerationStore(target)

    manifest_dict = state_store.load().to_dict()

    reconciler = Reconciler(
        config=config,
        target=target,
        manifest=manifest_dict,
        action_metas=registry.get_all_actions(),
        state_store=state_store,
        generation_store=gen_store,
    )
    plan, results = reconciler.build_plan()
    print(plan.render())

    if plan.is_empty():
        return 0

    new_manifest = reconciler.apply(plan, results, assume_yes=assume_yes)
    if new_manifest is None:
        print("Aborted: no changes applied.", file=sys.stderr)
        return 1

    print(f"Applied: now at generation {new_manifest.generation}.")
    return 0
```

(e) In `main`, route the `apply` verb. After the existing `args.verb == "plan"` block and before `parser.print_help`, add:

```python
        if args.verb == "apply":
            path = _validate_config_file(args.config)
            if path is None:
                return 1
            return _cmd_apply(path, args.target, args.yes)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/test_cli_apply.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Run the full suite**

Run: `PYTHONPATH=. pytest -v`
Expected: PASS — Plan 1 + Plan 2 + Plan 3 + Plan 4 all green.

- [ ] **Step 6: Smoke (no real apply — `plan` verb against the live host)**

`apply` against the live host actually installs/removes packages, so the
manual smoke runs the existing read-only `plan` verb against a config that
includes both a pacman pkg and an `aur-` entry, to confirm `plan()` now
emits AUR changes too:

```bash
cat > /tmp/dasik-apply-smoke.json <<'EOF'
{"packages": ["bash", "aur-totally-fake-package"]}
EOF
PYTHONPATH=. python -m dasik plan /tmp/dasik-apply-smoke.json --target /
```

Expected: the output now contains an `install totally-fake-package` line in
addition to whatever pacman-side change `bash` triggers. Exit 0, no system
change. (Skip if pacman is not available in the runner.)

- [ ] **Step 7: Commit**

```bash
git add dasik/__main__.py tests/test_cli_apply.py
git commit -m "feat(cli): add 'apply' verb (destructive convergence)

apply wires JsonParser -> setup_actions() -> Reconciler with injected
StateStore/GenerationStore; loads the active manifest; renders the plan;
calls Reconciler.apply(assume_yes=args.yes). On user-cancel returns
exit 1 with 'Aborted'. On success prints the new generation number.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Self-Review (completed during planning)

**1. Spec coverage (Plan 4 portion):**
- §3.5 v3 action contract → destructive `apply()` for packages: Task 1. ✅
- §3.6 Reconciler `apply(plan, *, assume_yes)`: Task 2. ✅
- §3.8 CLI `apply`: Task 3. ✅
- §4 apply flow (load → build plan → render → confirm → run actions → update manifest → record generation): Tasks 2+3. ✅
- §5 safety (destructive changes need confirmation, `--yes` bypass): Tasks 2 (prompt) + 3 (flag). ✅
- §6 storage wiring (StateStore + GenerationStore): Tasks 2 (DI) + 3 (instantiation). ✅
- §3.8 `sync` / `rollback` / `generations` verbs — **explicitly out of scope** (declared in plan header).
- §3.7 ConfigWriter — **out of scope** (declared in plan header; not needed for apply).
- §5 live-host warning for `--target /` with destructive changes — NOT in this plan; the prompt covers the safety floor, and the warning is a UX polish that can land alongside the eventual `sync`/`rollback` verbs.

**2. Placeholder scan:** none. Every code/test step contains complete source. Plan-4-deferred items (sync/rollback/generations/ConfigWriter/disks/bootloader/new domains) are explicit scope statements, not gaps.

**3. Type consistency:**
- `Reconciler(config, target, manifest, action_metas, state_store=None, generation_store=None)` — matches Task 2 tests (`_make_reconciler` passes all five) and Task 3 CLI call-site (passes all five).
- `Reconciler.apply(plan, results, *, assume_yes=False, input_fn=input) -> Optional[Manifest]` — matches Task 2's `r.apply(plan, results, assume_yes=...)` and Task 3's `reconciler.apply(plan, results, assume_yes=...)`.
- `ActionPlanResult(action, changes)` — unchanged from Plan 3; Task 2 tests instantiate it directly.
- `PackagesAction.apply(changes) -> None` — overrides `AbstractAction.apply` (Plan-2 default no-op). Matches Task 1 tests calling `a.apply(changes)`.
- `PackagesAction._apply_aur_install(pkgs: list[str]) -> None` — private helper used by `apply`; Task 1 patches it with `patch.object(PackagesAction, "_apply_aur_install")`.
- `Manifest(generation, applied_at, config_hash, managed)` matches the existing dataclass in `dasik/lib/state/manifest.py` (the `version` field defaults to `STATE_VERSION`).
- `StateStore(target).load() -> Manifest`, `StateStore(target).save(manifest) -> None`, `GenerationStore(target).new(config, manifest_dict) -> int` — all match Task 2/3.
- `Plan.is_empty()`, `Plan.destructive() -> list[Change]`, `Plan.render() -> str` — unchanged from Plan 1.
- `Change(domain, op, item, reason="")` with `Change.destructive` property — unchanged from Plan 1; `REMOVE` is destructive, `INSTALL` is not.
- CLI `main(argv=None) -> int` — unchanged from Plan 3; new verb routes through the existing dispatch.

**Decision notes:**
- **Why injection of StateStore/GenerationStore into the Reconciler:** keeps the orchestrator pure and side-effect-free in unit tests (no filesystem). The CLI is the only place that constructs them.
- **Why `--yes` bypasses the prompt rather than implying "yes" everywhere:** future safety polish (e.g. live-host extra warning per spec §5) can hook the same prompt path without breaking `--yes`. The CLI's only contract is "answer yes by default with `--yes`."
- **Why INSTALLs run before REMOVEs:** if the REMOVE step fails partway, the system is at least left with the new packages in place; if INSTALL fails first, no destructive cleanup has happened yet. Same principle the legacy installer follows (it never removes).
- **Why AUR write-path goes through `subprocess.run` directly instead of `Command.execute`:** the makepkg dance needs `su - <user> -c "..."` which is a shell pipeline `Command.execute` doesn't model. Reproducing the legacy helper's flow keeps Plan 4 small; refactoring `Command.execute` to handle shell pipelines is its own change.
- **Why `_target_argv` instead of hard-coding `arch-chroot /mnt`:** Plan 1 made `Command` Target-aware so `pacman -S` runs against `/mnt` when installing and `/` on day-2. The AUR helpers need the same routing, so we drop the legacy `arch-chroot /mnt` literals.
- **Why empty-plan returns generation 0 / no-op:** matches the spec — "running the same JSON again does nothing." Empty plan ⇒ no manifest change ⇒ no new generation; the CLI prints `No changes - system matches config.` and exits 0.
- **Why the user-cancel exit code is non-zero:** treats user-cancel as "did not apply what was asked", which is what scripts care about. The stderr "Aborted" makes it obvious it was a voluntary cancel, not a crash.
