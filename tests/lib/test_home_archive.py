"""Publishing the `$HOME` archives config-saver produces.

config-saver writes one timestamped directory per configuration per run, so
"upload my backups" is not one file: it is the newest archive of each
configuration, which on a real machine was seven files and 275 MB. Doing that
by hand means remembering a shell pipeline; doing it wrong means publishing an
old archive, or a plaintext one.

The refusal is the part worth having: an archive that is not encrypted must
never reach a release. `$HOME` holds browser profiles and SSH config, a release
asset is a URL, and "I forgot to set up encryption" is exactly the mistake this
catches.
"""
from pathlib import Path

import pytest

from dasik.lib.home_archive import (
    HomeArchiveError,
    latest_archives,
    publish_archives,
)


def _archive(root: Path, config: str, stamp: str, suffix: str = ".tar.gz.age") -> Path:
    path = root / config / stamp / f"{config}-{stamp}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    return path


# --- finding what to publish ------------------------------------------------ #

def test_the_newest_archive_of_each_configuration(tmp_path):
    _archive(tmp_path, "zsh", "20260101-000000")
    newest_zsh = _archive(tmp_path, "zsh", "20260815-120000")
    wallpapers = _archive(tmp_path, "wallpapers", "20260814-000000")

    assert latest_archives(tmp_path) == {"zsh": newest_zsh,
                                         "wallpapers": wallpapers}


def test_the_sidecar_files_config_saver_writes_are_not_archives(tmp_path):
    """Each run also drops a description.txt beside the archive. Sorting by
    file name picks THAT ("d" > "claude-…"), and publishing it would upload a
    text file in place of the backup — found against real archives."""
    archive = _archive(tmp_path, "claude", "20260815-120000")
    (archive.parent / "description.txt").write_text("a note")

    assert latest_archives(tmp_path) == {"claude": archive}


def test_the_newest_is_decided_by_the_run_not_the_file_name(tmp_path):
    """The timestamp lives in the directory; file names within a run vary."""
    _archive(tmp_path, "zsh", "20260815-120000")
    newest = _archive(tmp_path, "zsh", "20260815-235959")

    assert latest_archives(tmp_path)["zsh"] == newest


def test_a_configuration_that_produced_nothing_is_absent(tmp_path):
    """config-saver skips a document needing root, and writes no archive."""
    _archive(tmp_path, "zsh", "20260815-120000")
    (tmp_path / "etc-files" / "20260815-120000").mkdir(parents=True)

    assert set(latest_archives(tmp_path)) == {"zsh"}


def test_no_archives_at_all_is_an_error_naming_the_directory(tmp_path):
    with pytest.raises(HomeArchiveError, match=str(tmp_path)):
        latest_archives(tmp_path)


# --- publishing ------------------------------------------------------------- #

class _Recorder:
    """Stands in for the `gh` calls, recording argv."""

    def __init__(self, view_rc: int = 0):
        self.calls: list = []
        self.view_rc = view_rc

    def __call__(self, cmd, args, **kwargs):
        self.calls.append((cmd, list(args)))
        rc = self.view_rc if "view" in args else 0

        class _R:
            returncode, stdout, stderr = rc, "", ""
        return _R()


def test_a_plaintext_archive_is_refused(tmp_path, monkeypatch):
    plain = _archive(tmp_path, "zsh", "20260815-120000", suffix=".tar.gz")
    recorder = _Recorder()
    monkeypatch.setattr("dasik.lib.home_archive.Command.execute", recorder)

    with pytest.raises(HomeArchiveError, match="not encrypted"):
        publish_archives("amt911/data", "torre", {"zsh": plain})

    assert recorder.calls == [], "nothing may be uploaded when one is plaintext"


def test_existing_release_is_updated_in_place(tmp_path, monkeypatch):
    archive = _archive(tmp_path, "zsh", "20260815-120000")
    recorder = _Recorder(view_rc=0)          # the release exists
    monkeypatch.setattr("dasik.lib.home_archive.Command.execute", recorder)

    publish_archives("amt911/data", "torre", {"zsh": archive}, user="andres")

    uploads = [args for _cmd, args in recorder.calls if "upload" in args]
    assert len(uploads) == 1
    assert "--clobber" in uploads[0], "a re-publish must replace, not accumulate"
    assert str(archive) in uploads[0]
    # every call goes through the invoking user: gh's credentials are theirs
    assert all(cmd == "su" for cmd, _ in recorder.calls)


