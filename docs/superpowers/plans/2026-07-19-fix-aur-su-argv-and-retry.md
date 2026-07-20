# AUR `su` argv and Partial-Retry Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the AUR helper path pass `-S` and the remaining helper arguments to `yay`/`paru` instead of util-linux `su`, and make a retry reuse a declared helper that was installed before an earlier apply failed.

**Architecture:** Preserve the existing safe positional-argument design (`$1`, `$2`, …) and insert util-linux's `--` option terminator between the `su -c` script and the shell argv. `PackagesAction` will select an eligible helper from the complete declarative package set, while `AurInstaller` will either build it when it is part of the current install delta or verify and reuse it when a previous partial apply already installed it. The command worker and resolver architecture remain unchanged.

**Tech Stack:** Python 3.10+, pytest/unittest.mock, pydantic-backed dasik config, util-linux `su`, pacman/makepkg/yay inside an Arch target, Bash/QEMU vmtest harness.

## Global Constraints

- Follow strict TDD for every new branch of action logic: red test, minimal green implementation, refactor under green tests.
- Never run `dasik apply`, `makepkg`, `pacman`, partitioning, formatting, `arch-chroot`, or AUR installation against the development host. Mock them in unit tests; run the real flow only in the disposable QEMU qcow2 described in Task 3.
- Do not add runtime or development dependencies.
- Do not interpolate package names, URLs, paths, helper names, or flags into a shell script. Values remain positional shell arguments after a fixed script token.
- Keep `AurInstaller.HELPERS == ("yay", "paru")`; this order defines precedence when both are eligible.
- A helper skipped under `package_policy.unknown == "warn-and-skip"` is not eligible.
- If a declared helper is selected for retry but is neither in the current package delta nor installed, abort with a clear `CommandExecutionError`; do not silently change installation strategy.
- Keep the non-helper AUR dependency resolver unchanged.
- Keep cleanup semantics: remove the temporary sudoers fragment always, remove `_aurbuilder` only when this run created it, and remove the build root best-effort.
- Use the existing `.venv`; do not install packages without explicit user approval.
- Do not push in normal mode and never merge.

---

## Confirmed Diagnosis

The supplied log is `dasik-apply-20260719-092347.log` (22,432 lines).

- Lines 22199-22201: `su` successfully runs `git clone` because the positional values after `sh` do not begin with `-`.
- Lines 22203-22373: `yay 13.0.1` builds and installs successfully.
- Line 22375 records:

  ```text
  /usr/bin/arch-chroot /mnt su - _aurbuilder -c exec "$@" sh yay -S --noconfirm --needed ...
  ```

- Lines 22376-22378: util-linux `su` consumes `-S` as one of its own options, prints `su: invalid option -- 'S'`, and exits 1 before `yay` starts.
- Lines 22409-22418 trace the value back to `AurInstaller._install_with_helper()` and `_run_as_builder()`.
- `PackagesAction._su_argv()` currently returns:

  ```python
  ["su", "-", user, "-c", script, "sh", *args]
  ```

- On util-linux 2.42.2, the current form reproduces `su: invalid option -- 'S'`. Inserting `--` before `sh` stops option parsing; the same inert test then reaches user lookup instead of rejecting `-S`.
- The current focused suites are false-green: 60 tests pass because `_Harness._su()` assumes every value after `sh` reaches the child shell and does not model util-linux option permutation.

The Snapper `fatal library error, lookup self` messages, three packages skipped for lack of a source, and earlier initramfs warnings do not cause this traceback. They are explicitly out of scope at the end of this plan.

## File Map

| Path | Responsibility in this change |
| --- | --- |
| `dasik/lib/actions/packages_action.py` | Correct the shared `su` argv; select and pass the eligible declarative helper during `apply()`; preserve retry context. |
| `dasik/lib/actions/aur_installer.py` | Accept an explicit helper choice, build it on a clean install, or verify/reuse it after a partial failure. |
| `dasik/lib/actions/pkgbuild_git_installer.py` | Apply the same `su --` contract to the duplicated PKGBUILD-Git argv helper. |
| `tests/lib/actions/test_packages_action_validation.py` | Pure regression for the shared `PackagesAction._su_argv()` output. |
| `tests/lib/actions/test_pkgbuild_git_installer.py` | Pure regression for the duplicated PKGBUILD-Git argv output. |
| `tests/lib/actions/test_aur_installer.py` | Model the real argv boundary and cover clean helper use, partial retry, absent helper, cleanup, and precedence. |
| `tests/lib/actions/test_packages_action_v3.py` | Cover helper selection from full desired state and exclusion of skipped helpers. |
| `config/vm-aur-helper-retry.json` | Safe QEMU-only full config used after a bootstrap apply that installs only `yay`. |
| `scripts/vmtest/guest-aur-helper-retry.sh` | Drive the real partial-retry scenario and assert exact log/package/cleanup/idempotency evidence. |
| `scripts/vmtest/guest-install-auto.sh` | Make its final marker fail if either the first or second apply fails. |
| `docs/vm-testing.md` | Document the exact AUR helper retry scenario and expected results. |

