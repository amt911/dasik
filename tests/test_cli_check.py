import json

from dasik import __main__ as cli


def _write(tmp_path, obj, raw=None):
    p = tmp_path / "c.json"
    p.write_text(raw if raw is not None else json.dumps(obj))
    return p


def test_check_valid_config_ok(tmp_path, capsys):
    p = _write(tmp_path, {"timezone": {"region": "Europe", "city": "Madrid"},
                          "packages": ["base"]})
    rc = cli.main(["check", str(p)])
    assert rc == 0
    assert "OK" in capsys.readouterr().out


def test_check_rejects_invalid_json(tmp_path, capsys):
    p = _write(tmp_path, None, raw="{ not: valid json ,,, }")
    rc = cli.main(["check", str(p)])
    assert rc == 1
    assert "json" in capsys.readouterr().err.lower()


def test_check_rejects_schema_violation(tmp_path, capsys):
    # partition size is required + must be a valid unit; a disk with a bad
    # partition must fail validation.
    bad = {"disks": {"disks": [{"device": "/dev/vda", "partitions": [
        {"label": "root", "size": "notasize", "filesystem": "ext4"}]}]}}
    p = _write(tmp_path, bad)
    rc = cli.main(["check", str(p)])
    assert rc == 1
    err = capsys.readouterr().err.lower()
    assert "invalid" in err or "size" in err


def test_check_missing_file(tmp_path, capsys):
    rc = cli.main(["check", str(tmp_path / "nope.json")])
    assert rc == 1


def test_check_empty_config_is_valid(tmp_path, capsys):
    p = _write(tmp_path, {})
    assert cli.main(["check", str(p)]) == 0
