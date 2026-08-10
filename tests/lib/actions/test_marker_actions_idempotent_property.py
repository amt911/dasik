"""Property-based idempotency for the marker-based actions (CLAUDE.md § Quality).

BaseInstallAction and BootloaderAction converge on a marker file
(``.../pacman``, ``.../systemd-bootx64.efi`` / ``grub.cfg``): plan an INSTALL
when the marker is absent, and nothing when it is present. The invariant is the
NixOS one — once installed, a re-apply is a no-op — checked here over the
installed/absent state (and over both bootloader flavours).
"""
from types import SimpleNamespace

from hypothesis import given
from hypothesis import strategies as st

from dasik.lib.actions.base_install_action import BaseInstallAction
from dasik.lib.actions.bootloader_action import BootloaderAction
from dasik.lib.actions.ms_fonts_action import MicrosoftFontsAction
from dasik.lib.state.change import Op


@given(installed=st.booleans())
def test_base_install_idempotent(installed):
    a = BaseInstallAction({"enable_microcode": False}, context=SimpleNamespace(target=object()))
    a._installed = lambda: installed
    changes = a.plan(managed=[])
    if installed:
        assert changes == []            # already installed → no-op
        assert a.is_needed() is False
    else:
        assert [(c.op, c.item) for c in changes] == [(Op.INSTALL, "base")]
        assert a.is_needed() is True


@given(installed=st.booleans(), fallback=st.booleans(),
       loader=st.sampled_from(["sd-boot", "systemd-boot", "grub"]))
def test_bootloader_idempotent(installed, fallback, loader):
    """Converged means the loader marker AND — on systemd-boot — the rescue
    entry; once both are there a re-apply is a no-op."""
    a = BootloaderAction({"bootloader": loader}, context=SimpleNamespace(target=object()))
    a._installed = lambda: installed
    a._fallback_present = lambda: fallback
    sdboot = loader in ("sd-boot", "systemd-boot")

    expected = []
    if not installed:
        expected.append((Op.INSTALL, loader))
    if sdboot and not fallback:
        expected.append((Op.INSTALL, "fallback-entry"))

    changes = a.plan(managed=[])
    assert [(c.op, c.item) for c in changes] == expected
    assert a.is_needed() is bool(expected)
    if installed:
        assert a.verify() is True       # verify() only asserts the loader itself


@given(install=st.booleans(), has_iso=st.booleans(), present=st.booleans())
def test_ms_fonts_idempotent(install, has_iso, present):
    """Fonts install only when declared, given an ISO, and not already present;
    once present (or not declared) a re-apply is a no-op."""
    a = MicrosoftFontsAction(
        {"install": install, "source_iso": "/x.iso" if has_iso else ""},
        context=SimpleNamespace(target=object()),
    )
    a._fonts_present = lambda: present
    changes = a.plan(managed=[])
    if install and has_iso and not present:
        assert [(c.op, c.item) for c in changes] == [(Op.INSTALL, "windows-fonts")]
    else:
        assert changes == []            # not declared / no iso / already present → no-op