def test_a_missing_release_is_created(tmp_path, monkeypatch):
    archive = _archive(tmp_path, "zsh", "20260815-120000")
    recorder = _Recorder(view_rc=1)          # no such release yet
    monkeypatch.setattr("dasik.lib.home_archive.Command.execute", recorder)

    publish_archives("amt911/data", "torre", {"zsh": archive})

    assert any("create" in args for _cmd, args in recorder.calls)
    assert not any("upload" in args for _cmd, args in recorder.calls), \
        "create already carries the assets; uploading them again is a second copy"


# --- config-saver's restic store (3.5.0+) ------------------------------------ #
# Once the archives live in config-saver's deduplicated restic store there is
# no `<name>/<stamp>/*.tar.gz.age` left on disk: the newest backup of each
# configuration has to be EXPORTED before it can be published.

from types import SimpleNamespace  # noqa: E402

from dasik.lib import home_archive  # noqa: E402
from dasik.lib.home_archive import has_restic_store, store_exports  # noqa: E402


def test_a_home_with_a_restic_store_is_detected(tmp_path):
    assert has_restic_store(tmp_path) is False
    (tmp_path / ".config/config-saver/restic").mkdir(parents=True)
    assert has_restic_store(tmp_path) is True


def _fake_export(monkeypatch, names, returncode=0, stderr=""):
    """Stand in for `config-saver --export-all-configs --output DIR`."""
    calls = []

    def execute(cmd, args, **kwargs):
        calls.append([cmd, *args])
        out = Path(args[args.index("--output") + 1])
        for name in names:
            (out / name).write_text("x")
        # Command.execute hands back BYTES, as subprocess does — a str here
        # once hid a b'/home/…' path that only the VM caught.
        return SimpleNamespace(returncode=returncode, stdout=b"",
                               stderr=stderr.encode())

    monkeypatch.setattr(home_archive.Command, "execute", staticmethod(execute))
    return calls


def test_exports_run_as_the_user_and_map_each_configuration(tmp_path, monkeypatch):
    calls = _fake_export(monkeypatch, [
        "zsh-20261019-030000.tar.gz.age",
        "claude-conversations-20261019-030000.tar.gz.age",
    ])

    archives, warnings = store_exports("andres", tmp_path)

    assert calls == [["su", "-", "andres", "-c", 'config-saver "$@"', "--",
                      "config-saver", "--export-all-configs",
                      "--output", str(tmp_path)]]
    assert archives == {
        "zsh": tmp_path / "zsh-20261019-030000.tar.gz.age",
        "claude-conversations":
            tmp_path / "claude-conversations-20261019-030000.tar.gz.age",
    }
    assert warnings == []


def test_exports_without_a_user_run_config_saver_directly(tmp_path, monkeypatch):
    calls = _fake_export(monkeypatch, ["zsh-20261019-030000.tar.gz.age"])

    store_exports(None, tmp_path)

    assert calls == [["config-saver", "--export-all-configs",
                      "--output", str(tmp_path)]]


def test_a_failed_export_with_nothing_exported_is_an_error(tmp_path, monkeypatch):
    _fake_export(monkeypatch, [], returncode=5,
                 stderr="cannot decrypt the store password with /k")

    with pytest.raises(HomeArchiveError, match="cannot decrypt the store password") as e:
        store_exports("andres", tmp_path)
    assert "b'" not in str(e.value)


def test_a_partly_failed_export_still_returns_what_it_exported(tmp_path, monkeypatch):
    """One unreadable document must not stop the others from being published —
    the same as on disk, where a configuration that produced nothing is just
    absent. The failure is surfaced, not swallowed."""
    _fake_export(monkeypatch, ["zsh-20261019-030000.tar.gz.age"], returncode=3,
                 stderr="Error exporting kde: invalid configuration")

    archives, warnings = store_exports("andres", tmp_path)

    assert set(archives) == {"zsh"}
    assert len(warnings) == 1 and "invalid configuration" in warnings[0]
    assert "exit 3" in warnings[0]


