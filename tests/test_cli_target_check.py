"""The verbs that drive a target refuse to start without `arch-chroot`.

`plan`/`apply` default to `--target /mnt`, which routes every command through
`arch-chroot`. On an installed host that binary is usually absent, and the run
used to die mid-probe with "Binary not found: arch-chroot". The gate runs before
any action, so `apply` cannot fail half-way for a reason known up front.
"""
import json

import pytest

from dasik import __main__ as cli


def _write_config(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"packages": []}))
    return p


@pytest.fixture
def no_arch_chroot(monkeypatch):
    monkeypatch.setattr("dasik.lib.target.target_check.which", lambda _: None)


@pytest.mark.parametrize("argv", [
    ["plan", "CONFIG"],
    ["apply", "CONFIG", "--yes"],
    ["sync", "CONFIG", "--target", "/mnt"],
    ["rollback", "--target", "/mnt", "--yes"],
])
def test_verb_refuses_a_chroot_target_without_the_binary(argv, tmp_path, capsys,
                                                        no_arch_chroot):
    cfg = _write_config(tmp_path)
    rc = cli.main([str(cfg) if a == "CONFIG" else a for a in argv])

    assert rc == 1
    err = capsys.readouterr().err
    assert "arch-install-scripts" in err
    assert "--target /" in err


def test_running_host_target_is_not_gated(tmp_path, capsys, no_arch_chroot,
                                          monkeypatch):
    """`--target /` needs no chroot, so the gate must let it through."""
    cfg = _write_config(tmp_path)
    monkeypatch.setattr(cli, "setup_actions", lambda: None)
    monkeypatch.setattr(cli, "get_default_registry",
                        lambda: type("R", (), {"get_all_actions": staticmethod(lambda: [])})())

    rc = cli.main(["plan", str(cfg), "--target", "/"])

    assert rc == 0
    assert "arch-install-scripts" not in capsys.readouterr().err
