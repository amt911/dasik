"""Guard tests for scripts/vmtest/apply_disks_only.py.

The loopback layer's disk driver must REFUSE any device that is not a
file-backed loop/nbd device, before it runs a single command. These tests
exercise the real script via subprocess and assert the exit codes, so the
safety guard on the most destructive code path is itself under test — and they
need no root and touch no disk (refusal happens before any tool runs).
"""
import json
import subprocess
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "vmtest" / "apply_disks_only.py"

_REFUSE = 3   # non-disposable device
_USAGE = 2    # bad args / empty config


def _run(config: dict, tmp_path: Path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps(config))
    return subprocess.run(
        [sys.executable, str(_SCRIPT), str(cfg)],
        capture_output=True, text=True,
    )


def _disks(device: str) -> dict:
    return {
        "disks": {
            "disks": [
                {
                    "device": device,
                    "partition_table": "gpt",
                    "wipe_disk": False,
                    "partitions": [
                        {"label": "root", "size": "rest", "filesystem": "ext4",
                         "partition_type": "linux", "mountpoint": "/", "format": True},
                    ],
                }
            ]
        }
    }


def test_script_exists_and_is_executable():
    assert _SCRIPT.exists()


def test_refuses_real_sata_disk(tmp_path):
    r = _run(_disks("/dev/sda"), tmp_path)
    assert r.returncode == _REFUSE
    assert "REFUSING" in r.stderr


def test_refuses_nvme_disk(tmp_path):
    r = _run(_disks("/dev/nvme0n1"), tmp_path)
    assert r.returncode == _REFUSE
    assert "REFUSING" in r.stderr


def test_refuses_virtio_disk(tmp_path):
    r = _run(_disks("/dev/vda"), tmp_path)
    assert r.returncode == _REFUSE


def test_empty_config_is_usage_error(tmp_path):
    r = _run({}, tmp_path)
    assert r.returncode == _USAGE


def test_loop_device_passes_the_guard(tmp_path):
    """A /dev/loop* device is NOT refused — the guard lets it through to the
    real disk action. (It then fails downstream because no such loop device is
    attached here, but crucially it is never rejected as a real disk.)"""
    r = _run(_disks("/dev/loop7"), tmp_path)
    assert r.returncode != _REFUSE
    assert "REFUSING" not in r.stderr


def test_nbd_device_passes_the_guard(tmp_path):
    r = _run(_disks("/dev/nbd3"), tmp_path)
    assert r.returncode != _REFUSE
    assert "REFUSING" not in r.stderr
