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