def test_plaintext_exports_are_still_refused_by_publishing(tmp_path, monkeypatch):
    """An export of a document without `encrypt:` is a plain .tar.gz; the
    refusal that guards the on-disk path guards this one too."""
    _fake_export(monkeypatch, ["zsh-20261019-030000.tar.gz"])
    archives, _ = store_exports("andres", tmp_path)

    with pytest.raises(HomeArchiveError, match="not encrypted"):
        publish_archives("me/repo", "torre", archives)


# --- the export directory: made and removed AS THE USER ---------------------- #
# Root (sudo) never creates, chowns or deletes inside the user's home: a
# symlinked ~/.cache would otherwise make root write wherever it points.

from dasik.lib.home_archive import make_export_dir, remove_export_dir  # noqa: E402


def _recording(monkeypatch, stdout="", returncode=0):
    calls = []

    def execute(cmd, args, **kwargs):
        calls.append([cmd, *args])
        return SimpleNamespace(returncode=returncode, stdout=stdout.encode(),
                               stderr=b"boom")

    monkeypatch.setattr(home_archive.Command, "execute", staticmethod(execute))
    return calls


def test_the_export_dir_is_made_by_the_user(tmp_path, monkeypatch):
    made = tmp_path / ".cache" / "dasik-home-export-abc123"
    calls = _recording(monkeypatch, stdout=f"{made}\n")

    assert make_export_dir(tmp_path, "andres") == made
    assert calls == [["su", "-", "andres", "-c",
                      'mkdir -p "$HOME/.cache" && '
                      'mktemp -d "$HOME/.cache/dasik-home-export-XXXXXX"']]


def test_an_export_dir_outside_the_users_cache_is_refused(tmp_path, monkeypatch):
    _recording(monkeypatch, stdout="/etc/dasik-home-export-abc123\n")

    with pytest.raises(HomeArchiveError, match="unexpected export directory"):
        make_export_dir(tmp_path, "andres")


def test_failing_to_make_the_export_dir_is_a_home_archive_error(tmp_path, monkeypatch):
    _recording(monkeypatch, returncode=1)

    with pytest.raises(HomeArchiveError, match="could not create"):
        make_export_dir(tmp_path, "andres")


def test_without_a_user_the_dir_is_made_directly(tmp_path):
    made = make_export_dir(tmp_path, None)

    assert made.parent == tmp_path / ".cache"
    assert made.name.startswith("dasik-home-export-")
    assert made.stat().st_mode & 0o777 == 0o700


def test_a_broken_cache_without_a_user_is_a_home_archive_error(tmp_path):
    (tmp_path / ".cache").symlink_to(tmp_path / "nowhere")

    with pytest.raises(HomeArchiveError, match="could not create"):
        make_export_dir(tmp_path, None)


def test_the_export_dir_is_removed_by_the_user(tmp_path, monkeypatch):
    calls = _recording(monkeypatch)
    target = tmp_path / ".cache" / "dasik-home-export-abc123"

    remove_export_dir(target, "andres")

    assert calls == [["su", "-", "andres", "-c", 'rm -rf -- "$1"', "--", "rm",
                      str(target)]]


def test_without_a_user_the_dir_is_removed_directly(tmp_path):
    target = make_export_dir(tmp_path, None)
    (target / "x.tar.gz.age").write_text("x")

    remove_export_dir(target, None)

    assert not target.exists()


def test_exit_zero_with_nothing_exported_is_an_error(tmp_path, monkeypatch):
    _fake_export(monkeypatch, [])

    with pytest.raises(HomeArchiveError, match="exported nothing"):
        store_exports("andres", tmp_path)


def test_an_export_name_without_a_stamp_keeps_its_stem(tmp_path, monkeypatch):
    _fake_export(monkeypatch, ["odd.tar.gz.age"])

    archives, _ = store_exports("andres", tmp_path)

    assert archives == {"odd": tmp_path / "odd.tar.gz.age"}


def test_a_chatty_login_profile_does_not_break_the_export_dir(tmp_path, monkeypatch):
    """`su -` is a login shell: a profile that greets (fastfetch, motd) prints
    before mktemp. The path is the last line, not all of stdout."""
    made = tmp_path / ".cache" / "dasik-home-export-abc123"
    _recording(monkeypatch, stdout=f"Welcome back!\n\n{made}\n")

    assert make_export_dir(tmp_path, "andres") == made
