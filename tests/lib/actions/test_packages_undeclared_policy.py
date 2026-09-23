"""`package_policy.undeclared`: what `plan` does with a package nobody declared.

Found on a real laptop: a package installed by hand, outside the config, and
`dasik plan` said nothing. That is the ownership model working as designed —
dasik only removes what its manifest owns — but it leaves no way to keep a
machine down to exactly what the config lists.

``keep`` (default) is that model, unchanged. ``remove`` plans a REMOVE for
every explicitly-installed package (`pacman -Qqe`) the expanded config does not
account for. "Accounts for" is the whole difficulty: a hand-written config
never lists `base`, `linux` or `grub`, and deleting those is not a policy, it is
an unbootable machine.
"""
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.packages_action import PackagesAction
from dasik.lib.models.package_model import PackagePolicyModel
from dasik.lib.target.target import Target


@pytest.fixture(autouse=True)
def _quiet_run_logger():
    from dasik.lib.logging import run_logger
    with patch.object(run_logger, "get", return_value=MagicMock()):
        yield


def _plan(explicit, config=None, *, managed=(), installed=None,
          required_by=None, policy="remove"):
    """Plan against a machine whose `pacman -Qqe` is *explicit*.

    *required_by* is the `Required By` answer of `pacman -Qi` per name.
    """
    cfg = {"packages": [], **(config or {})}
    if policy is not None:
        cfg.setdefault("package_policy", {})["undeclared"] = policy
    action = PackagesAction(cfg, ActionContext(target=Target(root="/")))
    installed = set(installed if installed is not None else explicit)
    installed |= set(explicit)
    action._installed_all = MagicMock(return_value=installed)
    action._explicit_raw = MagicMock(return_value=set(explicit))
    action.actual = MagicMock(return_value=set(explicit))
    action._group_members = MagicMock(return_value={})
    blocks = "\n".join(
        f"Name            : {n}\nRequired By     : {r}\n"
        for n, r in (required_by or {}).items())
    qi = MagicMock(stdout=blocks.encode(), returncode=0)
    with patch("dasik.lib.actions.packages_action.Command.execute",
               return_value=qi):
        return [(c.op.name, c.item) for c in action.plan(managed=list(managed))]


# --- the model --------------------------------------------------------- #

def test_the_default_is_keep():
    assert PackagePolicyModel().undeclared == "keep"


def test_remove_is_accepted():
    assert PackagePolicyModel(undeclared="remove").undeclared == "remove"


def test_anything_else_is_rejected():
    with pytest.raises(ValidationError):
        PackagePolicyModel(undeclared="purge")


# --- keep: today's behaviour ------------------------------------------- #

def test_keep_leaves_a_hand_installed_package_alone():
    assert _plan({"cowsay"}, policy="keep") == []


def test_no_policy_at_all_leaves_it_alone():
    assert _plan({"cowsay"}, policy=None) == []


# --- remove: the new behaviour ----------------------------------------- #

def test_remove_plans_a_hand_installed_package():
    assert _plan({"cowsay"}) == [("REMOVE", "cowsay")]


def test_a_declared_package_is_not_removed():
    assert _plan({"cowsay"}, {"packages": ["cowsay"]}) == []


def test_a_dict_declared_package_is_not_removed():
    assert _plan({"cowsay"},
                 {"packages": [{"name": "cowsay", "optional": True}]}) == []


def test_a_legacy_aur_prefixed_declaration_covers_the_bare_name():
    with pytest.warns(DeprecationWarning):
        assert _plan({"yay"}, {"packages": ["aur-yay"]}) == []