---

### Task 1: Terminate util-linux `su` Option Parsing

**Files:**
- Modify: `dasik/lib/actions/packages_action.py:195-205`
- Modify: `dasik/lib/actions/pkgbuild_git_installer.py:33-37`
- Modify: `tests/lib/actions/test_packages_action_validation.py`
- Modify: `tests/lib/actions/test_pkgbuild_git_installer.py`
- Modify: `tests/lib/actions/test_aur_installer.py`

**Interfaces:**
- Consumes: `_su_argv(user: str, script: str, *args: str) -> List[str]` in the two existing modules.
- Produces: the invariant `... -c <script> -- sh <payload...>`, where `--` is consumed by `su`, `sh` becomes shell `$0`, and payload values become `$1` onward.

- [ ] **Step 1: Add the pure failing regression for `PackagesAction._su_argv`**

Append this test to `tests/lib/actions/test_packages_action_validation.py`:

```python
def test_su_argv_terminates_options_before_dash_prefixed_payload():
    argv = PackagesAction._su_argv(
        "_aurbuilder",
        'exec "$@"',
        "yay",
        "-S",
        "--noconfirm",
        "--needed",
        "asunder",
    )
    assert argv == [
        "su",
        "-",
        "_aurbuilder",
        "-c",
        'exec "$@"',
        "--",
        "sh",
        "yay",
        "-S",
        "--noconfirm",
        "--needed",
        "asunder",
    ]
```

- [ ] **Step 2: Add the pure failing regression for the duplicated Git-PKGBUILD helper**

Change the import in `tests/lib/actions/test_pkgbuild_git_installer.py` to:

```python
from dasik.lib.actions.pkgbuild_git_installer import (
    PkgbuildGitInstaller,
    _su_argv,
)
```

Add:

```python
def test_su_argv_terminates_options_before_dash_prefixed_payload():
    assert _su_argv(
        "_aurbuilder", 'exec "$@"', "yay", "-S", "asunder"
    ) == [
        "su",
        "-",
        "_aurbuilder",
        "-c",
        'exec "$@"',
        "--",
        "sh",
        "yay",
        "-S",
        "asunder",
    ]
```

- [ ] **Step 3: Run the two new tests and verify RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider -v \
  tests/lib/actions/test_packages_action_validation.py::test_su_argv_terminates_options_before_dash_prefixed_payload \
  tests/lib/actions/test_pkgbuild_git_installer.py::test_su_argv_terminates_options_before_dash_prefixed_payload
```

Expected: both tests fail at the position where actual `"sh"` differs from expected `"--"`.

- [ ] **Step 4: Implement the minimal option terminator in `PackagesAction`**

Replace the complete method with:

```python
@staticmethod
def _su_argv(user: str, script: str, *args: str) -> List[str]:
    """Argv for ``su - <user> -c <script> -- sh`` with values as ``$1``...

    ``--`` terminates util-linux ``su`` option parsing before the shell's
    positional argv. Without it, helper flags such as ``-S`` are permuted and
    consumed by ``su`` instead of reaching ``exec \"$@\"``. Values remain inert
    positional data and are never interpolated into *script*.
    """
    return ["su", "-", user, "-c", script, "--", "sh", *args]
```

- [ ] **Step 5: Implement the same contract in `pkgbuild_git_installer.py`**

Replace its complete helper with:

```python
def _su_argv(user: str, script: str, *args: str) -> List[str]:
    """Argv for ``su - <user> -c <script> -- sh`` with positional data.

    The option terminator ensures every later dash-prefixed value belongs to
    the child shell rather than util-linux ``su``. Mirrors
    ``PackagesAction._su_argv``.
    """
    return ["su", "-", user, "-c", script, "--", "sh", *args]
```

- [ ] **Step 6: Replace fixed argv indices in the AUR test harness**

Add this helper near the top of `tests/lib/actions/test_aur_installer.py`:

```python
def _su_script_and_payload(args):
    """Return the fixed shell script and values that become $1 onward."""
    command_index = args.index("-c")
    script = args[command_index + 1]
    assert args[command_index + 2:command_index + 4] == ["--", "sh"]
    return script, args[command_index + 4:]
