# kernel_cmdline v3 Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate `kernel_cmdline` onto the v3 set-math contract, resolving the LUKS UUID at plan time from the open mapping so the config stays portable across machines and device paths.

**Architecture:** `KernelCmdlineAction` gains v3 methods (`actual`/`plan`/`apply`/`managed_keys`/`import_state`) over the **token set** of the kernel cmdline. Desired = explicit params ∪ auto-derived params; the LUKS param's UUID is resolved via `cryptsetup status <luks_name>` → backing device → `blkid` (device-portable, robust to extra partitions). `sync` round-trips explicit params only (no UUID leak). Legacy `is_needed`/`execute` kept.

**Tech Stack:** Python 3.10+, pytest/pytest-cov.

Spec: `docs/superpowers/specs/2026-05-31-kernel-cmdline-v3-design.md`.

**Test runner:**
```bash
python -m venv /tmp/dasik-venv && /tmp/dasik-venv/bin/pip install -q pytest pytest-cov colorama pydantic
PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest ...
```

---

## Task 1: portable LUKS UUID resolution + lazy derivation

**Files:**
- Modify: `dasik/lib/actions/kernel_cmdline_action.py`
- Test: `tests/lib/actions/test_kernel_cmdline_action.py` (update the derive tests)

- [ ] **Step 1: Update/add the failing tests**

In `tests/lib/actions/test_kernel_cmdline_action.py`, **replace** the existing
`test_derive_encryption_params` with the resolving version and add the resolver tests. (Keep
the other existing tests.) Add these imports at the top if missing:

```python
from unittest.mock import MagicMock, patch
```

Add/replace:

```python
def _enc_cfg():
    return {"disks": {"disks": [{"partitions": [
        {"mountpoint": "/", "encrypt": True, "luks_name": "croot", "filesystem": "ext4"}]}]}}


def _fake_exec(mapping):
    """mapping: argv-prefix tuple -> stdout bytes. Matches on (cmd, args[0])."""
    def run(cmd, args, *a, **k):
        key = (cmd, args[0] if args else "")
        return MagicMock(stdout=mapping.get(key, b""), returncode=0)
    return run


def test_luks_backing_device_parses_status():
    a = KernelCmdlineAction(_enc_cfg())
    status = b"/dev/mapper/croot is active.\n  type:    LUKS2\n  device:  /dev/sda2\n"
    with patch("dasik.lib.actions.kernel_cmdline_action.Command.execute",
               _fake_exec({("cryptsetup", "status"): status})):
        assert a._luks_backing_device("croot") == "/dev/sda2"


def test_luks_backing_device_none_on_failure():
    a = KernelCmdlineAction(_enc_cfg())
    fail = MagicMock(return_value=MagicMock(stdout=b"", returncode=4))
    with patch("dasik.lib.actions.kernel_cmdline_action.Command.execute", fail):
        assert a._luks_backing_device("croot") is None


def test_resolve_luks_uuid_via_blkid():
    a = KernelCmdlineAction(_enc_cfg())
    status = b"  device:  /dev/sda2\n"
    with patch("dasik.lib.actions.kernel_cmdline_action.Command.execute",
               _fake_exec({("cryptsetup", "status"): status,
                           ("blkid", "-s"): b"DEAD-BEEF\n"})):
        assert a._resolve_luks_uuid("croot") == "DEAD-BEEF"


def test_derive_encryption_resolves_real_uuid():
    a = KernelCmdlineAction(_enc_cfg())
    status = b"  device:  /dev/sda2\n"
    with patch("dasik.lib.actions.kernel_cmdline_action.Command.execute",
               _fake_exec({("cryptsetup", "status"): status,
                           ("blkid", "-s"): b"U1\n"})):
        derived = a._derive_from_disks()
    assert "rd.luks.name=U1=croot" in derived
    assert "root=/dev/mapper/croot rw" in derived


def test_derive_omits_luks_param_when_unresolved():
    a = KernelCmdlineAction(_enc_cfg())
    fail = MagicMock(return_value=MagicMock(stdout=b"", returncode=4))
    with patch("dasik.lib.actions.kernel_cmdline_action.Command.execute", fail):
        derived = a._derive_from_disks()
    assert not any(d.startswith("rd.luks.name=") for d in derived)
    assert "root=/dev/mapper/croot rw" in derived
```