def test_a_member_of_a_declared_group_is_not_removed():
    cfg = {"packages": ["xorg"], "package_policy": {"undeclared": "remove"}}
    action = PackagesAction(cfg, ActionContext(target=Target(root="/")))
    action._installed_all = MagicMock(return_value={"xorg-server", "xorg-xinit"})
    action._explicit_raw = MagicMock(return_value={"xorg-server", "xorg-xinit"})
    action.actual = MagicMock(return_value={"xorg-server", "xorg-xinit", "xorg"})
    action._group_members = MagicMock(
        return_value={"xorg": {"xorg-server", "xorg-xinit"}})
    with patch("dasik.lib.actions.packages_action.Command.execute",
               return_value=MagicMock(stdout=b"", returncode=0)):
        assert action.plan(managed=[]) == []


def test_a_makepkg_debug_by_product_is_not_removed():
    """`yay-debug` goes with `yay`; it is never declared and resolves nowhere."""
    assert _plan({"yay", "yay-debug"}, {"packages": ["yay"]}) == []


def test_a_package_something_installed_requires_is_not_removed():
    """Explicit but required: pacman -Rns would fail the whole transaction."""
    assert _plan({"libfoo", "bar"}, {"packages": ["bar"]},
                 required_by={"libfoo": "bar"}) == []


def test_a_package_already_owned_is_planned_once():
    """Owned-and-undeclared is already a removal; the policy must not add it twice."""
    assert _plan({"cowsay"}, managed=["cowsay"]) == [("REMOVE", "cowsay")]


# --- what other actions install, which no hand-written config lists ---- #

@pytest.mark.parametrize("pkg", ["base", "linux", "linux-firmware", "mkinitcpio"])
def test_the_pacstrapped_base_is_never_removed(pkg):
    assert _plan({pkg}) == []


def test_the_declared_initramfs_generator_is_never_removed():
    assert _plan({"dracut"}, {"initramfs": "dracut"}) == []


def test_mkinitcpio_is_not_protected_on_a_dracut_machine():
    """The generator protected is the DECLARED one, not every generator."""
    assert _plan({"mkinitcpio"}, {"initramfs": "dracut"}) == [
        ("REMOVE", "mkinitcpio")]


@pytest.mark.parametrize("ucode", ["amd-ucode", "intel-ucode"])
def test_microcode_is_never_removed_when_enabled(ucode):
    assert _plan({ucode}, {"enable_microcode": True}) == []


def test_microcode_is_removable_when_not_enabled():
    assert _plan({"amd-ucode"}) == [("REMOVE", "amd-ucode")]


@pytest.mark.parametrize("pkg", ["grub", "efibootmgr"])
def test_grub_is_never_removed_on_a_grub_machine(pkg):
    """`bootloader` defaults to grub, so an absent key is grub too."""
    assert _plan({pkg}) == []
    assert _plan({pkg}, {"bootloader": "grub"}) == []


def test_grub_is_removable_on_a_systemd_boot_machine():
    assert _plan({"grub"}, {"bootloader": "sd-boot"}) == [("REMOVE", "grub")]


def test_7zip_is_never_removed_when_microsoft_fonts_are_installed():
    assert _plan({"7zip"}, {"microsoft_fonts": {"install": True}}) == []


def test_7zip_is_removable_without_microsoft_fonts():
    assert _plan({"7zip"}) == [("REMOVE", "7zip")]


@pytest.mark.parametrize("pkg", ["base-devel", "git"])
def test_the_aur_build_prerequisites_are_never_removed(pkg):
    """dasik installs them for any AUR or PKGBUILD build, and which declared
    package comes from the AUR is only known at apply time."""
    assert _plan({pkg}) == []


def test_only_the_foreign_package_goes_on_a_realistic_machine():
    explicit = {"base", "linux", "linux-firmware", "dracut", "amd-ucode",
                "base-devel", "git", "yay", "yay-debug", "firefox", "cowsay"}
    cfg = {"packages": ["firefox", "yay"], "initramfs": "dracut",
           "enable_microcode": True, "bootloader": "sd-boot"}
    assert _plan(explicit, cfg) == [("REMOVE", "cowsay")]


