"""An optional (``nofail``) encrypted DATA volume is unlocked by the real system.

Measured on torre-amd and in the vm-preserved-luks guest: the initramfs does not
wait for a ``nofail`` volume, so ``rd.luks.name`` only started an unlock that
``switch-root`` then killed (or that failed there), and the real system started
it again from /etc/crypttab — whose line carried neither ``fido2-device=auto``
nor the declared ``luks_options``. With the desktop already up, that second
attempt asked for a touch or a PIN nobody could see.

So such a volume is left OFF the kernel command line and its crypttab line
carries the whole unlock: the token, and every ``luks_options`` entry (e.g.
``headless=true`` so it never prompts). Root, swap, a volume without
``nofail`` and a keyfile unlock are deliberately unchanged.
"""
from unittest.mock import patch

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.disk_partition_action import DiskPartitionAction
from dasik.lib.actions.initramfs.dracut import DracutBackend
from dasik.lib.actions.kernel_cmdline_action import KernelCmdlineAction
from dasik.lib.actions.luks_uuid import luks_uuid
from dasik.lib.actions.partition_utils import opens_after_switch_root
from dasik.lib.target.target import Target

ROOT_UUID = luks_uuid("cryptroot")
SWAP_UUID = luks_uuid("cryptswap")
GAMES_UUID = "5eed5eed-0000-4000-8000-00000000c0de"
OPTS = ["token-timeout=10s", "nofail", "headless=true"]


def _root():
    return {"label": "root", "filesystem": "btrfs", "mountpoint": None,
            "encrypt": True, "luks_name": "cryptroot", "unlock_fido2": 2,
            "luks_options": ["token-timeout=10s"],
            "btrfs_subvolumes": [{"name": "@", "mountpoint": "/"}]}


def _swap():
    return {"label": "swap", "filesystem": "swap", "encrypt": True,
            "luks_name": "cryptswap", "unlock_fido2": 2,
            "luks_options": ["token-timeout=10s"]}


def _games(**over):
    part = {"label": "Games", "filesystem": "btrfs", "format": False,
            "encrypt": True, "luks_name": "games", "luks_uuid": GAMES_UUID,
            "unlock_fido2": 2, "luks_options": list(OPTS),
            "btrfs_subvolumes": [{"name": "@", "mountpoint": "/home/u/Games"}]}
    part.update(over)
    return part


DRACUT = {"initramfs": "dracut"}


def _cfg(*parts, **top):
    return {"initramfs": "dracut",
            "disks": {"disks": [{"device": "/dev/vda", "partitions": list(parts)}]},
            **top}


# --- the predicate ----------------------------------------------------------

def test_only_an_optional_encrypted_data_volume_opens_after_switch_root():
    assert opens_after_switch_root(_games(), DRACUT)
    assert not opens_after_switch_root(_games(luks_options=["token-timeout=10s"]), DRACUT)
    assert not opens_after_switch_root(_games(encrypt=False), DRACUT)
    assert not opens_after_switch_root(_games(unlock_keyfile="/k"), DRACUT)
    assert not opens_after_switch_root(_games(unlock_keydev="UUID=1234-ABCD"), DRACUT)
    assert not opens_after_switch_root({**_swap(), "luks_options": ["nofail"]}, DRACUT)
    assert not opens_after_switch_root({**_root(), "luks_options": ["nofail"]}, DRACUT)


# --- kernel command line ----------------------------------------------------

def _tokens(cfg):
    return KernelCmdlineAction(cfg)._desired_tokens()


def test_the_optional_data_volume_is_not_on_the_kernel_cmdline():
    tokens = _tokens(_cfg(_root(), _swap(), _games()))
    assert not [t for t in tokens if GAMES_UUID in t]


def test_root_and_swap_keep_exactly_their_unlock_parameters():
    with_games = _tokens(_cfg(_root(), _swap(), _games()))
    without = _tokens(_cfg(_root(), _swap()))
    assert with_games == without
    assert f"rd.luks.name={ROOT_UUID}=cryptroot" in with_games
    assert f"rd.luks.options={ROOT_UUID}=fido2-device=auto,token-timeout=10s" in with_games
    assert f"rd.luks.name={SWAP_UUID}=cryptswap" in with_games


def test_a_data_volume_without_nofail_stays_on_the_kernel_cmdline():
    tokens = _tokens(_cfg(_root(), _games(luks_options=["token-timeout=10s"])))
    assert f"rd.luks.name={GAMES_UUID}=games" in tokens
    assert f"rd.luks.options={GAMES_UUID}=fido2-device=auto,token-timeout=10s" in tokens


# --- crypttab -----------------------------------------------------------------

def _crypttab(cfg):
    return {ln.split()[0]: ln for ln in DracutBackend(cfg).crypttab().splitlines()
            if ln and not ln.startswith("#")}


def test_the_crypttab_line_carries_the_whole_unlock():
    line = _crypttab(_cfg(_root(), _games()))["games"]
    assert line == (f"games UUID={GAMES_UUID} none "
                    "luks,fido2-device=auto,token-timeout=10s,nofail,headless=true")