```

Replace `_Harness._su()` with:

```python
def _su(self, args):
    script, tail = _su_script_and_payload(args)
    if "--printsrcinfo" in script:
        pkg = self._pkg(tail[0]) if tail else ""
        deps = self.srcinfo.get(pkg, set())
        return 0, _srcinfo(pkg, deps).encode()
    if "makepkg -sri" in script:
        pkg = self._pkg(tail[0]) if tail else ""
        self.installed.add(pkg)
        self.satisfied.add(pkg)
        return 0, b""
    if script == 'exec "$@"':
        for name in tail[1:]:
            if not name.startswith("-"):
                self.installed.add(name)
        return 0, b""
    return 0, b""
```

Replace `_makepkgs()` and `_clones()` with:

```python
def _makepkgs(harness):
    out = []
    for cmd, args in harness.runs:
        if cmd != "su":
            continue
        script, payload = _su_script_and_payload(args)
        if "makepkg -sri" in script:
            out.append(_Harness._pkg(payload[0]))
    return out


def _clones(harness):
    out = []
    for cmd, args in harness.runs:
        if cmd != "su":
            continue
        script, payload = _su_script_and_payload(args)
        if "git clone" in script:
            out.append(_Harness._pkg(payload[1]))
    return out
```

Update the helper-path tests to obtain their payload through `_su_script_and_payload(args)[1]` instead of `args[5:]`. Keep their assertions on helper name, flags, package list, and absence of interpolation.

- [ ] **Step 7: Run focused tests and verify GREEN**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider -q \
  tests/lib/actions/test_aur_installer.py \
  tests/lib/actions/test_pkgbuild_git_installer.py \
  tests/lib/actions/test_packages_action_validation.py
```

Expected: all selected tests pass; the pre-change baseline was 60 tests.

- [ ] **Step 8: Commit Task 1**

```bash
git add \
  dasik/lib/actions/packages_action.py \
  dasik/lib/actions/pkgbuild_git_installer.py \
  tests/lib/actions/test_aur_installer.py \
  tests/lib/actions/test_pkgbuild_git_installer.py \
  tests/lib/actions/test_packages_action_validation.py
git commit -m "fix(aur): terminate su option parsing"
```

---

### Task 2: Preserve and Reuse the Declarative Helper on Partial Retry

**Files:**
- Modify: `dasik/lib/actions/packages_action.py:493-594`
- Modify: `dasik/lib/actions/aur_installer.py:74-137`
- Modify: `tests/lib/actions/test_packages_action_v3.py`
- Modify: `tests/lib/actions/test_aur_installer.py`

**Interfaces:**
- Consumes: Task 1's `su -c <script> -- sh <payload...>` invariant.
- Produces: `AurInstaller.install(pkgs: List[str], *, helper: str | None = None) -> None` and `PackagesAction._apply_aur_install(pkgs: list[str], *, helper: str | None = None) -> None`.
- Helper eligibility: declared in `self.desired`, not present in `self._skipped_unknown`, precedence from `AurInstaller.HELPERS`.

- [ ] **Step 1: Extend the package-resolution test fixture**

Replace `_resolution()` in `tests/lib/actions/test_packages_action_v3.py` with:

```python
def _resolution(repo=(), aur=(), groups=(), unknown=(), unavailable=()):
    return PackageResolution(
        repo=list(repo),
        aur=list(aur),
        groups=list(groups),
        unknown=list(unknown),
        unavailable=list(unavailable),
    )
```

- [ ] **Step 2: Add red tests for full-desired helper selection**

Add to `tests/lib/actions/test_packages_action_v3.py`:

```python
def test_apply_passes_declared_helper_when_only_rest_is_pending():
    action = PackagesAction(config=["yay", "asunder"], context=_ctx("/"))
    changes = [Change("packages", Op.INSTALL, "asunder")]
    with patch.object(
        action, "_resolve_sources", return_value=_resolution(aur=["asunder"])
    ), patch.object(action, "_apply_aur_install") as aur_install:
        action.apply(changes)
    aur_install.assert_called_once_with(["asunder"], helper="yay")


def test_apply_excludes_helper_skipped_as_unknown():
    action = PackagesAction(config=["yay", "asunder"], context=_ctx("/"))
    changes = [
        Change("packages", Op.INSTALL, "yay"),
        Change("packages", Op.INSTALL, "asunder"),
    ]
    with patch.object(
        action,
        "_resolve_sources",
        return_value=_resolution(aur=["asunder"], unknown=["yay"]),
    ), patch.object(action, "_apply_aur_install") as aur_install:
        action.apply(changes)
    aur_install.assert_called_once_with(["asunder"], helper=None)


def test_apply_uses_next_eligible_helper_when_first_is_skipped():
    action = PackagesAction(config=["yay", "paru", "asunder"], context=_ctx("/"))
    changes = [
        Change("packages", Op.INSTALL, "yay"),
        Change("packages", Op.INSTALL, "paru"),
        Change("packages", Op.INSTALL, "asunder"),
    ]
    with patch.object(
        action,
        "_resolve_sources",
        return_value=_resolution(aur=["paru", "asunder"], unknown=["yay"]),
    ), patch.object(action, "_apply_aur_install") as aur_install:
        action.apply(changes)
    aur_install.assert_called_once_with(["paru", "asunder"], helper="paru")
```