def test_the_reason_tells_the_two_removals_apart():
    """An owned package left the config; a foreign one was never in it."""
    cfg = {"packages": [], "package_policy": {"undeclared": "remove"}}
    action = PackagesAction(cfg, ActionContext(target=Target(root="/")))
    action._installed_all = MagicMock(return_value={"htop", "cowsay"})
    action._explicit_raw = MagicMock(return_value={"htop", "cowsay"})
    action.actual = MagicMock(return_value={"htop", "cowsay"})
    action._group_members = MagicMock(return_value={})
    with patch("dasik.lib.actions.packages_action.Command.execute",
               return_value=MagicMock(stdout=b"", returncode=0)):
        reasons = {c.item: c.reason for c in action.plan(managed=["htop"])}
    assert reasons == {
        "htop": "no longer declared",
        "cowsay": "not declared (package_policy.undeclared: remove)",
    }


# --- sync: the way to keep a hand-installed package ------------------------ #

def _machine(action, explicit):
    action._installed_all = MagicMock(return_value=set(explicit))
    action._explicit_raw = MagicMock(return_value=set(explicit))
    action.actual = MagicMock(return_value=set(explicit))
    action._group_members = MagicMock(return_value={})
    action._unit_provider_packages = MagicMock(return_value=set())
    action._captured_sources = MagicMock(return_value={})
    return action


def test_sync_then_plan_is_silent_under_remove():
    """`sync` captures the hand-installed package, and the captured config
    plans nothing: syncing is how you say "keep it"."""
    explicit = {"base", "linux", "linux-firmware", "mkinitcpio", "firefox",
                "cowsay"}
    seed = {"packages": ["firefox"], "package_policy": {"undeclared": "remove"}}
    ctx = ActionContext(target=Target(root="/"))
    captured = _machine(PackagesAction(seed, ctx), explicit).import_state()

    replayed = _machine(PackagesAction({**seed, **captured}, ctx), explicit)
    with patch("dasik.lib.actions.packages_action.Command.execute",
               return_value=MagicMock(stdout=b"", returncode=0)):
        assert "cowsay" in captured["packages"]
        assert replayed.plan(managed=[]) == []


# --- a declared name a PROVIDER satisfies ------------------------------- #

def _provider_plan(explicit, declared, qq_stdout, qq_raises=False):
    """Plan where `pacman -Qq <declared>` answers *qq_stdout* (it resolves a
    provide: `pacman -Qq sh` prints `bash`) and every other probe is empty."""
    cfg = {"packages": list(declared), "package_policy": {"undeclared": "remove"}}
    action = PackagesAction(cfg, ActionContext(target=Target(root="/")))
    _machine(action, explicit)

    def fake(cmd, args, **kw):
        if cmd == "pacman" and args[:1] == ["-Qq"]:
            if qq_raises:
                raise RuntimeError("no pacman")
            return MagicMock(stdout=qq_stdout.encode(), returncode=0)
        return MagicMock(stdout=b"", returncode=0)

    with patch("dasik.lib.actions.packages_action.Command.execute",
               side_effect=fake):
        action._satisfied = MagicMock(return_value=set(declared) - set(explicit))
        return [(c.op.name, c.item) for c in action.plan(managed=[])]


def test_the_provider_of_a_declared_name_is_not_removed():
    """`iptables-nft` satisfies a declared `iptables`: removing it would make
    the next plan install `iptables` back — a flip-flop, every apply."""
    assert _provider_plan({"iptables-nft", "cowsay"}, ["iptables"],
                          "iptables-nft\n") == [("REMOVE", "cowsay")]


def test_an_unreadable_provider_probe_plans_no_undeclared_removal():
    """Fail-safe: not knowing what satisfies a declared name means not knowing
    what is foreign, and the error to avoid is uninstalling."""
    assert _provider_plan({"iptables-nft", "cowsay"}, ["iptables"], "",
                          qq_raises=True) == []