If the old `test_derive_encryption_params` (expecting `rd.luks.name=<ROOT_UUID>=croot`) is
still present, delete it.

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_kernel_cmdline_action.py -k "luks or resolve or derive" -v`
Expected: FAIL — `_luks_backing_device`/`_resolve_luks_uuid` missing; `_derive_from_disks`
still emits the placeholder / is a staticmethod.

- [ ] **Step 3: Implement resolution + make derivation an instance method**

In `dasik/lib/actions/kernel_cmdline_action.py`:

1. Add the import:
```python
from ..command_worker.command_worker import Command
```

2. Replace `__init__` so derivation is lazy (no cryptsetup at construction):
```python
    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        self._cfg = cfg
        self.bootloader: str = cfg.get("bootloader", "grub")
        self.explicit_params: List[str] = cfg.get("kernel_cmdline", [])
```

3. Add a target helper:
```python
    def _target(self):
        return getattr(self.context, "target", None) if self.context else None
```

4. Add the resolver helpers (run on the **host** — device-mapper/blkid are host-level):
```python
    def _luks_backing_device(self, luks_name: str) -> Optional[str]:
        result = Command.execute("cryptsetup", ["status", luks_name])
        if getattr(result, "returncode", 1) != 0:
            return None
        stdout = getattr(result, "stdout", b"") or b""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        for line in stdout.splitlines():
            if "device:" in line:
                return line.split("device:")[1].strip()
        return None

    def _resolve_luks_uuid(self, luks_name: str) -> Optional[str]:
        dev = self._luks_backing_device(luks_name)
        if not dev:
            return None
        result = Command.execute("blkid", ["-s", "UUID", "-o", "value", dev])
        stdout = getattr(result, "stdout", b"") or b""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        uuid = stdout.strip()
        return uuid or None
```

5. Replace `_derive_from_disks` — make it an **instance** method that resolves the UUID:
```python
    def _derive_from_disks(self) -> List[str]:
        params: List[str] = []
        disks = self._cfg.get("disks", {})
        if not isinstance(disks, dict):
            return params
        for disk in disks.get("disks", []):
            for part in disk.get("partitions", []):
                if part.get("mountpoint") != "/":
                    continue
                if part.get("encrypt"):
                    dm_name = part.get("luks_name", "cryptroot")
                    uuid = self._resolve_luks_uuid(dm_name)
                    if uuid:
                        params.append(f"rd.luks.name={uuid}={dm_name}")
                    params.append(f"root=/dev/mapper/{dm_name} rw")
                fs = part.get("filesystem", "")
                if fs == "btrfs":
                    subvols = part.get("btrfs_subvolumes", [])
                    root_sv = next((s for s in subvols if s.get("mountpoint") == "/"), None)
                    sv_name = root_sv["name"] if root_sv else "@"
                    options = root_sv.get("mount_options", ["compress-force=zstd"]) if root_sv else ["compress-force=zstd"]
                    opts_str = ",".join(options + [f"subvol={sv_name}"])
                    params.append(f"rootflags={opts_str}")
        return params
```

Keep `_merge` as-is (still a staticmethod). Remove the now-stale `self._auto_params` /
`self.desired_params` attributes from `__init__` (they are recomputed lazily below in Task 2).
The legacy `_missing_params`/`is_needed`/`execute`/`verify` will be repointed in Task 5; for
now they may reference a `desired_params` property — add it so nothing breaks:
```python
    @property
    def desired_params(self) -> List[str]:
        return self._merge(self._derive_from_disks(), self.explicit_params)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_kernel_cmdline_action.py -v`
Expected: PASS (resolver + derive tests; the kept legacy tests still pass — `desired_params`
is now a property but behaves the same for explicit-only configs).

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/kernel_cmdline_action.py tests/lib/actions/test_kernel_cmdline_action.py
git commit -m "feat(kernel_cmdline): resolve real LUKS UUID via open mapping (portable)"
```

---

## Task 2: v3 `actual()` + token helpers + `plan()` + `managed_keys()`

**Files:**
- Modify: `dasik/lib/actions/kernel_cmdline_action.py`
- Test: `tests/lib/actions/test_kernel_cmdline_action.py` (append)

- [ ] **Step 1: Write the failing tests**

Append:

```python
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.target.target import Target
from dasik.lib.state.change import Op


def _ctx(root="/"):
    return ActionContext(target=Target(root=root))


def _grub_action(cfg, current_cmdline):
    a = KernelCmdlineAction(cfg, _ctx("/"))
    a.actual = lambda: set(current_cmdline.split())
    return a


def test_desired_tokens_flattens_and_merges():
    a = KernelCmdlineAction({"kernel_cmdline": ["quiet", "loglevel=3"]}, _ctx("/"))
    toks = a._desired_tokens()
    assert "quiet" in toks and "loglevel=3" in toks


def test_is_v3_true():
    assert KernelCmdlineAction.is_v3() is True


def test_plan_installs_missing_explicit():
    a = _grub_action({"kernel_cmdline": ["mitigations=off"]}, "quiet")
    changes = a.plan(managed=[])
    assert [(c.op, c.item) for c in changes] == [(Op.INSTALL, "mitigations=off")]


def test_plan_removes_owned_not_declared():
    a = _grub_action({"kernel_cmdline": []}, "quiet oldparam")
    changes = a.plan(managed=["oldparam"])
    assert [(c.op, c.item) for c in changes] == [(Op.REMOVE, "oldparam")]


def test_plan_empty_when_converged():
    a = _grub_action({"kernel_cmdline": ["quiet"]}, "quiet other")
    assert a.plan(managed=["quiet"]) == []


def test_managed_keys_lists_desired_tokens():
    a = KernelCmdlineAction({"kernel_cmdline": ["quiet"]}, _ctx("/"))
    assert a.managed_keys() == {"kernel_cmdline": ["quiet"]}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_kernel_cmdline_action.py -k "desired_tokens or is_v3 or plan or managed_keys" -v`
Expected: FAIL — `_desired_tokens`/`plan`/`managed_keys` missing; `is_v3()` False.

- [ ] **Step 3: Implement tokens, actual, plan, managed_keys**

Add to `KernelCmdlineAction`:

```python
    _DOMAIN = "kernel_cmdline"

    @staticmethod
    def _tokens(entries: List[str]) -> List[str]:
        out: List[str] = []
        for entry in entries:
            out.extend(entry.split())
        return out

    def _desired_tokens(self) -> List[str]:
        merged = self._merge(self._derive_from_disks(), self.explicit_params)
        seen: set = set()
        deduped: List[str] = []
        for tok in self._tokens(merged):
            if tok not in seen:
                seen.add(tok)
                deduped.append(tok)
        return deduped

    def _current_cmdline(self) -> str:
        if self.bootloader == "grub":
            return self._current_params_grub()
        entries = self._sdboot_entries()
        return self._current_params_sdboot(entries[0]) if entries else ""

    def actual(self) -> set:
        if self._target() is None:
            return set()
        return set(self._current_cmdline().split())

    def plan(self, managed):
        from ..state.set_math import compute_changes
        changes, _drift = compute_changes(
            self._DOMAIN,
            desired=self._desired_tokens(),
            managed=managed,
            actual=self.actual(),
        )
        return changes

    def managed_keys(self) -> dict:
        return {self._DOMAIN: self._desired_tokens()}
```

Make the file-path helpers target-aware — replace `_grub_file`/`_sdboot_entries`:
```python
    def _grub_file(self) -> str:
        t = self._target()
        return t.path("/etc/default/grub") if t is not None else "/mnt/etc/default/grub"

    def _sdboot_entries(self) -> List[str]:
        t = self._target()
        entries_dir = t.path("/boot/loader/entries") if t is not None else "/mnt/boot/loader/entries"
        if os.path.isdir(entries_dir):
            return [os.path.join(entries_dir, f) for f in os.listdir(entries_dir) if f.endswith(".conf")]
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_kernel_cmdline_action.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/kernel_cmdline_action.py tests/lib/actions/test_kernel_cmdline_action.py
git commit -m "feat(kernel_cmdline): v3 actual()/plan()/managed_keys() over token set"
```

---

## Task 3: v3 `apply()` (rewrite cmdline + regen)

**Files:**
- Modify: `dasik/lib/actions/kernel_cmdline_action.py`
- Test: `tests/lib/actions/test_kernel_cmdline_action.py` (append)

- [ ] **Step 1: Write the failing tests**

Append:

```python
from dasik.lib.state.change import Change


def test_apply_grub_rewrites_line_and_regens():
    a = KernelCmdlineAction({"bootloader": "grub"}, _ctx("/"))
    a._current_cmdline = lambda: "quiet old"
    grub_text = 'GRUB_CMDLINE_LINUX="quiet old"\n'
    from unittest.mock import mock_open
    changes = [Change("kernel_cmdline", Op.INSTALL, "new=1"),
               Change("kernel_cmdline", Op.REMOVE, "old")]
    with patch("builtins.open", mock_open(read_data=grub_text)) as m, \
         patch("dasik.lib.actions.kernel_cmdline_action.Command.execute") as run:
        a.apply(changes)
    body = "".join(c.args[0] for c in m().write.call_args_list)
    assert "new=1" in body and "quiet" in body and "old" not in body.replace("loglevel", "")
    assert (run.call_args.args[0], run.call_args.args[1]) == (
        "grub-mkconfig", ["-o", "/boot/grub/grub.cfg"])


def test_apply_noop_without_target():
    a = KernelCmdlineAction({"bootloader": "grub"}, None)
    with patch("dasik.lib.actions.kernel_cmdline_action.Command.execute") as run, \
         patch("builtins.open") as op:
        a.apply([Change("kernel_cmdline", Op.INSTALL, "x")])
    run.assert_not_called()
    op.assert_not_called()


def test_apply_empty_changes_noop():
    a = KernelCmdlineAction({"bootloader": "grub"}, _ctx("/"))
    with patch("dasik.lib.actions.kernel_cmdline_action.Command.execute") as run:
        a.apply([])
    run.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_kernel_cmdline_action.py -k apply -v`
Expected: FAIL — v3 `apply` not implemented (base no-op).

- [ ] **Step 3: Implement `apply` + a line-rewriter**

Add to `KernelCmdlineAction` (this v3 `apply` overrides the legacy `execute` path used by the
reconciler):

```python
    def _new_tokens(self, changes) -> List[str]:
        from ..state.change import Op as _Op
        installs = [c.item for c in changes if c.op is _Op.INSTALL]
        removes = {c.item for c in changes if c.op is _Op.REMOVE}
        current = [t for t in self._current_cmdline().split() if t not in removes]
        for tok in installs:
            if tok not in current:
                current.append(tok)
        return current

    def apply(self, changes) -> None:
        if self._target() is None or not changes:
            return
        line = " ".join(self._new_tokens(changes))
        if self.bootloader == "grub":
            self._write_grub(line)
            target = self._target()
            Command.execute("grub-mkconfig", ["-o", "/boot/grub/grub.cfg"], target=target)
        else:
            for entry in self._sdboot_entries():
                self._write_sdboot(entry, line)

    def _write_grub(self, line: str) -> None:
        path = self._grub_file()
        with open(path, "r") as f:
            text = f.read()
        text = re.sub(r'^GRUB_CMDLINE_LINUX="(.*)"',
                      f'GRUB_CMDLINE_LINUX="{line}"', text, flags=re.MULTILINE)
        with open(path, "w") as f:
            f.write(text)

    def _write_sdboot(self, entry_file: str, line: str) -> None:
        with open(entry_file, "r") as f:
            lines = f.readlines()
        with open(entry_file, "w") as f:
            for ln in lines:
                if ln.startswith("options "):
                    f.write(f"options {line}\n")
                else:
                    f.write(ln)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_kernel_cmdline_action.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/kernel_cmdline_action.py tests/lib/actions/test_kernel_cmdline_action.py
git commit -m "feat(kernel_cmdline): v3 apply() rewrites grub/sd-boot cmdline + regen"
```

---

## Task 4: v3 `import_state()` (explicit only — no UUID leak)

**Files:**
- Modify: `dasik/lib/actions/kernel_cmdline_action.py`
- Test: `tests/lib/actions/test_kernel_cmdline_action.py` (append)

- [ ] **Step 1: Write the failing tests**

Append:

```python
def test_import_state_returns_explicit_only():
    a = KernelCmdlineAction(
        {"kernel_cmdline": ["quiet", "loglevel=3"], **_enc_cfg()}, _ctx("/"))
    # Even though the encrypted disk would derive a rd.luks.name token,
    # import_state must return ONLY the explicit params (no resolved UUID).
    frag = a.import_state(managed=[])
    assert frag == {"kernel_cmdline": ["quiet", "loglevel=3"]}


def test_import_state_has_no_uuid_token():
    a = KernelCmdlineAction({"kernel_cmdline": ["quiet"], **_enc_cfg()}, _ctx("/"))
    frag = a.import_state(managed=[])
    assert not any("rd.luks.name" in t for t in frag["kernel_cmdline"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_kernel_cmdline_action.py -k import_state -v`
Expected: FAIL — base `import_state` returns `{}`.

- [ ] **Step 3: Implement `import_state`**