Also update existing assertions that currently expect `_apply_aur_install(["yay"])` so they expect `_apply_aur_install(["yay"], helper="yay")`.

- [ ] **Step 3: Add red tests for reuse and a stale/missing helper**

Change the test helper in `tests/lib/actions/test_aur_installer.py` to accept the future keyword without changing existing callers:

```python
def _install(
    pkgs,
    harness,
    resolver,
    exists=lambda p: "sudoers" in str(p),
    helper=None,
):
    inst = AurInstaller(Target(root="/"), resolver=resolver)
    with patch(
        "dasik.lib.actions.aur_installer.Command.execute",
        side_effect=harness.command_execute,
    ), patch(
        "dasik.lib.actions.aur_installer.os.path.exists", side_effect=exists
    ), patch("dasik.lib.actions.aur_installer.os.remove"), patch(
        "builtins.open", MagicMock()
    ):
        inst.install(pkgs, helper=helper)
    return inst
```

Add:

```python
def test_preinstalled_declared_helper_is_reused_without_rebuild():
    harness = _Harness(installed=["yay"])
    resolver = _StubResolver(repo=[], aur=["asunder"])
    _install(["asunder"], harness, resolver, helper="yay")

    assert _makepkgs(harness) == []
    helper_calls = []
    for cmd, args in harness.runs:
        if cmd != "su":
            continue
        script, payload = _su_script_and_payload(args)
        if script == 'exec "$@"':
            helper_calls.append(payload)
    assert helper_calls == [
        ["yay", "-S", "--noconfirm", "--needed", "asunder"]
    ]
    assert ("pacman", ["-Q", "yay"]) in harness.runs
    assert ("pacman", ["-Q", "asunder"]) in harness.runs


def test_selected_retry_helper_must_already_be_installed():
    harness = _Harness(installed=[])
    resolver = _StubResolver(repo=[], aur=["asunder"])
    with pytest.raises(
        CommandExecutionError,
        match="declared AUR helper 'yay' is not installed",
    ):
        _install(["asunder"], harness, resolver, helper="yay")

    assert not any(
        cmd == "su" and _su_script_and_payload(args)[0] == 'exec "$@"'
        for cmd, args in harness.runs
    )
```

- [ ] **Step 4: Run new tests and verify RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider -v \
  tests/lib/actions/test_packages_action_v3.py::test_apply_passes_declared_helper_when_only_rest_is_pending \
  tests/lib/actions/test_packages_action_v3.py::test_apply_excludes_helper_skipped_as_unknown \
  tests/lib/actions/test_packages_action_v3.py::test_apply_uses_next_eligible_helper_when_first_is_skipped \
  tests/lib/actions/test_aur_installer.py::test_preinstalled_declared_helper_is_reused_without_rebuild \
  tests/lib/actions/test_aur_installer.py::test_selected_retry_helper_must_already_be_installed
```

Expected: failures because the two production methods do not accept `helper=`, and `PackagesAction.apply()` does not preserve the helper outside the current delta.

- [ ] **Step 5: Select the eligible helper in `PackagesAction.apply()`**

Replace the current `if aur_installs:` block with:

```python
if aur_installs:
    from .aur_installer import AurInstaller

    helper = next(
        (
            name
            for name in AurInstaller.HELPERS
            if name in self.desired and name not in self._skipped_unknown
        ),
        None,
    )
    self._apply_aur_install(aur_installs, helper=helper)
```

Replace `_apply_aur_install()` with:

```python
def _apply_aur_install(
    self,
    pkgs: list[str],
    *,
    helper: "str | None" = None,
) -> None:
    """Install resolved AUR packages, preserving a declared helper on retry."""
    if self.context is None or self.context.target is None:
        raise CommandExecutionError(
            "AUR install requires an action context with a target."
        )
    from .aur_installer import AurInstaller

    AurInstaller(
        self.context.target,
        resolver=self._resolver,
    ).install(pkgs, helper=helper)
