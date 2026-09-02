"""Some of the registry's order is load-bearing, and nothing was pinning it.

`ActionExecutor` walks `setup_actions()` in order, and four of those positions
are not style — they are fixes for failures that already happened once:

- **PacmanHooks before BaseInstall** (forensic F-10): the neutraliser hooks must
  exist before the FIRST pacman transaction, or mkinitcpio clobbers dracut's
  initramfs during pacstrap.
- **Snapper before Packages** (F-13): snap-pac snapshots each transaction, so
  the snapper config has to be there before packages start moving.
- **Disks first**: everything else writes into a filesystem that must already be
  mounted at /mnt.
- **Bootloader and kernel cmdline last**: they read what the initramfs step
  produced (image names, the generator in use) and write the entries from it.

A refactor that reorders the list would put those failures back silently — the
suite would stay green, and only a VM install would notice. This test is the
tripwire; it asserts relative positions, not absolute indices, so inserting new
actions is still free.
"""
from dasik.lib.actions.action_registry import get_default_registry
from dasik.lib.actions.actions_handler_v2 import setup_actions


def _positions():
    setup_actions()
    return {entry["class"].__name__: i
            for i, entry in enumerate(get_default_registry()._actions)}


def test_the_mkinitcpio_neutraliser_lands_before_the_first_transaction():
    """F-10: pacstrap runs mkinitcpio's hooks; the neutralisers must pre-date it."""
    at = _positions()

    assert at["PacmanHooksAction"] < at["BaseInstallAction"] < at["PackagesAction"]


def test_snapper_is_configured_before_packages_move():
    """F-13: snap-pac snapshots every pacman transaction it can see."""
    at = _positions()

    assert at["SnapperAction"] < at["PackagesAction"]


def test_the_disks_come_first():
    at = _positions()

    assert at["DiskPartitionAction"] == 0


def test_the_boot_chain_comes_last():
    """The bootloader and the cmdline read what the initramfs step produced."""
    at = _positions()

    assert at["InitramfsAction"] < at["BootloaderAction"]
    assert at["InitramfsAction"] < at["KernelCmdlineAction"]
    assert at["BootloaderAction"] == max(at.values()) - 1
    assert at["KernelCmdlineAction"] == max(at.values())


def test_the_keyfile_is_enrolled_while_the_disk_step_is_still_fresh():
    """It needs the LUKS volume the disk action just created, and the key device
    mounted before anything else touches /mnt."""
    at = _positions()

    assert at["DiskPartitionAction"] < at["LuksKeyfileAction"] < at["BaseInstallAction"]


def test_users_exist_before_anything_writes_into_their_home():
    at = _positions()

    assert at["UsersAction"] < at["SudoAction"]
    assert at["PackagesAction"] < at["UsersAction"]     # shells come from packages


def test_registering_a_new_action_does_not_break_the_pins():
    """The pins are relative, so a new action anywhere in the middle is free."""
    at = _positions()

    assert len(at) == len(set(at)), "two actions registered under one name"
    assert at["PacmanHooksAction"] < at["BaseInstallAction"]


def test_ai_skills_runs_after_the_user_and_the_binaries_exist():
    """The installers run AS the user, inside the target, from $HOME.

    Users has to have created the account (and its home), and Packages has to
    have installed `claude`/`codex`/`nodejs` — every command this domain runs is
    one of those binaries.
    """
    at = _positions()

    assert at["UsersAction"] < at["AiSkillsAction"]
    assert at["PackagesAction"] < at["AiSkillsAction"]
    assert at["AiSkillsAction"] < at["BootloaderAction"]


def test_uv_tools_run_before_the_skills_that_need_them():
    """graphify ships its skill FROM the program: `graphify install --platform`.

    So the uv tool has to be installed before AiSkillsAction runs, or the skill
    fails on a fresh install and only converges on the next apply.
    """
    at = _positions()

    assert at["UsersAction"] < at["UvToolsAction"]
    assert at["PackagesAction"] < at["UvToolsAction"]     # uv itself is a package
    assert at["UvToolsAction"] < at["AiSkillsAction"]
