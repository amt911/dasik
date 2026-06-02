# MVP PR B: disks v3 domain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Bring `DiskPartitionAction` under the v3 verb pipeline so `plan`/`apply` reconcile disk partitioning idempotently, with a conservative bootstrap safety model.

**Architecture:** Wrap the existing (destructive) `run()`/`_process_disk`/`_format`/`_mount` machinery with a v3 contract. Add an idempotency/decision layer (`actual`/`plan`) and a dict+context `__init__`; do **not** rewrite the dangerous partitioning core. Bootstrap-conservative semantics: a disk whose desired partition labels are all present is converged (no-op); a non-converged disk is (re)partitioned only when `wipe_disk` is set or the disk has no partition table — a populated, non-matching disk is left untouched with a warning.

**Tech Stack:** Python 3.10+, pydantic (`DisksConfiguration`/`DiskLayout`), pytest, `unittest.mock`. Destructive `apply()` is never run in tests — only the decision layer is exercised, with `Command.execute` mocked.

**Spec:** `docs/superpowers/specs/2026-06-02-mvp-nixos-expansion-design.md` (slice 4).

**Safety:** This action partitions/formats/mounts real disks. Tests must never touch a real device. `apply()`/`_process_disk` are asserted via mocks, never executed against hardware.

**Branch:** `feat-mvp-disks-v3` (already created off `main`).

**Pre-flight:**
- `dasik/lib/actions/disk_partition_action.py` — existing destructive machinery to preserve.
- `dasik/lib/models/disk_model.py` — `DisksConfiguration{disks: [DiskLayout]}`, `DiskLayout{device, partition_table, wipe_disk, partitions: [Partition{label, ...}]}`.
- `dasik/lib/actions/abstract_action.py` — `__init__(config, context)`, v3 contract, `empty_config`.
- `dasik/lib/state/change.py` — `Change`, `Op`.

---

## Task B.1: v3 contract + idempotency around the existing machinery

**Files:**
- Modify: `dasik/lib/actions/disk_partition_action.py`
- Test (create): `tests/lib/actions/test_disk_partition_action.py`

- [ ] **Step 1: Write the failing decision-layer tests**

Create `tests/lib/actions/test_disk_partition_action.py`:

```python
from unittest.mock import patch

from dasik.lib.actions.disk_partition_action import DiskPartitionAction
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.target.target import Target
from dasik.lib.state.change import Op


def _ctx(root="/mnt"):
    return ActionContext(target=Target(root=root))


def _cfg(device="/dev/vda", wipe=False):
    return {"disks": [{
        "device": device,
        "partition_table": "gpt",
        "wipe_disk": wipe,
        "partitions": [
            {"label": "boot", "size": "512MiB", "filesystem": "fat32",
             "partition_type": "esp", "mountpoint": "/boot", "format": True},
            {"label": "root", "size": "rest", "filesystem": "ext4",
             "partition_type": "linux", "mountpoint": "/", "format": True},
        ],
    }]}


def test_is_v3_true():
    assert DiskPartitionAction.is_v3() is True


def test_empty_config_is_dict():
    assert DiskPartitionAction.empty_config() == {}


def test_no_disks_plan_empty():
    a = DiskPartitionAction({}, _ctx())
    assert a.plan(managed=[]) == []
    assert a.actual() == set()


def test_actual_converged_when_labels_present():
    a = DiskPartitionAction(_cfg(), _ctx())
    with patch.object(DiskPartitionAction, "_device_labels", return_value={"boot", "root", "swap"}):
        assert a.actual() == {"/dev/vda"}


def test_actual_empty_when_labels_missing():
    a = DiskPartitionAction(_cfg(), _ctx())
    with patch.object(DiskPartitionAction, "_device_labels", return_value=set()):
        assert a.actual() == set()


def test_plan_empty_when_converged():
    a = DiskPartitionAction(_cfg(), _ctx())
    with patch.object(DiskPartitionAction, "_device_labels", return_value={"boot", "root"}):
        assert a.plan(managed=[]) == []


def test_plan_install_when_empty_disk():
    a = DiskPartitionAction(_cfg(wipe=False), _ctx())
    with patch.object(DiskPartitionAction, "_device_labels", return_value=set()), \
         patch.object(DiskPartitionAction, "_has_partition_table", return_value=False):
        changes = a.plan(managed=[])
    assert changes and changes[0].op is Op.INSTALL and changes[0].item == "/dev/vda"


def test_plan_install_when_wipe():
    a = DiskPartitionAction(_cfg(wipe=True), _ctx())
    with patch.object(DiskPartitionAction, "_device_labels", return_value={"old"}), \
         patch.object(DiskPartitionAction, "_has_partition_table", return_value=True):
        changes = a.plan(managed=[])
    assert changes and changes[0].op is Op.INSTALL


def test_plan_skips_populated_disk_without_wipe(capsys):
    a = DiskPartitionAction(_cfg(wipe=False), _ctx())
    with patch.object(DiskPartitionAction, "_device_labels", return_value={"old"}), \
         patch.object(DiskPartitionAction, "_has_partition_table", return_value=True):
        changes = a.plan(managed=[])
    assert changes == []                       # refuse to clobber
    assert "wipe_disk" in capsys.readouterr().out


def test_apply_processes_changed_disks():
    a = DiskPartitionAction(_cfg(wipe=True), _ctx())
    with patch.object(DiskPartitionAction, "_device_labels", return_value=set()), \
         patch.object(DiskPartitionAction, "_has_partition_table", return_value=False), \
         patch.object(DiskPartitionAction, "_process_disk") as proc:
        a.apply(a.plan(managed=[]))
    proc.assert_called_once()


def test_apply_noop_when_no_changes():
    a = DiskPartitionAction(_cfg(), _ctx())
    with patch.object(DiskPartitionAction, "_process_disk") as proc:
        a.apply([])
    proc.assert_not_called()


def test_managed_keys_lists_converged():
    a = DiskPartitionAction(_cfg(), _ctx())
    with patch.object(DiskPartitionAction, "_device_labels", return_value={"boot", "root"}):
        assert a.managed_keys() == {"disks": ["/dev/vda"]}


def test_import_state_empty():
    a = DiskPartitionAction(_cfg(), _ctx())
    assert a.import_state(managed=[]) == {}


def test_device_labels_parses_lsblk():
    a = DiskPartitionAction(_cfg(), _ctx())
    from unittest.mock import MagicMock
    with patch("dasik.lib.actions.disk_partition_action.Command.execute",
               return_value=MagicMock(stdout=b"boot\nroot\n\n")):
        assert a._device_labels("/dev/vda") == {"boot", "root"}


def test_name_and_optional():
    a = DiskPartitionAction({})
    assert a.name == "Disk Partitioning"
    assert a.is_optional is True
```

- [ ] **Step 2: Run, expect failure**

Run: `pytest tests/lib/actions/test_disk_partition_action.py -q`
Expected: failures (`is_v3` False, no `actual`/`plan`/`_device_labels`/`empty_config`; `__init__` rejects two args / dict).

- [ ] **Step 3: Add the v3 layer to the action**

In `dasik/lib/actions/disk_partition_action.py`:

Update the imports at the top (add `Change`/`Op` and `DiskLayout` is already imported):

```python
from dasik.lib.state.change import Change, Op
```

Replace the `__init__` (currently `def __init__(self, disks_config: DisksConfiguration)`) with a dict+context-aware version, and add the `_DOMAIN` attribute:

```python
class DiskPartitionAction(AbstractAction):
    """Action to handle disk partitioning declaratively (v3 domain "disks")."""

    _DOMAIN = "disks"

    def __init__(self, config=None, context=None):
        super().__init__(config, context)
        self.disks: List[DiskLayout] = self._parse(config)
        self.partition_map: Dict[str, str] = {}

    @staticmethod
    def _parse(config) -> "List[DiskLayout]":
        if config is None:
            return []
        if isinstance(config, DisksConfiguration):
            return list(config.disks)
        if isinstance(config, dict):
            raw = config.get("disks")
            if not raw:
                return []
            return [DiskLayout.model_validate(d) for d in raw]
        return []

    @property
    def name(self) -> str:
        return "Disk Partitioning"

    @property
    def is_optional(self) -> bool:
        return True

    @classmethod
    def empty_config(cls):
        return {}
```

Keep the old `KEY_NAME`/`can_incrementally_change`/`_before_check`/`after_check`/`do_action`/`run` and all `_process_disk`/`_create_*`/`_format_*`/`_mount_*` helpers **unchanged**, except: `do_action` and `run` referenced `self.disks_config.disks`; update those two references to iterate `self.disks` directly.