```

- [ ] **Step 6: Implement explicit helper selection and reuse in `AurInstaller`**

Replace `install()` with:

```python
def install(
    self,
    pkgs: List[str],
    *,
    helper: "str | None" = None,
) -> None:
    pkgs = list(dict.fromkeys(pkgs))
    if not pkgs:
        return
    for pkg in pkgs:
        _validate_pkg_name(pkg)

    if helper is not None and helper not in self.HELPERS:
        raise CommandExecutionError(f"Unsupported AUR helper {helper!r}")
    selected_helper = helper or next(
        (name for name in self.HELPERS if name in pkgs),
        None,
    )

    created = self._ensure_prerequisites()
    sudoers_path = self._target.path(f"/etc/sudoers.d/{self.BUILD_USER}")
    try:
        if selected_helper is not None:
            self._install_with_helper(selected_helper, pkgs)
        else:
            self._install_via_resolution(pkgs)
        self._verify_installed(pkgs)
    finally:
        self._cleanup(created, sudoers_path)
```

Replace `_install_with_helper()` with:

```python
def _install_with_helper(self, helper: str, pkgs: List[str]) -> None:
    if helper in pkgs:
        self._clone(helper)
        self._build_one(helper)
    else:
        installed = self._run("pacman", ["-Q", helper], check=False)
        if getattr(installed, "returncode", 0) != 0:
            raise CommandExecutionError(
                f"declared AUR helper {helper!r} is not installed and is not "
                "part of the current install delta; re-run plan/apply"
            )

    rest = [pkg for pkg in pkgs if pkg != helper]
    if not rest:
        return
    self._run_as_builder(
        'exec "$@"',
        helper,
        "-S",
        "--noconfirm",
        "--needed",
        *rest,
        check=True,
        stream=True,
    )
```

- [ ] **Step 7: Update the delegation test to lock the keyword interface**

Replace `test_apply_aur_install_delegates_to_aur_installer()` with:

```python
def test_apply_aur_install_delegates_helper_and_resolver():
    action = PackagesAction(config=["yay", "asunder"], context=_ctx("/"))
    with patch("dasik.lib.actions.aur_installer.AurInstaller") as Installer:
        action._apply_aur_install(["asunder"], helper="yay")

    Installer.assert_called_once()
    call = Installer.call_args
    assert call.args[0] is action.context.target
    assert call.kwargs["resolver"] is action._resolver
    Installer.return_value.install.assert_called_once_with(
        ["asunder"], helper="yay"
    )
```

- [ ] **Step 8: Run focused suites and verify GREEN**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider -q \
  tests/lib/actions/test_aur_installer.py \
  tests/lib/actions/test_packages_action_v3.py \
  tests/lib/actions/test_packages_action_validation.py \
  tests/lib/actions/test_pkgbuild_git_installer.py
```

Expected: all selected tests pass. In particular, partial retry has zero `makepkg` calls for `yay` and one exact helper payload containing `-S` after `-- sh yay`.

- [ ] **Step 9: Commit Task 2**

```bash
git add \
  dasik/lib/actions/packages_action.py \
  dasik/lib/actions/aur_installer.py \
  tests/lib/actions/test_packages_action_v3.py \
  tests/lib/actions/test_aur_installer.py
git commit -m "fix(aur): reuse declared helper on retry"
```

---

### Task 3: Add a Real QEMU Partial-Retry Regression

**Files:**
- Create: `config/vm-aur-helper-retry.json`
- Create: `scripts/vmtest/guest-aur-helper-retry.sh`
- Modify: `scripts/vmtest/guest-install-auto.sh:41-60`
- Modify: `docs/vm-testing.md`

**Interfaces:**
- Consumes: `scripts/vmtest/qemu.sh install` and generic `qemu.sh drive <image> <guest-script> <marker>`.
- Produces: marker `AUR-RETRY-DONE rc=0` only when bootstrap, retry, exact argv evidence, cleanup, and final no-op all pass.

- [ ] **Step 1: Create the exact QEMU-only full config**

Create `config/vm-aur-helper-retry.json` with:

```json
{
  "metadata": {
    "note": "QEMU-only AUR helper partial-retry regression. A guest script first derives a bootstrap config without aur-downgrade and installs aur-yay, then applies this full config so yay is already installed while downgrade is pending. The legacy aur- prefixes intentionally force the stable AUR path. NOT for real hardware."
  },
  "disks": {
    "disks": [
      {
        "device": "/dev/vda",
        "partition_table": "gpt",
        "wipe_disk": false,
        "partitions": [
          {"label": "ESP", "size": "512MiB", "filesystem": "fat32", "partition_type": "esp", "mountpoint": "/boot", "format": true},
          {"label": "ROOT", "size": "rest", "filesystem": "ext4", "partition_type": "linux", "mountpoint": "/", "format": true}
        ]
      }
    ]
  },
  "bootloader": "sd-boot",
  "enable_microcode": false,
  "hostname": "dasik-aur-retry",
  "kernel_cmdline": ["console=ttyS0,115200"],
  "timezone": {"region": "Etc", "city": "UTC"},
  "locales": {"selected_locales": ["en_US.UTF-8 UTF-8"], "desired_locale": "en_US.UTF-8", "desired_tty_layout": "us"},
  "network": {"type": "systemd-networkd"},
  "packages": [
    "base",
    "linux",
    "linux-firmware",
    "python",
    "python-pydantic",
    "python-colorama",
    "aur-yay",
    "aur-downgrade"
  ],
  "files": [
    {
      "path": "/etc/systemd/system/serial-getty@ttyS0.service.d/autologin.conf",
      "content": "[Service]\nExecStart=\nExecStart=-/sbin/agetty --autologin root --noclear --keep-baud 115200,38400,9600 %I $TERM\n"
    }
  ]
}
```