Add to `KernelCmdlineAction`:

```python
    def import_state(self, managed=None) -> dict:
        # Round-trip the declared explicit params only. Never emit the resolved
        # LUKS UUID — keeping the config portable across machines.
        return {self._DOMAIN: list(self.explicit_params)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_kernel_cmdline_action.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/kernel_cmdline_action.py tests/lib/actions/test_kernel_cmdline_action.py
git commit -m "feat(kernel_cmdline): v3 import_state() returns explicit params only (no UUID leak)"
```

---

## Task 5: legacy path sanity + sample + full suite + gate

**Files:**
- Modify: `config/install-megamix.json` (drop the hand-written LUKS line — now auto-derived)
- Test: full suite

- [ ] **Step 1: Verify the legacy path still works**

The legacy `is_needed`/`execute`/`verify`/`_missing_params` reference the `desired_params`
property (added in Task 1) and the existing `_param_present`/`_current_params_*` helpers — no
change needed. Confirm the existing legacy tests are still green:
```bash
PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest tests/lib/actions/test_kernel_cmdline_action.py -k "is_needed or param_present or current_params or sdboot or merge or name" -v
```
Expected: PASS.

- [ ] **Step 2: Simplify the sample's kernel_cmdline (portability)**

In `config/install-megamix.json`, the `kernel_cmdline` array currently hand-writes a LUKS
line with a placeholder UUID. Since the LUKS param is now auto-derived with the real UUID,
remove that hand-written entry, leaving only genuinely-explicit params. For example, if it is:
```jsonc
  "kernel_cmdline": [
    "rd.luks.name=UUID=ROOTUUID=cryptroot root=/dev/mapper/cryptroot rw",
    "quiet",
    "loglevel=3"
  ],
```
change it to:
```jsonc
  "kernel_cmdline": [
    "quiet",
    "loglevel=3"
  ],
```
(Keep whatever non-LUKS params the sample already had; only drop the placeholder LUKS/root
line. If the array had only the LUKS line, replace it with `["quiet"]`.)

- [ ] **Step 3: Validate the sample parses**

Run:
```bash
PYTHONPATH=. /tmp/dasik-venv/bin/python -c "from dasik.lib.json_parser.json_parser import JsonParser; JsonParser('config/install-megamix.json').debug(); print('OK')"
```
Expected: `OK`.

- [ ] **Step 4: Full suite + coverage**

Run: `PYTHONPATH=. /tmp/dasik-venv/bin/python -m pytest --cov=dasik -q`
Expected: all pass; `Required test coverage of 80.0% reached`.

- [ ] **Step 5: Commit**

```bash
git add config/install-megamix.json
git commit -m "docs(config): drop hand-written LUKS cmdline (now auto-derived) in megamix"
```

---

## Self-Review notes

- **Spec coverage:** Task 1 = portable UUID resolution + lazy derive; Task 2 = tokens/actual/plan/managed_keys; Task 3 = apply; Task 4 = import_state (explicit only); Task 5 = legacy sanity + sample + gate. All spec sections covered.
- **Type consistency:** `_DOMAIN="kernel_cmdline"`, helpers `_luks_backing_device`/`_resolve_luks_uuid`/`_derive_from_disks`(instance)/`_tokens`/`_desired_tokens`/`_current_cmdline`/`_new_tokens`/`_write_grub`/`_write_sdboot`/`_target`, `Op.INSTALL`/`Op.REMOVE` — consistent across tasks.
- **Reconciler integration:** registered `config_key="__root__"`; subclassing/overriding `plan` makes `is_v3()` True so `build_plan`/`sync` include it; `_domain_for` sees the single `kernel_cmdline` key; `import_state` returns explicit-only so `sync` stays portable.
- **Idempotency:** once the LUKS UUID resolves to the real value, the derived token equals the on-disk token, so `plan()` is empty when converged (`test_plan_empty_when_converged` covers the explicit case; the resolved-UUID case is exercised by `test_derive_encryption_resolves_real_uuid` + `actual` containing the same token).
- **Host vs chroot:** `cryptsetup status` + `blkid` run on the host (device-mapper is host-level) via `Command.execute(cmd, args)` with no target; the cmdline file reads/writes + `grub-mkconfig` are target-aware.
- **Legacy coexistence:** `desired_params` is now a property (lazy, resolves UUID); legacy `is_needed`/`execute` keep working through it. The reconciler uses the v3 `plan`/`apply` path.