def test_trim_and_tpm2_ride_along():
    line = _crypttab(_cfg(_games(unlock_tpm2=True), enable_trim=True))["games"]
    assert line.split()[3] == ("luks,discard,tpm2-device=auto,fido2-device=auto,"
                               "token-timeout=10s,nofail,headless=true")


def test_root_and_swap_crypttab_lines_are_unchanged():
    with_games = _crypttab(_cfg(_root(), _swap(), _games()))
    without = _crypttab(_cfg(_root(), _swap()))
    assert with_games["cryptroot"] == without["cryptroot"]
    assert with_games["cryptswap"] == without["cryptswap"]
    assert with_games["cryptroot"].split()[3] == "luks,x-initrd.attach"


# --- sync reads the options back from crypttab --------------------------------

def _read(tmp_path, crypttab, cmdline=""):
    (tmp_path / "etc").mkdir(exist_ok=True)
    (tmp_path / "etc" / "crypttab").write_text(crypttab)
    action = DiskPartitionAction({"disks": []},
                                 ActionContext(target=Target(root=str(tmp_path))))
    with patch.object(DiskPartitionAction, "_kernel_cmdline_text", return_value=cmdline):
        return action._read_luks_options(GAMES_UUID)


def test_sync_reads_an_off_cmdline_volume_options_from_crypttab(tmp_path):
    crypttab = ("# Managed by dasik\n"
                f"cryptroot UUID={ROOT_UUID} none luks,x-initrd.attach\n"
                f"games UUID={GAMES_UUID} none "
                "luks,discard,fido2-device=auto,token-timeout=10s,nofail,headless=true\n")
    # `discard` is kept, as the cmdline path keeps it; the composed line
    # de-duplicates it against the one enable_trim derives, so it re-plans
    # to the same line either way.
    assert _read(tmp_path, crypttab) == ["discard", *OPTS]


def test_sync_still_prefers_the_kernel_cmdline(tmp_path):
    crypttab = f"games UUID={GAMES_UUID} none luks,headless=true\n"
    cmdline = f"rd.luks.options={GAMES_UUID}=fido2-device=auto,token-timeout=5s"
    assert _read(tmp_path, crypttab, cmdline) == ["token-timeout=5s"]


def test_sync_with_no_crypttab_line_captures_nothing(tmp_path):
    assert _read(tmp_path, f"other UUID={ROOT_UUID} none luks\n") == []


# --- review findings ---------------------------------------------------------

def test_mkinitcpio_keeps_the_old_path_since_nothing_else_writes_crypttab():
    """Only the dracut backend composes /etc/crypttab. With mkinitcpio (the
    default) moving the volume off the cmdline would leave it with no unlock."""
    cfg = _cfg(_root(), _games())
    cfg["initramfs"] = "mkinitcpio"
    assert not opens_after_switch_root(_games(), cfg)
    assert f"rd.luks.name={GAMES_UUID}=games" in _tokens(cfg)
    del cfg["initramfs"]                       # unset means mkinitcpio too
    assert f"rd.luks.name={GAMES_UUID}=games" in _tokens(cfg)


def test_sync_does_not_keep_the_old_parameters_as_hand_set_ones(tmp_path):
    """A machine still booting a dasik <= 0.28 entry carries rd.luks.* for the
    volume; sync must not capture them as explicit kernel_cmdline entries, or
    the next apply would put them right back."""
    from dasik.lib.actions.action_context import ActionContext as Ctx
    entries = tmp_path / "boot/loader/entries"
    entries.mkdir(parents=True)
    (tmp_path / "boot/loader/loader.conf").write_text("default arch\n")
    old = [f"rd.luks.name={GAMES_UUID}=games",
           f"rd.luks.options={GAMES_UUID}=fido2-device=auto,token-timeout=10s,nofail",
           f"rd.luks.key={GAMES_UUID}=/k"]
    (entries / "arch.conf").write_text(
        "title Arch\noptions root=/dev/mapper/cryptroot rw quiet " + " ".join(old) + "\n")
    cfg = {"bootloader": "sd-boot", **_cfg(_root(), _games())}
    action = KernelCmdlineAction(cfg, Ctx(target=Target(root=str(tmp_path))))
    kept = action.import_state(managed=[])["kernel_cmdline"]
    assert "quiet" in kept
    assert not [t for t in kept if GAMES_UUID in t]


def test_sync_ignores_crypttab_options_of_a_volume_without_nofail(tmp_path):
    """A hand-written crypttab line for a volume that stays on the cmdline
    must not turn into luks_options (they would then land on the cmdline)."""
    crypttab = f"games UUID={GAMES_UUID} none luks,fido2-device=auto,size512,token-timeout=5s\n"
    assert _read(tmp_path, crypttab) == []


def test_sync_ignores_a_keyfile_crypttab_line(tmp_path):
    crypttab = f"games UUID={GAMES_UUID} /k:UUID=1234-ABCD luks,nofail\n"
    assert _read(tmp_path, crypttab) == []


def test_a_declared_discard_survives_the_round_trip(tmp_path):
    """The cmdline path never subtracted `discard`; neither may crypttab."""
    crypttab = f"games UUID={GAMES_UUID} none luks,discard,nofail\n"
    assert _read(tmp_path, crypttab) == ["discard", "nofail"]
