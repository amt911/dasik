"""Journey: an optional (nofail) encrypted data volume through every verb.

`main([...])` against a fake root, system commands mocked (the real thing is
scripts/vmtest/guest-preserved-luks*.sh). The volume must stay OFF the kernel
cmdline and be unlocked from its /etc/crypttab line, with the root's unlock
exactly as before. Round trips: plan -> apply -> plan silent for those two
files; sync -> check -> plan silent; generations lists the apply.
"""
import json
from unittest.mock import MagicMock, patch

from dasik.__main__ import main
from dasik.lib.actions.luks_uuid import luks_uuid
from tests.support.pacman import pacman_double

GAMES = "5eed5eed-0000-4000-8000-00000000c0de"
ROOT = luks_uuid("cryptroot")
OPTS = ["token-timeout=10s", "nofail", "headless=true"]


def _config():
    return {
        "bootloader": "sd-boot", "initramfs": "dracut", "packages": ["git"],
        "disks": {"disks": [
            {"device": "/dev/vda", "partition_table": "gpt", "wipe_disk": False,
             "partitions": [
                 {"label": "root", "size": "rest", "filesystem": "ext4",
                  "mountpoint": "/", "format": False, "encrypt": True,
                  "luks_name": "cryptroot", "luks_options": ["token-timeout=10s"]}]},
            {"device": "/dev/vdb", "partition_table": "gpt", "wipe_disk": False,
             "partitions": [
                 {"label": "Games", "size": "rest", "filesystem": "btrfs",
                  "format": False, "encrypt": True, "luks_name": "games",
                  "luks_uuid": GAMES, "luks_options": list(OPTS),
                  "btrfs_subvolumes": [{"name": "@", "mountpoint": "/home/u/Games"}]}]}]}}


