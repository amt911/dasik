"""`PacmanRepositoriesAction.apply()` — every command it drives, asserted as
argv (never actually run: `Command.execute` is patched at the module level),
and the fixed order from task-6-brief.md that must hold regardless of the
order `changes` arrives in: key CREATEs, then the single pacman.conf
read+write, then one `pacman -Sy --config <single-repo conf>` per
CREATE/MODIFY repo, then key DELETEs.

`pacman.conf` and the `/var/tmp` temp files are real files under `tmp_path`
(`Target(root=str(tmp_path))`), written and read by the action for real — only
the external commands (`curl`, `gpg`, `pacman-key`, `pacman`) are mocked, the
same idiom as `test_mcp_servers_apply.py` and `test_pacman_repositories_plan.py`.
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.pacman_repos_state import RepoSection, options_block, render
from dasik.lib.actions.pacman_repositories_action import PacmanRepositoriesAction
from dasik.lib.exceptions.exceptions import PacmanKeyMismatchError
from dasik.lib.state.change import Change, Op
from dasik.lib.target.target import Target

FIXTURES = Path("tests/fixtures/pacman_repos")
STOCK = (FIXTURES / "pacman.conf.stock").read_text()
SHOW_KEYS_AMT911 = (FIXTURES / "show-keys-amt911.txt").read_text()

AMT911_FPR = "6C6568CE34894645A23ABC44B5BD6F8F9023E53B"
AMT_SERVER = "https://amt911.github.io/arch-packages/$arch"
AMT_SECTION = RepoSection("amt911", "Required", (AMT_SERVER,), None)
AMT_REPO_CFG = {"name": "amt911", "sig_level": "Required", "servers": [AMT_SERVER]}
AMT_KEY_URL = "https://amt911.github.io/arch-packages/amt911.gpg"
AMT_KEY_CFG = {"fingerprint": AMT911_FPR, "url": AMT_KEY_URL}

CREATE_KEY = f"key:{AMT911_FPR}"
CREATE_REPO = "repo:amt911"

# A colon text with one `pub`/`fpr` pair whose fingerprint is NOT the
# declared one — the "wrong key entirely" mismatch case.
WRONG_KEY_COLONS = (
    "pub:-:4096:1:CCCCCCCCCCCCCCCC:1789487636:::-:::scESC::::::23::0:\n"
    "fpr:::::::::1111111111111111111111111111111111111111:\n"
    "uid:-::::1789487636::yyyy::another key <b@example.com>::::::::::0:\n"
)

# A colon text with TWO `pub`/`fpr` pairs, one of which IS the declared
# fingerprint — still a mismatch, because the file carries more than the one
# key that was declared (built by hand per the task-6 dispatch: no captured
# fixture has this shape).
TWO_KEYS_COLONS = (
    "pub:-:4096:1:B5BD6F8F9023E53B:1789487636:::-:::scESC::::::23::0:\n"
    f"fpr:::::::::{AMT911_FPR}:\n"
    "uid:-::::1789487636::xxxx::amt911 Arch Repository <a@example.com>::::::::::0:\n"
    "pub:-:4096:1:CCCCCCCCCCCCCCCC:1789487636:::-:::scESC::::::23::0:\n"
    "fpr:::::::::1111111111111111111111111111111111111111:\n"
    "uid:-::::1789487636::yyyy::another key <b@example.com>::::::::::0:\n"
)


# --- fixtures on disk --------------------------------------------------------


def _write_conf(tmp_path: Path, text: str) -> None:
    etc = tmp_path / "etc"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "pacman.conf").write_text(text)


def _target(tmp_path: Path) -> Target:
    (tmp_path / "var/tmp").mkdir(parents=True, exist_ok=True)
    return Target(root=str(tmp_path))


def _action(tmp_path: Path, config) -> PacmanRepositoriesAction:
    return PacmanRepositoriesAction(config, ActionContext(target=_target(tmp_path)))


def _download_side_effect(gpg_stdout: str):
    """A `Command.execute` stand-in for a CREATE key with a `url`: `curl`
    really writes the (fake) downloaded file at the host path so the
    finally-cleanup has something real to remove, and `gpg --show-keys`
    answers with *gpg_stdout*. Any other command succeeds with empty output."""
    def run(cmd, args, **kwargs):
        if cmd == "curl":
            host_path = kwargs["target"].path(args[args.index("-o") + 1])
            Path(host_path).write_bytes(b"mock-key-data")
            return MagicMock(returncode=0, stdout=b"", stderr=b"")
        if cmd == "gpg":
            return MagicMock(returncode=0, stdout=gpg_stdout, stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")
    return run


# --- 1. CREATE key with url: curl -> gpg --show-keys -> add -> lsign --------


def test_create_key_with_url_downloads_verifies_adds_and_lsigns(tmp_path):
    target = _target(tmp_path)
    action = PacmanRepositoriesAction({"keys": [AMT_KEY_CFG]}, ActionContext(target=target))

    with patch("dasik.lib.actions.pacman_repositories_action.Command.execute",
              side_effect=_download_side_effect(SHOW_KEYS_AMT911)) as execute:
        action.apply([Change("pacman_repositories", Op.CREATE, CREATE_KEY)])

    calls = execute.call_args_list
    assert [c.args[0] for c in calls] == ["curl", "gpg", "pacman-key", "pacman-key"]
    for call in calls:
        assert call.kwargs["target"] == target
        assert call.kwargs["check"] is True

    curl_args = calls[0].args[1]
    assert curl_args[0] == "-fsSL"
    assert curl_args[1] == AMT_KEY_URL
    key_path = curl_args[curl_args.index("-o") + 1]
    assert key_path.startswith("/var/tmp/dasik-key-")
    # a key server/CDN that hangs must not hang `dasik apply` forever
    assert curl_args[curl_args.index("--max-time") + 1] == "120"

    assert calls[1].args[1] == ["--show-keys", "--with-colons", key_path]
    assert calls[2].args[1] == ["--add", key_path]
    assert calls[3].args[1] == ["--lsign-key", AMT911_FPR]

    # the temp file curl "downloaded" is gone once apply() returns
    assert not Path(target.path(key_path)).exists()


# --- 2. mismatch: wrong key entirely ----------------------------------------


def test_key_mismatch_raises_before_touching_the_keyring(tmp_path):
    target = _target(tmp_path)
    action = PacmanRepositoriesAction({"keys": [AMT_KEY_CFG]}, ActionContext(target=target))

    downloaded = {}

    def side_effect(cmd, args, **kwargs):
        if cmd == "curl":
            host_path = kwargs["target"].path(args[args.index("-o") + 1])
            downloaded["path"] = host_path
            Path(host_path).write_bytes(b"mock-key-data")
            return MagicMock(returncode=0, stdout=b"", stderr=b"")
        if cmd == "gpg":
            return MagicMock(returncode=0, stdout=WRONG_KEY_COLONS, stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("dasik.lib.actions.pacman_repositories_action.Command.execute",
              side_effect=side_effect) as execute:
        with pytest.raises(PacmanKeyMismatchError):
            action.apply([Change("pacman_repositories", Op.CREATE, CREATE_KEY)])

    assert "pacman-key" not in [c.args[0] for c in execute.call_args_list]
    assert not Path(downloaded["path"]).exists()


# --- 3. mismatch: file carries two primary keys, one of them declared ------


def test_key_file_with_two_primary_keys_still_mismatches(tmp_path):
    target = _target(tmp_path)
    action = PacmanRepositoriesAction({"keys": [AMT_KEY_CFG]}, ActionContext(target=target))

    with patch("dasik.lib.actions.pacman_repositories_action.Command.execute",
              side_effect=_download_side_effect(TWO_KEYS_COLONS)) as execute:
        with pytest.raises(PacmanKeyMismatchError):
            action.apply([Change("pacman_repositories", Op.CREATE, CREATE_KEY)])

    assert "pacman-key" not in [c.args[0] for c in execute.call_args_list]


# --- 4. CREATE key without url -----------------------------------------------


def test_create_key_without_url_uses_recv_keys(tmp_path):
    target = _target(tmp_path)
    action = PacmanRepositoriesAction({"keys": [{"fingerprint": AMT911_FPR}]},
                                      ActionContext(target=target))

    with patch("dasik.lib.actions.pacman_repositories_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=0, stdout="", stderr="")
        action.apply([Change("pacman_repositories", Op.CREATE, CREATE_KEY)])

    calls = execute.call_args_list
    assert [c.args[0] for c in calls] == ["pacman-key", "pacman-key"]
    assert calls[0].args[1] == ["--recv-keys", AMT911_FPR]
    assert calls[1].args[1] == ["--lsign-key", AMT911_FPR]
    assert "curl" not in [c.args[0] for c in calls]


# --- 5. CREATE repo: conf rewrite + single-repo sync ------------------------


def test_create_repo_writes_conf_and_syncs_only_that_repo(tmp_path):
    _write_conf(tmp_path, STOCK)
    target = _target(tmp_path)
    action = PacmanRepositoriesAction({"repositories": [AMT_REPO_CFG]},
                                      ActionContext(target=target))

    captured = {}

    def side_effect(cmd, args, **kwargs):
        if cmd == "pacman":
            conf_arg = args[args.index("--config") + 1]
            host_conf = kwargs["target"].path(conf_arg)
            captured["host_path"] = host_conf
            captured["text"] = Path(host_conf).read_text()
            captured["argv"] = args
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("dasik.lib.actions.pacman_repositories_action.Command.execute",
              side_effect=side_effect) as execute:
        action.apply([Change("pacman_repositories", Op.CREATE, CREATE_REPO)])

    conf_text = (tmp_path / "etc/pacman.conf").read_text()
    assert conf_text.index("[amt911]") < conf_text.index("\n[core]")

    pacman_calls = [c for c in execute.call_args_list if c.args[0] == "pacman"]
    assert len(pacman_calls) == 1
    assert pacman_calls[0].args[1] == ["-Sy", "--config", "/var/tmp/dasik-pacman-amt911.conf"]
    assert pacman_calls[0].kwargs["check"] is True
    assert pacman_calls[0].kwargs["stream"] is True

    assert "[options]" in captured["text"]
    assert "[amt911]" in captured["text"]
    assert "[core]" not in captured["text"]

    # the single-repo temp conf is gone once apply() returns
    assert not Path(captured["host_path"]).exists()


# --- 6. ordering: keys -> conf write -> sync --------------------------------


def test_keys_are_applied_before_the_conf_write_which_precedes_sync(tmp_path):
    _write_conf(tmp_path, STOCK)
    target = _target(tmp_path)
    action = PacmanRepositoriesAction(
        {"repositories": [AMT_REPO_CFG], "keys": [AMT_KEY_CFG]},
        ActionContext(target=target))

    conf_path = tmp_path / "etc/pacman.conf"
    order = []

    def side_effect(cmd, args, **kwargs):
        snapshot = conf_path.read_text()
        order.append((cmd, snapshot))
        if cmd == "curl":
            host_path = kwargs["target"].path(args[args.index("-o") + 1])
            Path(host_path).write_bytes(b"mock-key-data")
        if cmd == "gpg":
            return MagicMock(returncode=0, stdout=SHOW_KEYS_AMT911)
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("dasik.lib.actions.pacman_repositories_action.Command.execute",
              side_effect=side_effect):
        # deliberately handed in reverse of the fixed apply order
        action.apply([
            Change("pacman_repositories", Op.CREATE, CREATE_REPO),
            Change("pacman_repositories", Op.CREATE, CREATE_KEY),
        ])

    cmds = [cmd for cmd, _ in order]
    sy_index = cmds.index("pacman")
    assert cmds[:sy_index] == ["curl", "gpg", "pacman-key", "pacman-key"]

    # while the key phase runs, pacman.conf does not carry the new section yet
    for _, snapshot in order[:sy_index]:
        assert "[amt911]" not in snapshot
    # by the time -Sy runs, the conf write has already happened
    assert "[amt911]" in order[sy_index][1]


# --- 7. DELETE repo: byte-for-byte restore, no sync -------------------------


def test_delete_repo_restores_stock_exactly_and_never_syncs(tmp_path):
    _write_conf(tmp_path, render(STOCK, [AMT_SECTION], remove=[]))
    action = _action(tmp_path, {})

    with patch("dasik.lib.actions.pacman_repositories_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=0, stdout="", stderr="")
        action.apply([Change("pacman_repositories", Op.DELETE, CREATE_REPO,
                             reason="no longer declared")])

    assert (tmp_path / "etc/pacman.conf").read_text() == STOCK
    assert "pacman" not in [c.args[0] for c in execute.call_args_list]


# --- 8. DELETE key -----------------------------------------------------------


def test_delete_key_calls_pacman_key_delete(tmp_path):
    target = _target(tmp_path)
    action = PacmanRepositoriesAction({}, ActionContext(target=target))

    with patch("dasik.lib.actions.pacman_repositories_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=0, stdout="", stderr="")
        action.apply([Change("pacman_repositories", Op.DELETE, CREATE_KEY,
                             reason="no longer declared")])

    execute.assert_called_once_with(
        "pacman-key", ["--delete", AMT911_FPR], target=target, check=True)


# --- 9. apply([]) is a true no-op -------------------------------------------


def test_apply_with_no_changes_runs_nothing_and_touches_nothing(tmp_path):
    _write_conf(tmp_path, STOCK)
    action = _action(tmp_path, {"repositories": [AMT_REPO_CFG], "keys": [AMT_KEY_CFG]})
    conf_path = tmp_path / "etc/pacman.conf"
    mtime_before = conf_path.stat().st_mtime_ns

    with patch("dasik.lib.actions.pacman_repositories_action.Command.execute") as execute:
        action.apply([])

    execute.assert_not_called()
    assert conf_path.stat().st_mtime_ns == mtime_before


# --- no target -> inert ------------------------------------------------------


def test_no_target_applies_nothing():
    action = PacmanRepositoriesAction({"keys": [AMT_KEY_CFG]}, None)
    with patch("dasik.lib.actions.pacman_repositories_action.Command.execute") as execute:
        action.apply([Change("pacman_repositories", Op.CREATE, CREATE_KEY)])
    execute.assert_not_called()


# --- options_block (pure helper added to pacman_repos_state for step 3) ----


def test_options_block_extracts_the_options_section_from_stock():
    block = options_block(STOCK)
    assert block.startswith("[options]")
    assert "[core]" not in block
    assert "Architecture = auto" in block


def test_options_block_stops_at_a_section_re_homed_ahead_of_core():
    conf_with_repo = render(STOCK, [AMT_SECTION], remove=[])
    block = options_block(conf_with_repo)
    assert "[options]" in block
    assert "[amt911]" not in block
    assert "[core]" not in block


def test_options_block_is_empty_without_an_options_header():
    assert options_block("[core]\nInclude = /etc/pacman.d/mirrorlist\n") == ""


def test_options_block_runs_to_end_of_file_when_options_is_last():
    text = "[options]\nArchitecture = auto\n"
    assert options_block(text) == text