- [ ] **Step 2: Create the exact in-guest regression driver**

Create `scripts/vmtest/guest-aur-helper-retry.sh` with:

```bash
#!/bin/bash
# QEMU-only: run through qemu.sh drive against an image built from vm-day2.json.
set -u

cd /root || { echo "AUR-RETRY-DONE rc=91"; poweroff -f; }
export PYTHONPATH=/root/repo

FULL=/root/repo/config/vm-aur-helper-retry.json
BOOTSTRAP=/root/vm-aur-helper-bootstrap.json
BOOTSTRAP_LOG=/root/aur-bootstrap.log
RETRY_LOG=/root/aur-retry.log
rc=0

fail() {
    echo "BAD: $*"
    rc=1
}

python - "$FULL" "$BOOTSTRAP" <<'PY'
import json
import sys
from pathlib import Path

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
data = json.loads(source.read_text(encoding="utf-8"))
data["packages"] = [
    package for package in data["packages"] if package != "aur-downgrade"
]
assert "aur-yay" in data["packages"]
assert "aur-downgrade" not in data["packages"]
destination.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
[ "$?" -eq 0 ] || fail "could not create bootstrap config"

echo "AUR-RETRY: bootstrap installs only yay"
python -m dasik apply "$BOOTSTRAP" --target / --yes --log "$BOOTSTRAP_LOG"
[ "$?" -eq 0 ] || fail "bootstrap apply failed"
pacman -Q yay >/dev/null 2>&1 || fail "yay missing after bootstrap"
if pacman -Q downgrade >/dev/null 2>&1; then
    fail "downgrade unexpectedly installed before retry"
fi

echo "AUR-RETRY: full apply must reuse preinstalled yay"
python -m dasik apply "$FULL" --target / --yes --log "$RETRY_LOG"
[ "$?" -eq 0 ] || fail "retry apply failed"
pacman -Q yay downgrade >/dev/null 2>&1 || fail "yay/downgrade missing after retry"

grep -F 'su - _aurbuilder -c exec "$@" -- sh yay -S --noconfirm --needed downgrade' \
    "$RETRY_LOG" >/dev/null \
    || fail "retry log lacks the exact su option barrier/helper argv"
if grep -F 'https://aur.archlinux.org/yay.git' "$RETRY_LOG" >/dev/null; then
    fail "retry rebuilt yay instead of reusing it"
fi
if grep -F "su: invalid option" "$RETRY_LOG" >/dev/null; then
    fail "su still consumed a helper flag"
fi
if id _aurbuilder >/dev/null 2>&1; then
    fail "temporary AUR builder still exists"
fi
if [ -e /etc/sudoers.d/_aurbuilder ]; then
    fail "temporary AUR sudoers fragment still exists"
fi

echo "AUR-RETRY: third apply must be a no-op"
third_output="$(python -m dasik apply "$FULL" --target / --yes --no-log 2>&1)"
third_rc=$?
printf '%s\n' "$third_output"
[ "$third_rc" -eq 0 ] || fail "third apply exited $third_rc"
printf '%s\n' "$third_output" | grep -F "No changes" >/dev/null \
    || fail "third apply was not a no-op"

echo "AUR-RETRY-DONE rc=$rc"
[ -n "${DASIK_VM_NOPOWEROFF:-}" ] || poweroff -f
```

- [ ] **Step 3: Make the unattended install marker include second-apply failure**

In `scripts/vmtest/guest-install-auto.sh`, replace the apply/idempotency section from the first real apply through the final marker with:

```bash
echo "DASIK-VM: plan"
/root/venv/bin/dasik plan "$CONFIG" --target /mnt
echo "DASIK-VM: apply (destructive, guest /dev/vda only)"
/root/venv/bin/dasik apply "$CONFIG" --target /mnt --yes
first_rc=$?
echo "DASIK-VM: dasik apply exit=$first_rc"

echo "DASIK-VM: /mnt ->"; ls -A /mnt 2>&1 | tr '\n' ' '; echo
echo "DASIK-VM: /mnt/boot ->"; ls -A /mnt/boot 2>&1 | tr '\n' ' '; echo
echo "DASIK-VM: kernel present ->"; ls /mnt/boot/vmlinuz-* 2>/dev/null && echo yes || echo no
echo "DASIK-VM: pacman db ->"; ls -d /mnt/var/lib/pacman 2>/dev/null && echo yes || echo no

second_rc=0
if [ "$first_rc" -eq 0 ]; then
    echo "DASIK-VM: second apply (expect no-op)"
    /root/venv/bin/dasik apply "$CONFIG" --target /mnt --yes
    second_rc=$?
    echo "DASIK-VM: second apply exit=$second_rc"
else
    echo "DASIK-VM: second apply skipped because first apply failed"
fi

final_rc=$first_rc
if [ "$final_rc" -eq 0 ] && [ "$second_rc" -ne 0 ]; then
    final_rc=$second_rc
fi

echo "DASIK-VM-DONE rc=$final_rc"
sync
sleep 3
[ -n "${DASIK_VM_NOPOWEROFF:-}" ] || poweroff -f
```

- [ ] **Step 4: Validate config and shell syntax before launching a VM**

Run:

```bash
.venv/bin/dasik check config/vm-aur-helper-retry.json
bash -n scripts/vmtest/guest-install-auto.sh
bash -n scripts/vmtest/guest-aur-helper-retry.sh
```

Expected:

```text
config/vm-aur-helper-retry.json: OK — valid dasik config.
```

Both `bash -n` commands exit 0 with no output.

- [ ] **Step 5: Add the exact manual scenario to `docs/vm-testing.md`**

Document these commands under a new `Scenario — AUR helper partial retry` section:

```bash
DASIK_AUR_VM_DIR="$(mktemp -d /var/tmp/dasik-aur-retry.XXXXXX)"

DASIK_VM_WORKDIR="$DASIK_AUR_VM_DIR" \
DASIK_VM_RAM=4096 \
DASIK_VM_DISK=12G \
scripts/vmtest/qemu.sh install config/vm-day2.json

DASIK_VM_WORKDIR="$DASIK_AUR_VM_DIR" \
DASIK_VM_RAM=4096 \
DASIK_VM_DRIVE_TIMEOUT=1200 \
scripts/vmtest/qemu.sh drive \
  "$DASIK_AUR_VM_DIR/vda.qcow2" \
  guest-aur-helper-retry.sh \
  AUR-RETRY-DONE
```

State explicitly that `DASIK_VM_ISO` must already point to an existing Arch ISO. Expected final evidence:

```text
AUR-RETRY-DONE rc=0
```

The retry log must contain `-- sh yay -S`, must not contain a `yay.git` clone, and the third apply must render `No changes`.

- [ ] **Step 6: Run the real scenario only in the disposable qcow2**

Before running, verify `DASIK_VM_ISO` is set and points to a file:

```bash
test -n "${DASIK_VM_ISO:-}" && test -f "$DASIK_VM_ISO"
```

Then run the two commands from Step 5. Do not substitute a host block device for the qcow2-backed guest `/dev/vda`.

Expected: `qemu.sh install` exits 0; `qemu.sh drive` exits 0; serial/drive output ends in `AUR-RETRY-DONE rc=0`.

- [ ] **Step 7: Commit Task 3**

```bash
git add \
  config/vm-aur-helper-retry.json \
  scripts/vmtest/guest-aur-helper-retry.sh \
  scripts/vmtest/guest-install-auto.sh \
  docs/vm-testing.md
git commit -m "test(aur): cover helper retry in QEMU"
```

---

### Task 4: Full Gates, Review, and PR Handoff

**Files:**
- Verify: all files changed in Tasks 1-3
- No additional production files

**Interfaces:**
- Consumes: completed Tasks 1-3.
- Produces: fresh verification evidence and, if a PR is opened, the mandatory manual test section and agentic-verification PR comment.

- [ ] **Step 1: Run all unit tests with the coverage gate**

```bash
.venv/bin/pytest --cov=dasik
```

Expected: all tests pass and total branch/statement coverage is at least 80%.

- [ ] **Step 2: Run static gates**

```bash
.venv/bin/mypy dasik
.venv/bin/bandit -c pyproject.toml -r dasik
git diff --check
```

Expected: mypy reports no issues, Bandit reports no findings, and `git diff --check` exits 0 without output.

- [ ] **Step 3: Re-run the exact focused regression after the full suite**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider -q \
  tests/lib/actions/test_aur_installer.py \
  tests/lib/actions/test_packages_action_v3.py \
  tests/lib/actions/test_packages_action_validation.py \
  tests/lib/actions/test_pkgbuild_git_installer.py