In `run`:
```python
    def run(self) -> None:
        print("Starting disk partitioning process...")
        for disk in self.disks:
            print(f"\nProcessing disk: {disk.device}")
            self._process_disk(disk)
        print("\nDisk partitioning completed successfully!")
```

In `_before_check`:
```python
    def _before_check(self) -> bool:
        return len(self.disks) > 0
```

Add the v3 contract methods (after `is_optional`, before the legacy helpers):

```python
    # --- v3 contract -------------------------------------------------- #

    def _device_labels(self, device: str) -> set:
        """Partition labels currently present on *device* (empty if none)."""
        try:
            result = Command.execute("lsblk", ["-no", "LABEL", device])
            out = result.stdout
            if isinstance(out, bytes):
                out = out.decode("utf-8", "replace")
            return {line.strip() for line in out.splitlines() if line.strip()}
        except Exception:
            return set()

    def _disk_converged(self, disk: DiskLayout) -> bool:
        want = {p.label for p in disk.partitions}
        return bool(want) and want.issubset(self._device_labels(disk.device))

    def actual(self) -> set:
        return {d.device for d in self.disks if self._disk_converged(d)}

    def managed_keys(self) -> dict:
        return {self._DOMAIN: sorted(self.actual())}

    def plan(self, managed) -> list:
        changes = []
        for disk in self.disks:
            if self._disk_converged(disk):
                continue
            if disk.wipe_disk or not self._has_partition_table(disk.device):
                changes.append(Change(
                    self._DOMAIN, Op.INSTALL, disk.device,
                    reason="wipe_disk" if disk.wipe_disk else "empty disk",
                ))
            else:
                print(
                    f"  Warning: {disk.device} is populated and does not match the "
                    f"declared layout; set wipe_disk:true to repartition. Skipping."
                )
        return changes

    def apply(self, changes) -> None:
        if not changes:
            return
        targets = {c.item for c in changes}
        for disk in self.disks:
            if disk.device in targets:
                self._process_disk(disk)

    def import_state(self, managed=None) -> dict:
        # Disks are user-declared; sync does not rewrite the section.
        return {}
```

- [ ] **Step 4: Run, expect pass**

Run: `pytest tests/lib/actions/test_disk_partition_action.py -q`
Expected: all PASS.

- [ ] **Step 5: Full suite + coverage**

Run: `pytest -q`
Expected: all PASS (disks now v3; absent-`disks` configs still skipped, so verb-integration unaffected).

Run: `pytest --cov=dasik -q`
Expected: total ≥ 80%. The destructive `_process_disk`/`_create_*`/`_format_*`/`_mount_*` bodies stay uncovered (asserted via mock per CLAUDE.md). If coverage dips below 80% because the large untested destructive helpers now count, add an `omit` entry with justification to `[tool.coverage.run]` in `pyproject.toml`:
```toml
# destructive partitioning/format/mount — covered via plan/actual + mocked Command
omit = ["dasik/lib/actions/disk_partition_action.py"]
```
Prefer NOT to omit if the gate already passes.

- [ ] **Step 6: Commit**

```bash
git add dasik/lib/actions/disk_partition_action.py tests/lib/actions/test_disk_partition_action.py
git commit -m "feat(disks): v3 domain with conservative bootstrap idempotency

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

If a coverage omit was needed:
```bash
git add pyproject.toml && git commit -m "test(cov): omit destructive disk partitioning body (covered via mocks)"
```

---

## Self-review notes (spec coverage)

- Spec slice 4 "actual=read partitions, plan=create when missing, apply=destructive gated by wipe_disk/format" → Task B.1. ✓
- User decision "bootstrap conservador: re-partition only on wipe or empty; never clobber a populated disk" → `plan()` gating + `test_plan_skips_populated_disk_without_wipe`. ✓
- Safety "never run destructive apply in tests" → `_process_disk` mocked; `Command.execute` mocked. ✓
- Preserve destructive core (don't rewrite) → only `__init__`, `run`, `_before_check` touched; partition/format/mount helpers unchanged. ✓
- Naming: `_device_labels`, `_disk_converged`, `_has_partition_table`, `_process_disk`, `_DOMAIN`, `empty_config` — consistent. ✓
- Mounting stays at `/mnt` (install flow); day-2 (`target=/`) disks are converged no-ops. Documented limitation; full apply ordering handled in PR D. ✓
