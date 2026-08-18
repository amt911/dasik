import json
import os
import time
import urllib.parse
from unittest.mock import MagicMock, patch

from dasik import __main__ as cli
from tests.support.pacman import pacman_double


def _write(tmp_path, obj, raw=None):
    p = tmp_path / "c.json"
    p.write_text(raw if raw is not None else json.dumps(obj))
    return p


def _aur_http(packages=None, providers=None):
    """Serve both RPC endpoints from dicts (see test_aur_closure.py)."""
    packages = packages or {}
    providers = providers or {}
    calls = []

    def http_get(url: str) -> bytes:
        calls.append(url)
        parsed = urllib.parse.urlparse(url)
        if "/rpc/v5/info" in parsed.path:
            requested = urllib.parse.parse_qs(parsed.query).get("arg[]", [])
            results = [{"Name": n, "Depends": packages[n]} for n in requested
                       if n in packages]
        else:
            searched = urllib.parse.unquote(parsed.path.rsplit("/", 1)[-1])
            results = [{"Name": n} for n in providers.get(searched, [])]
        return json.dumps({"resultcount": len(results),
                           "results": results}).encode()

    http_get.calls = calls  # type: ignore[attr-defined]
    return http_get


def _run_check(config_path, *, flag=True, repo=(), provided=(), packages=None,
               providers=None, http_get=None, sync_dir=None):
    """Drive `dasik check [--resolve-aur]` with pacman + the AUR faked."""
    pacman = pacman_double(repo=list(repo), provided=list(provided))
    http = http_get or _aur_http(packages, providers)
    argv = ["check", str(config_path)] + (["--resolve-aur"] if flag else [])
    with patch("dasik.lib.actions.package_resolver.Command.execute", pacman), \
         patch("dasik.lib.validation.aur_closure.Command.execute", pacman), \
         patch("dasik.lib.actions.package_resolver._default_http_get", http), \
         patch.object(cli, "_SYNC_DB_DIR",
                      sync_dir if sync_dir is not None else _fresh_sync_dir()):
        return cli.main(argv), pacman, http


_FRESH_DIR_CACHE = {}


def _fresh_sync_dir(tmp=None, age_days=0):
    """A fake /var/lib/pacman/sync with one db of the given age."""
    import pathlib
    import tempfile
    base = pathlib.Path(tmp or tempfile.mkdtemp(prefix="dasik-sync-"))
    base.mkdir(parents=True, exist_ok=True)
    db = base / "core.db"
    db.write_bytes(b"")
    stamp = time.time() - age_days * 86400
    os.utime(db, (stamp, stamp))
    return base


# -- --resolve-aur ---------------------------------------------------------

def test_check_without_the_flag_stays_offline(tmp_path):
    p = _write(tmp_path, {"packages": ["base", "someaurthing"]})
    pacman = MagicMock()
    http = MagicMock()
    with patch("dasik.lib.actions.package_resolver.Command.execute", pacman), \
         patch("dasik.lib.actions.package_resolver._default_http_get", http):
        rc = cli.main(["check", str(p)])
    assert rc == 0
    pacman.assert_not_called()
    http.assert_not_called()


def test_resolve_aur_all_repo_config_passes(tmp_path, capsys):
    p = _write(tmp_path, {"packages": ["base", "git"]})
    rc, _, http = _run_check(p, repo=["base", "git"])
    assert rc == 0
    assert http.calls == []
    assert "OK" in capsys.readouterr().out


def test_resolve_aur_lists_the_names_that_resolve_to_the_aur(tmp_path, capsys):
    p = _write(tmp_path, {"packages": ["base", "yay"]})
    rc, _, _ = _run_check(p, repo=["base"], packages={"yay": []})
    assert rc == 0
    out = capsys.readouterr().out
    assert "AUR" in out and "yay" in out


def test_resolve_aur_reports_the_broken_chain_and_fails(tmp_path, capsys):
    p = _write(tmp_path, {"packages": ["lib32-gst-libav"]})
    rc, _, _ = _run_check(
        p,
        packages={"lib32-gst-libav": ["lib32-ffmpeg"],
                  "lib32-ffmpeg": ["lib32-libdav1d"]},
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "aur_dependency_unsatisfiable" in err
    assert "lib32-gst-libav → lib32-ffmpeg → lib32-libdav1d" in err


def test_resolve_aur_unknown_name_fails_under_the_error_policy(tmp_path, capsys):
    p = _write(tmp_path, {"packages": ["ghost-pkg"],
                          "package_policy": {"unknown": "error"}})
    rc, _, _ = _run_check(p, packages={})
    assert rc == 1
    assert "ghost-pkg" in capsys.readouterr().err


def test_resolve_aur_unknown_name_warns_under_warn_and_skip(tmp_path, capsys):
    p = _write(tmp_path, {"packages": ["ghost-pkg"]})
    rc, _, _ = _run_check(p, packages={})
    assert rc == 0
    assert "ghost-pkg" in capsys.readouterr().out


def test_resolve_aur_rpc_down_fails_loudly(tmp_path, capsys):
    def boom(url):
        raise OSError("no route to host")

    p = _write(tmp_path, {"packages": ["yay"]})
    rc, _, _ = _run_check(p, http_get=boom)
    assert rc == 1
    assert "unavailable" in capsys.readouterr().err.lower()


def test_resolve_aur_without_pacman_fails_with_a_named_cause(tmp_path, capsys):
    from dasik.lib.exceptions.exceptions import CommandNotFoundException

    def no_pacman(*a, **kw):
        raise CommandNotFoundException("pacman not found")

    p = _write(tmp_path, {"packages": ["yay"]})
    with patch("dasik.lib.actions.package_resolver.Command.execute", no_pacman), \
         patch("dasik.lib.validation.aur_closure.Command.execute", no_pacman), \
         patch("dasik.lib.actions.package_resolver._default_http_get",
               _aur_http()), \
         patch.object(cli, "_SYNC_DB_DIR", _fresh_sync_dir()):
        rc = cli.main(["check", str(p), "--resolve-aur"])
    assert rc == 1
    assert "pacman" in capsys.readouterr().err


def test_resolve_aur_warns_on_stale_sync_dbs(tmp_path, capsys):
    p = _write(tmp_path, {"packages": ["base"]})
    stale = _fresh_sync_dir(tmp_path / "stale-sync", age_days=12)
    rc, _, _ = _run_check(p, repo=["base"], sync_dir=stale)
    assert rc == 0
    out = capsys.readouterr().out
    assert "pacman -Sy" in out


def test_resolve_aur_stays_quiet_on_fresh_sync_dbs(tmp_path, capsys):
    p = _write(tmp_path, {"packages": ["base"]})
    fresh = _fresh_sync_dir(tmp_path / "fresh-sync", age_days=0)
    rc, _, _ = _run_check(p, repo=["base"], sync_dir=fresh)
    assert rc == 0
    assert "pacman -Sy" not in capsys.readouterr().out


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
