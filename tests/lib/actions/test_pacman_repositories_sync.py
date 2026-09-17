"""`pacman.repositories` / `pacman.keys` sync capture (task 7).

Controller ruling behind this file's shape: `Reconciler.sync` merges every
action's `import_state()` fragment with `fragments.update(fragment)` — BY
TOP-LEVEL KEY (`reconciler.py`). `PacmanRepositoriesAction` and `PacmanAction`
both read `config_key='pacman'`, so two `{"pacman": ...}` fragments would
silently overwrite each other. The fix: `PacmanRepositoriesAction.import_state`
always returns `{}` (see its docstring), and `PacmanAction._import_fragment` is
the single fragment that wins — it asks a `PacmanRepositoriesAction` built from
its OWN config for `captured()`, which reads the machine with the exact same
private readers (`_conf_text`, `parse_sections`/`third_party`,
`_trusted_keys`) `plan()`/`actual()` use, so plan and sync can never disagree
about the same machine.

`Command.execute` is patched at the module level exactly as in
`test_pacman_repositories_plan.py` (never actually shells out to `gpg`);
`/etc/pacman.conf` and `/var/lib/pacman/sync/*.db` are real files under
`tmp_path`, reusing that file's fixtures/constants (mirrors
`test_ai_skills_sync.py` importing from `test_ai_skills_plan.py`).
"""
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.action_registry import get_default_registry
from dasik.lib.actions.actions_handler_v2 import setup_actions
from dasik.lib.actions.packages_action import PackagesAction
from dasik.lib.actions.pacman_action import PacmanAction
from dasik.lib.actions.pacman_repos_state import render
from dasik.lib.actions.pacman_repositories_action import PacmanRepositoriesAction
from dasik.lib.models.json_model import JsonModel
from dasik.lib.target.target import Target

from tests.lib.actions.test_pacman_repositories_plan import (
    AMT911_FPR,
    AMT_KEY_CFG,
    AMT_REPO_CFG,
    AMT_SECTION,
    AMT_SERVER,
    LIST_SECRET_KEYS,
    LIST_SIGS_LSIGNED,
    STOCK,
    _patched,
    _write_conf,
    _write_db,
)


def _ctx(tmp_path):
    return ActionContext(target=Target(root=str(tmp_path)))


def _pacman_fragment(tmp_path, seed_pacman, secret_out=LIST_SECRET_KEYS,
                     sigs_out=LIST_SIGS_LSIGNED):
    """`PacmanAction.import_state()`'s `pacman` fragment, gpg mocked exactly
    as `test_pacman_repositories_plan.py` mocks it for `plan()`. Defaults to
    the "amt911 is trusted" fixtures; pass empty strings for a keyring with no
    master key (nothing trusted), mirroring that file's row 1."""
    action = PacmanAction(seed_pacman, _ctx(tmp_path))
    with _patched(secret_out=secret_out, sigs_out=sigs_out):
        fragment = action.import_state()
    return fragment["pacman"]


# --- registration: right after PacmanAction, before PackagesAction --------- #


def test_pacman_repositories_registered_right_after_pacman_and_before_packages():
    setup_actions()
    classes = [meta["class"] for meta in get_default_registry().get_all_actions()]
    pacman_index = classes.index(PacmanAction)
    repos_index = classes.index(PacmanRepositoriesAction)
    packages_index = classes.index(PackagesAction)

    assert repos_index == pacman_index + 1
    assert repos_index < packages_index


# --- PacmanRepositoriesAction.import_state never contributes a fragment ---- #


def test_pacman_repositories_import_state_is_always_empty(tmp_path):
    """Whatever the machine or the config declare, this action's own
    `import_state` is `{}` — capture is `PacmanAction`'s job (see module
    docstring)."""
    _write_conf(tmp_path, render(STOCK, [AMT_SECTION], remove=[]))
    _write_db(tmp_path, "amt911")
    action = PacmanRepositoriesAction(
        {"repositories": [AMT_REPO_CFG], "keys": [AMT_KEY_CFG]}, _ctx(tmp_path))
    with _patched(secret_out=LIST_SECRET_KEYS, sigs_out=LIST_SIGS_LSIGNED):
        assert action.import_state(managed=None) == {}
        assert action.import_state() == {}


