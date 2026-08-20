"""`cryptsetup luksDump` hides half the Tokens section on stdout.

Reported from a real installed laptop: two FIDO2 keys enrolled, `luksDump`
showing both on screen, and `dasik plan` still asking for `cryptroot:fido2#2`.

The cause is that cryptsetup delegates each token's detail to that token's
EXTERNAL PLUGIN — systemd ships `libcryptsetup-token-systemd-fido2.so` — and the
plugin writes its part to **stderr**. A terminal merges the two streams, so a
human sees a complete dump; a program reading `result.stdout` sees:

    Tokens:
      0: systemd-fido2

and nothing else. No second token, no `Keyslot:` lines. Measured on cryptsetup
2.8.7 against a header carrying two `systemd-fido2` tokens.

Three things read that dump, and all three were wrong:

* the token COUNT — one instead of two, so a second key was planned for ever;
* the keyslot NUMBERS, used by a removal. Empty, so `_wipe` fell back to
  `--wipe-slot=fido2`, which wipes EVERY fido2 keyslot — dropping from two keys
  to one would have taken both;
* the "is there a keyslot no token owns?" guard, which decides whether wiping is
  safe at all.

`--disable-external-tokens` keeps cryptsetup's own generic dump, which prints
the type and the keyslot of every token, on stdout, where a program can read it.
"""
from unittest.mock import MagicMock, patch

from dasik.lib.actions.disk_partition_action import DiskPartitionAction
from dasik.lib.actions.luks_token_action import LuksTokenAction
from dasik.lib.state.change import Op

# EXACTLY what cryptsetup 2.8.7 puts on stdout for a header with two
# systemd-fido2 tokens, when the systemd token plugin is installed.
_PLUGIN_STDOUT = """LUKS header information
Version:        2
Keyslots:
  0: luks2
        Key:        512 bits
  1: luks2
        Key:        512 bits
  2: luks2
        Key:        512 bits
Tokens:
  0: systemd-fido2
"""

# The same header dumped with --disable-external-tokens: cryptsetup's own
# generic rendering, complete, on stdout.
_GENERIC_STDOUT = """LUKS header information
Version:        2
Keyslots:
  0: luks2
        Key:        512 bits
  1: luks2
        Key:        512 bits
  2: luks2
        Key:        512 bits
Tokens:
  0: systemd-fido2
\tKeyslot:    1
  1: systemd-fido2
\tKeyslot:    2
Digests:
  0: pbkdf2
"""


def _executor(generic=_GENERIC_STDOUT, plugin=_PLUGIN_STDOUT, flag_supported=True):
    """A `cryptsetup` double that behaves like the real one.

    Without `--disable-external-tokens` it returns the truncated stdout the
    plugin leaves behind; with it, the complete generic dump.
    """
    def execute(cmd, args=None, **kwargs):
        args = list(args or [])
        if cmd == "cryptsetup" and args[:1] == ["luksDump"]:
            if "--disable-external-tokens" in args:
                if not flag_supported:
                    raise RuntimeError("unknown option --disable-external-tokens")
                return MagicMock(stdout=generic.encode(), returncode=0)
            return MagicMock(stdout=plugin.encode(), returncode=0)
        if cmd == "cryptsetup" and args[:1] == ["status"]:
            return MagicMock(stdout=b"  device: /dev/nvme0n1p3\n", returncode=0)
        return MagicMock(stdout=b"", returncode=0)
    return execute


def _token_action(keys=2):
    part = {"label": "root", "encrypt": True, "luks_name": "cryptroot",
            "mountpoint": "/", "luks_password": "hunter2", "unlock_fido2": keys}
    return LuksTokenAction(
        {"disks": {"disks": [{"device": "/dev/nvme0n1", "partitions": [part]}]}}, None)


# --- the reported symptom -------------------------------------------------- #

def test_two_enrolled_keys_plan_nothing():
    """The bug, end to end: both keys are in the header, so nothing is missing."""
    action = _token_action(keys=2)
    with patch("dasik.lib.actions.luks_token_action.Command.execute",
               side_effect=_executor()):
        changes = action.plan(managed=["cryptroot:fido2", "cryptroot:fido2#2"])

    assert changes == [], "the header carries both keys; nothing is to be enrolled"


def test_the_dump_asks_cryptsetup_not_to_call_the_plugins():
    action = _token_action()
    with patch("dasik.lib.actions.luks_token_action.Command.execute",
               side_effect=_executor()) as execute:
        action._dump("/dev/nvme0n1p3")

    args = execute.call_args[0][1]
    assert "--disable-external-tokens" in args


def test_a_cryptsetup_without_the_flag_still_gets_an_answer():
    """Older cryptsetup: fall back rather than report an unreadable header,
    which would make `plan` claim nothing is enrolled at all."""
    action = _token_action()
    with patch("dasik.lib.actions.luks_token_action.Command.execute",
               side_effect=_executor(flag_supported=False)):
        dump = action._dump("/dev/nvme0n1p3")

    assert "systemd-fido2" in dump


# --- the two silent consequences ------------------------------------------- #

def test_a_removal_names_the_keyslot_it_means():
    """With an empty slot list `_wipe` fell back to `--wipe-slot=fido2`, which
    takes EVERY fido2 keyslot: dropping to one key would have wiped both."""
    action = _token_action(keys=1)
    with patch("dasik.lib.actions.luks_token_action.Command.execute",
               side_effect=_executor()) as execute:
        changes = action.plan(managed=["cryptroot:fido2", "cryptroot:fido2#2"])
        assert [c.item for c in changes if c.op is Op.REMOVE] == ["cryptroot:fido2#2"]
        action.apply(changes)

    wiped = [a for call in execute.call_args_list for a in (call[0][1] or [])
             if isinstance(a, str) and a.startswith("--wipe-slot=")]
    assert wiped == ["--wipe-slot=2"], "by number, and only the surplus one"


def test_the_last_way_in_is_still_protected():
    """The guard needs the token keyslots: with none read, a header whose only
    keyslot is a token looked like it still had a passphrase."""
    only_token = _GENERIC_STDOUT.replace("  0: luks2\n        Key:        512 bits\n", "", 1)
    action = _token_action(keys=0)
    with patch("dasik.lib.actions.luks_token_action.Command.execute",
               side_effect=_executor(generic=only_token)):
        changes = action.plan(managed=["cryptroot:fido2", "cryptroot:fido2#2"])

    assert changes == [], "nothing may be wiped while it is the only way in"


# --- and the capture, which reads the same dump ---------------------------- #

def test_sync_captures_both_keys():
    """Otherwise `sync` rewrites a working `unlock_fido2: 2` down to `true`."""
    config = {"disks": [{
        "device": "/dev/nvme0n1", "partition_table": "gpt", "wipe_disk": False,
        "partitions": [{
            "label": "root", "size": "rest", "filesystem": "btrfs", "mountpoint": "/",
            "encrypt": True, "luks_name": "cryptroot", "unlock_fido2": 2,
            "format": False,
        }],
    }]}
    action = DiskPartitionAction(config, None)
    action._luks_backing_device = lambda name: "/dev/nvme0n1p3"
    action._read_luks_uuid = lambda name: "u-u-i-d"
    action._read_luks_options = lambda uuid: []
    action._capture_unlock_keyfile = lambda part, uuid: None
    action._live_subvol_options = lambda: {}

    with patch("dasik.lib.actions.disk_partition_action.Command.execute",
               side_effect=_executor()):
        frag = action.import_state()

    part = frag["disks"]["disks"][0]["partitions"][0]
    assert part["unlock_fido2"] == 2
