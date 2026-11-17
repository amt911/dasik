"""Hardware tokens on a PRESERVED encrypted disk that is not open.

A reinstall keeps a data disk (``wipe_disk: false``, ``format: false``, an
explicit ``luks_uuid``) whose header already carries its FIDO2 keyslots. On the
live ISO that volume is closed, so ``cryptsetup status <name>`` names no device,
and the plan used to announce every declared keyslot as "may not be enrolled" —
the installer then asked to enrol keys the header already had.

``cryptsetup luksDump`` reads a header WITHOUT unlocking it, so the closed
volume is read through ``/dev/disk/by-uuid/<luks_uuid>`` instead. Only for a
preserved partition: a partition about to be formatted may still have an OLD
header under the same (deterministic) UUID, and counting that one's tokens would
skip the enrolment the new header needs.
"""
import pytest

from dasik.lib.actions import luks_token_action as mod
from dasik.lib.actions.luks_token_action import LuksTokenAction
from dasik.lib.state.change import Op

_UUID = "11111111-2222-3333-4444-555555555555"
_BY_UUID = f"/dev/disk/by-uuid/{_UUID}"


def _dump(fido2_slots=()):
    out = ["LUKS header information\nVersion:        2\n", "Keyslots:\n"]
    for s in sorted({0, *fido2_slots}):
        out.append(f"  {s}: luks2\n        Key:        512 bits\n")
    out.append("Tokens:\n")
    for n, s in enumerate(fido2_slots):
        out.append(f"  {n}: systemd-fido2\n        Keyslot:    {s}\n")
    out.append("Digests:\n  0: pbkdf2\n")
    return "".join(out)


def _action(monkeypatch, *, fido2_slots=(1, 2), fmt=False, wipe=False,
            by_uuid_exists=True, explicit_uuid=True):
    part = {"label": "Games", "encrypt": True, "luks_name": "games",
            "format": fmt, "unlock_fido2": 2}
    if explicit_uuid:
        part["luks_uuid"] = _UUID
    config = {"disks": {"disks": [{"device": "/dev/vdb", "wipe_disk": wipe,
                                   "partitions": [part]}]}}
    action = LuksTokenAction(config, None)
    action._luks_device = lambda name: None            # the volume is CLOSED
    reads = []

    def dump(dev):
        reads.append(dev)
        return _dump(fido2_slots) if dev == _BY_UUID else ""
    action._dump = dump
    action.reads = reads
    monkeypatch.setattr(mod.os.path, "exists",
                        lambda p: by_uuid_exists and p == _BY_UUID)
    return action


def test_closed_preserved_volume_with_its_keys_plans_nothing(monkeypatch):
    action = _action(monkeypatch)
    assert action.plan(managed=[]) == []
    assert action.reads == [_BY_UUID]


def test_closed_preserved_volume_missing_a_key_says_why(monkeypatch):
    changes = _action(monkeypatch, fido2_slots=(1,)).plan(managed=[])
    assert [c.item for c in changes] == ["games:fido2#2"]
    assert changes[0].op is Op.INSTALL
    assert "no luks_password" in changes[0].reason


def test_actual_counts_the_closed_preserved_header(monkeypatch):
    assert _action(monkeypatch).actual() == {"games:fido2", "games:fido2#2"}


@pytest.mark.parametrize("kw", [
    {"fmt": True},                 # about to be formatted: the old header lies
    {"wipe": True},                # the whole disk is erased
    {"explicit_uuid": False},      # derived UUID: not a disk dasik kept
    {"by_uuid_exists": False},     # no such disk plugged in
])
def test_only_a_preserved_present_volume_is_read_closed(monkeypatch, kw):
    action = _action(monkeypatch, **kw)
    changes = action.plan(managed=[])
    assert [c.item for c in changes] == ["games:fido2", "games:fido2#2"]
    assert all("not open" in c.reason for c in changes)
    assert action.reads == []