# --- converged machine: captured repositories/keys equal the seed ---------- #


def test_sync_captures_the_declared_repo_and_key_with_its_url(tmp_path):
    _write_conf(tmp_path, render(STOCK, [AMT_SECTION], remove=[]))
    _write_db(tmp_path, "amt911")
    seed = {"repositories": [AMT_REPO_CFG], "keys": [AMT_KEY_CFG]}

    fragment = _pacman_fragment(tmp_path, seed)

    assert fragment["repositories"] == [
        {"name": "amt911", "servers": [AMT_SERVER], "sig_level": "Required"}]
    assert fragment["keys"] == [
        {"fingerprint": AMT911_FPR, "url": AMT_KEY_CFG["url"]}]

    config = {"pacman": fragment}
    JsonModel.model_validate(config)

    # sync -> plan must be silent, on the same machine.
    replan = PacmanRepositoriesAction(fragment, _ctx(tmp_path))
    with _patched(secret_out=LIST_SECRET_KEYS, sigs_out=LIST_SIGS_LSIGNED):
        assert replan.plan(managed=[]) == []


# --- sync from {} still captures the machine's repo + a bare fingerprint --- #


def test_sync_from_empty_seed_captures_the_repo_and_a_bare_key(tmp_path):
    """No seed `pacman.keys` entry for this fingerprint -> `url` is omitted,
    never invented."""
    _write_conf(tmp_path, render(STOCK, [AMT_SECTION], remove=[]))
    _write_db(tmp_path, "amt911")

    fragment = _pacman_fragment(tmp_path, {})

    assert fragment["repositories"] == [
        {"name": "amt911", "servers": [AMT_SERVER], "sig_level": "Required"}]
    assert fragment["keys"] == [{"fingerprint": AMT911_FPR}]

    JsonModel.model_validate({"pacman": fragment})


# --- machine without them: a declared list is CLEARED, never invented ------ #


def test_sync_clears_a_declared_repo_and_key_the_machine_no_longer_has(tmp_path):
    _write_conf(tmp_path, STOCK)  # no [amt911] section, no trusted key
    seed = {"repositories": [AMT_REPO_CFG], "keys": [AMT_KEY_CFG]}

    # Empty secret-keys output -> no master key -> nothing trusted, matching
    # test_pacman_repositories_plan.py's "declared, machine has neither" row.
    fragment = _pacman_fragment(tmp_path, seed, secret_out="", sigs_out="")

    assert fragment["repositories"] == []
    assert fragment["keys"] == []
    JsonModel.model_validate({"pacman": fragment})


# --- fragment-collision regression --------------------------------------- #
#
# PacmanRepositoriesAction shares PacmanAction's `pacman` config_key and, for
# a while during this task's development, its `_import_fragment` risked being
# the fragment that overwrites the other. This pins that options/multilib
# (read straight off the machine by PacmanAction) survive ALONGSIDE
# repositories/keys (read off the machine by PacmanRepositoriesAction) in the
# one fragment that is actually returned.


def test_sync_keeps_pacman_options_from_the_machine_alongside_repositories(tmp_path):
    text = render(STOCK, [AMT_SECTION], remove=[])
    # The stock fixture ships `Color` commented out, and the seed declares it
    # False too — but the MACHINE has it active, which is what must win.
    assert "#Color\n" in text
    text = text.replace("#Color\n", "Color\n")
    _write_conf(tmp_path, text)
    _write_db(tmp_path, "amt911")
    seed = {"options": {"Color": False}, "repositories": [AMT_REPO_CFG],
           "keys": [AMT_KEY_CFG]}

    fragment = _pacman_fragment(tmp_path, seed)

    assert fragment["options"]["Color"] is True
    assert fragment["repositories"] == [
        {"name": "amt911", "servers": [AMT_SERVER], "sig_level": "Required"}]
    assert fragment["keys"] == [
        {"fingerprint": AMT911_FPR, "url": AMT_KEY_CFG["url"]}]

    JsonModel.model_validate({"pacman": fragment})