def _root(tmp_path):
    for d in ("etc", "usr/bin", "boot/loader/entries"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    (tmp_path / "usr/bin/pacman").write_text("")
    (tmp_path / "etc/fstab").write_text("UUID=t / ext4 rw 0 1\n")   # an installed machine
    kernel = tmp_path / "usr/lib/modules/6.12.1-arch1-1"
    kernel.mkdir(parents=True)
    (kernel / "pkgbase").write_text("linux\n")
    (tmp_path / "boot/loader/loader.conf").write_text("default arch.conf\n")
    # sd-boot already installed, with its fallback entry: an installed machine,
    # so the bootloader does not rewrite the entry under the cmdline action.
    (tmp_path / "boot/EFI/systemd").mkdir(parents=True)
    (tmp_path / "boot/EFI/systemd/systemd-bootx64.efi").write_text("")
    (tmp_path / "boot/vmlinuz-linux").write_text("")
    (tmp_path / "boot/initramfs-linux.img").write_text("")
    (tmp_path / "boot/initramfs-linux-fallback.img").write_text("")
    (tmp_path / "boot/loader/entries/arch-fallback.conf").write_text(
        "title Arch (fallback)\nlinux /vmlinuz-linux\ninitrd /initramfs-linux-fallback.img\n"
        f"options rd.luks.name={ROOT}=cryptroot root=/dev/mapper/cryptroot rw\n")
    (tmp_path / "boot/loader/entries/arch.conf").write_text(
        "title Arch\nlinux /vmlinuz-linux\ninitrd /initramfs-linux.img\n"
        f"options rd.luks.name={ROOT}=cryptroot root=/dev/mapper/cryptroot rw\n")
    return tmp_path


def _exec():
    strict = pacman_double(installed=["git", "dracut"], repo=["git", "dracut"])

    def run(cmd, args=None, *a, **k):
        if isinstance(cmd, (list, tuple)):
            cmd, args = cmd[0], list(cmd[1:])
        if cmd == "pacman":
            return strict(cmd, args)
        if cmd == "genfstab":
            return MagicMock(stdout=b"UUID=t / ext4 rw 0 1\n", stderr=b"", returncode=0)
        return MagicMock(stdout=b"", stderr=b"", returncode=0)
    return run


def _invoke(argv):
    fake = _exec()
    with patch("dasik.lib.command_worker.command_worker.Command.execute", side_effect=fake), \
         patch("subprocess.run", side_effect=fake):
        return main(argv)


def _games_lines(out):
    """Plan lines that touch the kept volume: its LUKS UUID or its mapper name
    (a crypttab change for it carries both)."""
    return [ln for ln in out.splitlines() if GAMES in ln or "games" in ln]


def test_every_verb_keeps_the_volume_off_the_cmdline(tmp_path, capsys):
    root = _root(tmp_path)
    p = root / "config.json"
    p.write_text(json.dumps(_config()))
    entry = root / "boot/loader/entries/arch.conf"

    # check
    assert _invoke(["check", str(p)]) == 0

    # plan: nothing for the kept volume on the cmdline, the root's as before
    capsys.readouterr()
    assert _invoke(["plan", str(p), "--target", str(root), "--no-log"]) == 0
    out = capsys.readouterr().out
    assert f"rd.luks.options={ROOT}=token-timeout=10s" in out
    assert GAMES not in out

    # apply: the entry has no parameter for it, crypttab carries its unlock
    assert _invoke(["apply", str(p), "--target", str(root), "--yes", "--no-log"]) == 0
    options = entry.read_text()
    assert f"rd.luks.name={ROOT}=cryptroot" in options
    assert f"rd.luks.options={ROOT}=token-timeout=10s" in options
    assert GAMES not in options
    crypttab = (root / "etc/crypttab").read_text().splitlines()
    assert f"games UUID={GAMES} none luks,{','.join(OPTS)}" in crypttab
    assert f"cryptroot UUID={ROOT} none luks,x-initrd.attach" in crypttab

    # plan again: silent for the cmdline and crypttab
    capsys.readouterr()
    assert _invoke(["plan", str(p), "--target", str(root), "--no-log"]) == 0
    out = capsys.readouterr().out
    assert "[kernel_cmdline]" not in out
    assert _games_lines(out) == []

    # generations records the apply
    capsys.readouterr()
    assert _invoke(["generations", "--target", str(root)]) == 0
    assert "1" in capsys.readouterr().out

    # sync -> check -> plan: the options come back from crypttab, no stray
    # kernel parameter is invented, and the capture re-plans to nothing here
    with patch("dasik.lib.actions.disk_partition_action.DiskPartitionAction"
               "._kernel_cmdline_text", return_value=entry.read_text()):
        assert _invoke(["sync", str(p), "--target", str(root), "--no-log"]) == 0
    captured = json.loads(p.read_text())
    games = [q for d in captured["disks"]["disks"] for q in d["partitions"]
             if q.get("luks_name") == "games"]
    assert games and games[0]["luks_options"] == OPTS
    assert not [t for t in captured.get("kernel_cmdline", []) if GAMES in t]
    assert _invoke(["check", str(p)]) == 0
    capsys.readouterr()
    assert _invoke(["plan", str(p), "--target", str(root), "--no-log"]) == 0
    out = capsys.readouterr().out
    assert "[kernel_cmdline]" not in out
    assert _games_lines(out) == []


def test_dropping_the_volume_from_the_config_destroys_nothing(tmp_path, capsys):
    """The block removed: the disk is not dasik's to wipe, its keyslots are not
    touched, and no kernel parameter appears or disappears for it."""
    root = _root(tmp_path)
    p = root / "config.json"
    p.write_text(json.dumps(_config()))
    assert _invoke(["apply", str(p), "--target", str(root), "--yes", "--no-log"]) == 0
    cfg = _config()
    cfg["disks"]["disks"].pop()                     # Games gone from the config
    p.write_text(json.dumps(cfg))
    capsys.readouterr()
    assert _invoke(["plan", str(p), "--target", str(root), "--no-log"]) == 0
    out = capsys.readouterr().out
    assert "[luks_token] remove" not in out
    assert "[kernel_cmdline]" not in out
    assert "ERASES" not in out