```

Expected: all selected tests pass with no failures.

- [ ] **Step 4: Review the diff for scope and secrets**

```bash
git status --short
git diff --stat
git diff -- \
  dasik/lib/actions/packages_action.py \
  dasik/lib/actions/aur_installer.py \
  dasik/lib/actions/pkgbuild_git_installer.py \
  tests/lib/actions/test_packages_action_validation.py \
  tests/lib/actions/test_pkgbuild_git_installer.py \
  tests/lib/actions/test_aur_installer.py \
  tests/lib/actions/test_packages_action_v3.py \
  config/vm-aur-helper-retry.json \
  scripts/vmtest/guest-aur-helper-retry.sh \
  scripts/vmtest/guest-install-auto.sh \
  docs/vm-testing.md
```

Expected: only the planned files changed; no passwords, tokens, VM artifacts, run logs, or generated state appear in the diff.

- [ ] **Step 5: Prepare the PR body without pushing or merging**

The PR body must include this manual-test section, updated with the actual work directory and captured results:

```markdown
## How to test manually

1. Set `DASIK_VM_ISO` to an existing Arch ISO.
2. Run the QEMU install of `config/vm-day2.json` into a fresh qcow2.
3. Run `qemu.sh drive "$DASIK_AUR_VM_DIR/vda.qcow2" guest-aur-helper-retry.sh AUR-RETRY-DONE`.
4. Expect `AUR-RETRY-DONE rc=0`.
5. Confirm `/root/aur-retry.log` contains `-- sh yay -S`, does not clone `yay.git`, and contains no `su: invalid option`.
6. Confirm `pacman -Q yay downgrade` succeeds in the guest.
7. Confirm `_aurbuilder` and `/etc/sudoers.d/_aurbuilder` are absent afterward.
8. Confirm the final apply prints `No changes`.

Unit/error cases covered: a missing selected helper aborts clearly; a helper skipped as unknown is not selected; when both helpers are declared, the first eligible helper in `(yay, paru)` wins.
```

Do not push in normal mode. Never merge.

- [ ] **Step 6: Run and post mandatory agentic PR verification if a PR exists**

Run the repository's required build/CLI smoke in a controlled environment and capture its output. Resolve the current PR and create a verdict file without hard-coded identifiers:

```bash
DASIK_PR_NUMBER="$(gh pr view --json number --jq .number)"
DASIK_VERDICT_FILE="$(mktemp /var/tmp/dasik-pr-verdict.XXXXXX.md)"
```

Write the captured verdict to `$DASIK_VERDICT_FILE`, then post it with:

```bash
gh pr comment "$DASIK_PR_NUMBER" --body-file "$DASIK_VERDICT_FILE"
```

The comment must include:

- `pytest --cov=dasik` result and coverage percentage.
- `mypy dasik` and Bandit results.
- `dasik --help` and `python -m dasik --help` entry-point smoke results.
- QEMU commands and `AUR-RETRY-DONE rc=0` evidence.
- A statement that no real host disk or host `apply` was used.

The agent does not merge the PR.

---

## Acceptance Criteria

- The logged helper invocation is structurally `su - _aurbuilder -c 'exec "$@"' -- sh yay -S ...`.
- util-linux `su` never consumes `-S`, `--noconfirm`, or `--needed`.
- Package names and other values remain positional data; no shell interpolation is introduced.
- A clean install still builds a declared helper and delegates the remaining AUR packages to it.
- After an interrupted apply has installed `yay`, the next apply reuses it and does not clone or rebuild it.
- A selected-but-missing retry helper raises a clear `CommandExecutionError` before helper execution.
- A helper skipped by `warn-and-skip` is not selected; the next eligible helper wins, or the own-resolution path is used.
- Existing non-helper AUR resolution remains unchanged.
- Cleanup removes `_aurbuilder`, its build root, and its sudoers fragment after success or failure according to existing ownership rules.
- The final repeated apply is a no-op.
- Full pytest coverage remains at least 80%; mypy and Bandit are clean.
- The real verification touches only a disposable QEMU qcow2.

## Explicitly Out of Scope

- Investigating Snapper's repeated `fatal library error, lookup self` hook output. The log shows those transactions still exited 0, but snapshots may be broken and need a separate root-cause investigation.
- Adding sources for `config-saver`, `ttf-atkinson-hyperlegible-next-nerd-git`, or `ttf-atkinson-hyperlegible-next-nerd-mono-git`. The current `warn-and-skip` policy leaves them uninstalled.
- Revisiting the earlier initramfs warnings. The supplied log later records a successful initramfs generation.
- Refactoring the duplicated `su` argv helper into a new shared module. This fix keeps the change minimal and locks both existing copies with tests.
