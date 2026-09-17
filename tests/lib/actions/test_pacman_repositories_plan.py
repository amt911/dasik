"""`PacmanRepositoriesAction.actual()`/`.plan()`/`.managed_keys()` — the
detectability matrix from task-5-brief.md, amended by the controller ruling
in the task-5 dispatch: trust is a LOCAL signature from the keyring's master
key (`gpg --list-sigs`, class `10l`), never the plain validity column (which
also reaches `f` through the web of trust — FACT-PR-2, docs/FACTS.md).

`Command.execute` is patched at the module level (never actually shells out to
`gpg`); `/etc/pacman.conf` and `/var/lib/pacman/sync/*.db` are real files
under `tmp_path`, built with `pacman_repos_state.render` from the stock
fixture the same way `test_pacman_repos_conf.py` does, so the parsing path is
exercised for real and only the keyring listing is mocked.
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.pacman_repos_state import RepoSection, render
from dasik.lib.actions.pacman_repositories_action import PacmanRepositoriesAction
from dasik.lib.state.change import Change, Op
from dasik.lib.target.target import Target

FIXTURES = Path("tests/fixtures/pacman_repos")
STOCK = (FIXTURES / "pacman.conf.stock").read_text()
LIST_SECRET_KEYS = (FIXTURES / "list-secret-keys.txt").read_text()
LIST_SIGS_LSIGNED = (FIXTURES / "list-sigs-lsigned.txt").read_text()
ARCHLINUX_TRUSTED_HEAD = (FIXTURES / "archlinux-trusted.head").read_text()

AMT911_FPR = "6C6568CE34894645A23ABC44B5BD6F8F9023E53B"
MASTER_KEYID = "2535D4F912C7BF3C"
MASTER_FPR = "125C4E1E31F65FC81389D9FF2535D4F912C7BF3C"
AMT_SERVER = "https://amt911.github.io/arch-packages/$arch"

# The declared shape: what `section_of()` builds from the config dict below,
# and what `render()` needs to write a matching pacman.conf for the
# converged-machine test rows.
AMT_SECTION = RepoSection("amt911", "Required", (AMT_SERVER,), None)

AMT_REPO_CFG = {"name": "amt911", "sig_level": "Required", "servers": [AMT_SERVER]}
AMT_KEY_CFG = {"fingerprint": AMT911_FPR,
              "url": "https://amt911.github.io/arch-packages/amt911.gpg"}

CREATE_KEY = f"key:{AMT911_FPR}"
CREATE_REPO = "repo:amt911"


# --- fixtures on disk -------------------------------------------------------


def _write_conf(tmp_path: Path, text: str) -> None:
    etc = tmp_path / "etc"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "pacman.conf").write_text(text)


def _write_db(tmp_path: Path, name: str) -> None:
    sync_dir = tmp_path / "var/lib/pacman/sync"
    sync_dir.mkdir(parents=True, exist_ok=True)
    (sync_dir / f"{name}.db").write_text("")


def _write_keyring_trusted(tmp_path: Path, filename: str, text: str) -> None:
    keyrings = tmp_path / "usr/share/pacman/keyrings"
    keyrings.mkdir(parents=True, exist_ok=True)
    (keyrings / filename).write_text(text)


def _action(tmp_path: Path, config) -> PacmanRepositoriesAction:
    return PacmanRepositoriesAction(
        config, ActionContext(target=Target(root=str(tmp_path))))


# --- gpg mock ----------------------------------------------------------------


def _gpg_side_effect(secret_out: str = "", secret_rc: int = 0,
                     sigs_out: str = "", sigs_rc: int = 0):
    """A `Command.execute` stand-in that answers only the two gpg calls
    `_trusted_keys()` is allowed to make (controller ruling, task-5
    dispatch): `--list-secret-keys` and `--list-sigs`, both against the
    pacman keyring homedir. Anything else fails the test loudly instead of
    silently returning nonsense."""
    def run(cmd, args, **kwargs):
        assert cmd == "gpg"
        assert "--homedir" in args and "/etc/pacman.d/gnupg" in args
        if "--list-secret-keys" in args:
            return MagicMock(returncode=secret_rc, stdout=secret_out)
        if "--list-sigs" in args:
            return MagicMock(returncode=sigs_rc, stdout=sigs_out)
        raise AssertionError(f"unexpected gpg invocation: {args!r}")
    return run


def _patched(secret_out="", secret_rc=0, sigs_out="", sigs_rc=0):
    return patch("dasik.lib.actions.pacman_repositories_action.Command.execute",
                side_effect=_gpg_side_effect(secret_out, secret_rc, sigs_out, sigs_rc))


# --- 1. declared key + repo, machine has neither -----------------------------


def test_missing_key_and_repo_are_both_created(tmp_path):
    _write_conf(tmp_path, STOCK)
    action = _action(tmp_path, {"repositories": [AMT_REPO_CFG], "keys": [AMT_KEY_CFG]})
    with _patched():  # empty secret-keys output -> no master key -> nothing trusted
        changes = action.plan(managed=[])
    assert changes == [
        Change("pacman_repositories", Op.CREATE, CREATE_KEY),
        Change("pacman_repositories", Op.CREATE, CREATE_REPO),
    ]


# --- 2. machine has both, DB present -> silence ------------------------------


def test_converged_machine_plans_nothing(tmp_path):
    _write_conf(tmp_path, render(STOCK, [AMT_SECTION], remove=[]))
    _write_db(tmp_path, "amt911")
    action = _action(tmp_path, {"repositories": [AMT_REPO_CFG], "keys": [AMT_KEY_CFG]})
    with _patched(secret_out=LIST_SECRET_KEYS, sigs_out=LIST_SIGS_LSIGNED):
        assert action.plan(managed=[]) == []


# --- 3. section present, Server differs -> MODIFY (section drift) -----------


def test_server_drift_is_a_modify_with_section_drift_reason(tmp_path):
    drifted = RepoSection("amt911", "Required", ("https://old.example/$arch",), None)
    _write_conf(tmp_path, render(STOCK, [drifted], remove=[]))
    _write_db(tmp_path, "amt911")
    action = _action(tmp_path, {"repositories": [AMT_REPO_CFG], "keys": [AMT_KEY_CFG]})
    with _patched(secret_out=LIST_SECRET_KEYS, sigs_out=LIST_SIGS_LSIGNED):
        changes = action.plan(managed=[])
    assert changes == [
        Change("pacman_repositories", Op.MODIFY, CREATE_REPO, reason="section drift"),
    ]


# --- 4. section below [core] -> MODIFY (below [core]) ------------------------


def test_section_below_core_is_a_modify_with_below_core_reason(tmp_path):
    text = (STOCK + f"\n[amt911]\nSigLevel = Required\nServer = {AMT_SERVER}\n")
    _write_conf(tmp_path, text)
    _write_db(tmp_path, "amt911")
    action = _action(tmp_path, {"repositories": [AMT_REPO_CFG], "keys": [AMT_KEY_CFG]})
    with _patched(secret_out=LIST_SECRET_KEYS, sigs_out=LIST_SIGS_LSIGNED):
        changes = action.plan(managed=[])
    assert changes == [
        Change("pacman_repositories", Op.MODIFY, CREATE_REPO, reason="below [core]"),
    ]


# --- 5. section right, DB missing -> MODIFY (database not synced) -----------


def test_missing_sync_db_is_a_modify_with_database_not_synced_reason(tmp_path):
    _write_conf(tmp_path, render(STOCK, [AMT_SECTION], remove=[]))
    # no _write_db(tmp_path, "amt911") — the whole point of this row
    action = _action(tmp_path, {"repositories": [AMT_REPO_CFG], "keys": [AMT_KEY_CFG]})
    with _patched(secret_out=LIST_SECRET_KEYS, sigs_out=LIST_SIGS_LSIGNED):
        changes = action.plan(managed=[])
    assert changes == [
        Change("pacman_repositories", Op.MODIFY, CREATE_REPO,
              reason="database not synced"),
    ]


# --- 5b/5c. reason precedence when more than one condition is true ----------
#
# Rows 3-5 above each isolate a single failing condition. That alone lets the
# three `if`s be reordered (or the middle one dropped) without any test
# noticing, since in each row only one of them is ever true. These two pin
# the FIXED order the brief specifies — "section drift", then
# "below [core]", then "database not synced", first match wins — by making
# two of the three conditions true at once and asserting which one is
# reported.


def test_drift_reason_wins_over_below_core_and_missing_db(tmp_path):
    # Below [core] AND a different Server AND no synced db: "section drift"
    # must still be the one reported (checked first).
    drifted_below_core = (
        STOCK + "\n[amt911]\nSigLevel = Required\nServer = https://old.example/$arch\n")
    _write_conf(tmp_path, drifted_below_core)
    # no _write_db(tmp_path, "amt911")
    action = _action(tmp_path, {"repositories": [AMT_REPO_CFG], "keys": [AMT_KEY_CFG]})
    with _patched(secret_out=LIST_SECRET_KEYS, sigs_out=LIST_SIGS_LSIGNED):
        changes = action.plan(managed=[])
    assert changes == [
        Change("pacman_repositories", Op.MODIFY, CREATE_REPO, reason="section drift"),
    ]


def test_below_core_reason_wins_over_missing_db_when_content_matches(tmp_path):
    # Content matches exactly (no drift), below [core], AND no synced db:
    # "below [core]" must be the one reported (checked before the db check).
    text = STOCK + f"\n[amt911]\nSigLevel = Required\nServer = {AMT_SERVER}\n"
    _write_conf(tmp_path, text)
    # no _write_db(tmp_path, "amt911")
    action = _action(tmp_path, {"repositories": [AMT_REPO_CFG], "keys": [AMT_KEY_CFG]})
    with _patched(secret_out=LIST_SECRET_KEYS, sigs_out=LIST_SIGS_LSIGNED):
        changes = action.plan(managed=[])
    assert changes == [
        Change("pacman_repositories", Op.MODIFY, CREATE_REPO, reason="below [core]"),
    ]


# --- 6. key added but not lsigned -> CREATE key ------------------------------


def _drop_master_local_sig(colons: str) -> str:
    """`list-sigs-lsigned.txt` minus the one `sig` record that is the
    keyring master's LOCAL (class `10l`) signature — i.e. the shape of
    `gpg --list-sigs` right after `pacman-key --add` but before
    `--lsign-key` ever ran. Per the task-5 dispatch: there is no captured
    fixture for this moment (`list-keys-added.txt` is a `--list-keys`
    capture, wrong shape for `--list-sigs`), so it is derived here from the
    real lsigned capture instead of hand-writing one from scratch."""
    return "\n".join(line for line in colons.split("\n")
                     if not (line.startswith("sig:") and ":10l::" in line))


def test_key_added_but_not_lsigned_is_created(tmp_path):
    _write_conf(tmp_path, STOCK)
    action = _action(tmp_path, {"keys": [AMT_KEY_CFG]})
    sigs_without_lsign = _drop_master_local_sig(LIST_SIGS_LSIGNED)
    assert "10l" not in sigs_without_lsign  # sanity: the derivation worked
    with _patched(secret_out=LIST_SECRET_KEYS, sigs_out=sigs_without_lsign):
        changes = action.plan(managed=[])
    assert changes == [Change("pacman_repositories", Op.CREATE, CREATE_KEY)]


# --- 7. block emptied, manifest owns both -> both DELETEd --------------------


def test_emptied_block_deletes_the_owned_key_and_repo(tmp_path):
    _write_conf(tmp_path, render(STOCK, [AMT_SECTION], remove=[]))
    _write_db(tmp_path, "amt911")
    action = _action(tmp_path, {})
    with _patched(secret_out=LIST_SECRET_KEYS, sigs_out=LIST_SIGS_LSIGNED):
        changes = action.plan(managed=[CREATE_KEY, CREATE_REPO])
    assert changes == [
        Change("pacman_repositories", Op.DELETE, CREATE_KEY, reason="no longer declared"),
        Change("pacman_repositories", Op.DELETE, CREATE_REPO, reason="no longer declared"),
    ]


# --- 8. block emptied, nothing managed -> drift is left alone ---------------


def test_emptied_block_with_nothing_managed_touches_nothing(tmp_path):
    _write_conf(tmp_path, render(STOCK, [AMT_SECTION], remove=[]))
    _write_db(tmp_path, "amt911")
    action = _action(tmp_path, {})
    with _patched(secret_out=LIST_SECRET_KEYS, sigs_out=LIST_SIGS_LSIGNED):
        assert action.plan(managed=[]) == []


# --- 9. a packaged-trusted fingerprint never appears in actual() ------------


def test_packaged_trusted_fingerprint_is_excluded_from_actual(tmp_path):
    archlinux_fpr = "2AC0A42EFB0B5CBC7A0402ED4DC95B6D7BE9892E"
    assert archlinux_fpr in ARCHLINUX_TRUSTED_HEAD  # sanity
    _write_conf(tmp_path, STOCK)
    _write_keyring_trusted(tmp_path, "archlinux-trusted", ARCHLINUX_TRUSTED_HEAD)
    sigs_out = (
        "pub:-:4096:1:AAAAAAAAAAAAAAAA:1700000000:::-:::scESC::::::23::0:\n"
        f"fpr:::::::::{archlinux_fpr}:\n"
        "uid:-::::1700000000::deadbeef::Arch Linux <x@example.com>::::::::::0:\n"
        f"sig:::1:{MASTER_KEYID}:1700000000::::Pacman Keyring Master Key "
        f"<pacman@localhost>:10l::{MASTER_FPR}:::10:\n"
    )
    action = _action(tmp_path, {})
    with _patched(secret_out=LIST_SECRET_KEYS, sigs_out=sigs_out):
        actual = action.actual()
    assert f"key:{archlinux_fpr}" not in actual
    assert actual == set()


# --- managed_keys ------------------------------------------------------------


def test_managed_keys_reports_the_declared_items(tmp_path):
    _write_conf(tmp_path, STOCK)
    action = _action(tmp_path, {"repositories": [AMT_REPO_CFG], "keys": [AMT_KEY_CFG]})
    assert action.managed_keys() == {
        "pacman_repositories": sorted([CREATE_KEY, CREATE_REPO]),
    }


def test_managed_keys_is_empty_for_an_empty_block(tmp_path):
    action = _action(tmp_path, {})
    assert action.managed_keys() == {"pacman_repositories": []}


# --- no target -> inert -------------------------------------------------------


def test_no_target_plans_and_reports_nothing():
    action = PacmanRepositoriesAction({"repositories": [AMT_REPO_CFG],
                                       "keys": [AMT_KEY_CFG]}, None)
    assert action.actual() == set()
    assert action.plan(managed=[]) == []


# --- Reconciler probe (finding 1): `pacman` key absent from the WHOLE config -
#
# `Reconciler._any_managed_for` (reconciler.py) decides whether an optional
# domain whose config slice is missing still owns something to clean up by
# probing the class with `cls.__new__(cls)` — no `__init__`, so `self.config`/
# `self.context` are set by hand and every other instance attribute is
# whatever `__init__` would have set is simply absent. `managed_keys()` must
# survive that probe: `PacmanRepositoriesAction` carries class-level `_repos`/
# `_keys` defaults precisely so `_desired()` doesn't raise `AttributeError` on
# an uninitialized instance (see the class body). Without that tolerance the
# probe's broad `except Exception` swallows the error, `_any_managed_for`
# reports "owns nothing", and `build_plan` skips the action outright — the
# owned repo/key are never proposed for DELETE, which contradicts the spec's
# "block absent ⇒ DELETE of what the manifest owns" (design doc § plan()).


def test_pacman_block_absent_from_the_whole_config_deletes_what_the_manifest_owns(tmp_path):
    from dasik.lib.actions.action_registry import get_default_registry
    from dasik.lib.actions.actions_handler_v2 import setup_actions
    from dasik.lib.reconciler.reconciler import Reconciler

    _write_conf(tmp_path, render(STOCK, [AMT_SECTION], remove=[]))
    _write_db(tmp_path, "amt911")

    setup_actions()
    metas = [m for m in get_default_registry().get_all_actions()
             if m["class"] is PacmanRepositoriesAction]
    assert len(metas) == 1, "PacmanRepositoriesAction is not registered"
    assert metas[0]["config_key"] == "pacman"

    # No "pacman" key anywhere in this config — not even `{}` — the case
    # `build_plan` hands to `_any_managed_for` before ever constructing the
    # action with a real config.
    config = {"hostname": "box"}
    manifest = {"managed": {"pacman_repositories": sorted([CREATE_KEY, CREATE_REPO])}}

    reconciler = Reconciler(config=config, target=Target(root=str(tmp_path)),
                            manifest=manifest, action_metas=metas)
    with _patched(secret_out=LIST_SECRET_KEYS, sigs_out=LIST_SIGS_LSIGNED):
        plan, _results = reconciler.build_plan()

    changes = [(c.op.name, c.item) for c in plan.changes
              if c.domain == "pacman_repositories"]
    assert ("DELETE", CREATE_KEY) in changes
    assert ("DELETE", CREATE_REPO) in changes


# --- finding 7: narrowed `_run_gpg` except + one-time keyring-unreadable warn -
#
# `_run_gpg` used to catch bare `Exception`, which (a) hid genuine bugs in
# dasik's own code behind a silent "nothing trusted" result and (b) meant an
# unreadable/uninitialized pacman keyring (wrong permissions — it needs root,
# or `pacman-key --init` never ran) looked EXACTLY like "no keys declared
# yet", with no signal to the operator. Narrowed to the specific exceptions a
# real `Command.execute` can raise (`CommandNotFoundException`,
# `CommandExecutionError`, `OSError`); anything else propagates. A warning is
# printed once per action instance (not once per `_trusted_keys()` call, and
# `plan()`/`captured()` each call it) when the keyring can't be read AT ALL
# (non-zero rc or one of those exceptions) or has no master key, but only
# when there is a `pacman.conf` to converge in the first place — an unbuilt
# target with no pacman.conf yet is not a keyring problem.
import pytest as _pytest

from dasik.lib.logging import run_logger as _run_logger


@_pytest.fixture
def _fresh_run_logger():
    """`RunLogger` is a process-wide singleton that captures `sys.stderr` at
    construction time; resetting before AND after lets `capsys` see a
    freshly-built one bound to ITS redirected stream, and stops a
    capsys-bound instance leaking into a later test."""
    _run_logger.reset()
    yield
    _run_logger.reset()


def test_unreadable_keyring_warns_once_per_instance_not_per_call(tmp_path, capsys, _fresh_run_logger):
    from dasik.lib.exceptions.exceptions import CommandNotFoundException

    _write_conf(tmp_path, STOCK)
    action = _action(tmp_path, {"keys": [AMT_KEY_CFG]})

    def raise_not_found(cmd, args, **kwargs):
        raise CommandNotFoundException("gpg not found")

    with patch("dasik.lib.actions.pacman_repositories_action.Command.execute",
              side_effect=raise_not_found):
        action.plan(managed=[])   # calls _trusted_keys() once
        action.plan(managed=[])   # and again — still ONE warning total

    err = capsys.readouterr().err
    assert err.count("key trust") == 1
    assert "root" in err.lower()


def test_narrowed_except_does_not_swallow_an_unrelated_bug(tmp_path):
    _write_conf(tmp_path, STOCK)
    action = _action(tmp_path, {"keys": [AMT_KEY_CFG]})

    def boom(cmd, args, **kwargs):
        raise RuntimeError("not a Command.execute failure mode at all")

    with patch("dasik.lib.actions.pacman_repositories_action.Command.execute",
              side_effect=boom):
        with _pytest.raises(RuntimeError):
            action.plan(managed=[])


def test_no_warning_when_there_is_no_pacman_conf_to_converge(tmp_path, capsys, _fresh_run_logger):
    from dasik.lib.exceptions.exceptions import CommandNotFoundException

    # No _write_conf(tmp_path, ...) at all: an unbuilt target.
    action = _action(tmp_path, {"keys": [AMT_KEY_CFG]})

    def raise_not_found(cmd, args, **kwargs):
        raise CommandNotFoundException("gpg not found")

    with patch("dasik.lib.actions.pacman_repositories_action.Command.execute",
              side_effect=raise_not_found):
        action.plan(managed=[])   # still plans the declared key as CREATE

    assert capsys.readouterr().err == ""


def test_master_key_present_but_nothing_lsigned_yet_does_not_warn(tmp_path, capsys, _fresh_run_logger):
    """Master key exists, nothing is trusted yet — the ordinary "declared key
    not lsigned yet" state, not a broken keyring. Must stay silent."""
    _write_conf(tmp_path, STOCK)
    action = _action(tmp_path, {"keys": [AMT_KEY_CFG]})
    with _patched(secret_out=LIST_SECRET_KEYS, sigs_out=""):
        action.plan(managed=[])
    assert capsys.readouterr().err == ""


def test_no_master_key_at_all_with_pacman_conf_present_warns(tmp_path, capsys, _fresh_run_logger):
    _write_conf(tmp_path, STOCK)
    action = _action(tmp_path, {"keys": [AMT_KEY_CFG]})
    with _patched(secret_out="", sigs_out=""):
        action.plan(managed=[])
    err = capsys.readouterr().err
    assert "key trust" in err
    assert "root" in err.lower()
