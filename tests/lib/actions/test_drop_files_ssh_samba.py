"""`sync` must read back the two /etc files a machine really has and dasik did not.

`sshd_config.d` and `smb.conf` were the exact shape the project calls a one-way
street: `apply` writes them, `plan` shows them, and `sync` cannot see them — so
capturing the machine and re-applying the capture makes them silently vanish.
On the machine that found this, that meant an SSH hardening drop-in and four
Samba shares (`printers`, `shared`, `dasik`, `pc-games`).

Neither is reachable through `_SECTIONS`: one is a drop-in directory nobody
listed, the other is a single file, like `/etc/crypttab`. They are discovered
into `files`, so no schema field is invented for them and a config that already
declares the path keeps winning.

`/etc/samba/` is handled by **whitelisting exactly one file** rather than
blacklisting the credential stores. `smbpasswd`, `secrets.tdb` and `passdb.tdb`
are then unreachable by construction, which is a stronger guarantee than a list
somebody has to keep complete.
"""
from unittest.mock import MagicMock, patch

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.drop_files_action import DropFilesAction
from dasik.lib.target.target import Target


_OWNED = {"/etc/ssh/sshd_config.d/99-vendor.conf"}


def _pacman(cmd, args=None, *rest, **kw):
    if cmd == "pacman" and args and args[0] == "-Qo":
        return MagicMock(returncode=0 if args[1] in _OWNED else 1)
    return MagicMock(returncode=1)


def _w(tmp_path, rel, content):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def _files(tmp_path, config=None):
    action = DropFilesAction(config or {}, ActionContext(target=Target(root=str(tmp_path))))
    with patch("dasik.lib.actions.drop_files_action.Command.execute", side_effect=_pacman):
        captured = action.import_state(managed=[])
    return {e["path"]: e["content"] for e in captured["files"]}


# --------------------------------------------------------------------------- #
#  /etc/ssh/sshd_config.d
# --------------------------------------------------------------------------- #

def test_an_sshd_drop_in_is_captured(tmp_path):
    _w(tmp_path, "etc/ssh/sshd_config.d/10-dasik.conf",
       "PermitRootLogin prohibit-password\nPasswordAuthentication no\n")

    files = _files(tmp_path)

    assert files["/etc/ssh/sshd_config.d/10-dasik.conf"] == (
        "PermitRootLogin prohibit-password\nPasswordAuthentication no\n")


def test_a_package_owned_sshd_drop_in_is_skipped(tmp_path):
    _w(tmp_path, "etc/ssh/sshd_config.d/99-vendor.conf", "# shipped by a package\n")

    assert "/etc/ssh/sshd_config.d/99-vendor.conf" not in _files(tmp_path)


def test_a_symlinked_sshd_drop_in_is_skipped(tmp_path):
    real = _w(tmp_path, "etc/ssh/sshd_config.d/10-dasik.conf", "X11Forwarding no\n")
    (tmp_path / "etc/ssh/sshd_config.d/20-link.conf").symlink_to(real)

    assert "/etc/ssh/sshd_config.d/20-link.conf" not in _files(tmp_path)


def test_only_conf_files_are_captured_from_sshd_config_d(tmp_path):
    """sshd itself reads `Include /etc/ssh/sshd_config.d/*.conf`, so anything
    else in there is a leftover the daemon never reads — a `.pacnew`, an editor
    backup — and capturing it would put a file into the config that changes
    nothing."""
    _w(tmp_path, "etc/ssh/sshd_config.d/10-dasik.conf", "X11Forwarding no\n")
    _w(tmp_path, "etc/ssh/sshd_config.d/10-dasik.conf.pacnew", "# pacnew\n")
    _w(tmp_path, "etc/ssh/sshd_config.d/notes.txt", "reminder\n")

    files = _files(tmp_path)

    assert "/etc/ssh/sshd_config.d/10-dasik.conf" in files
    assert "/etc/ssh/sshd_config.d/10-dasik.conf.pacnew" not in files
    assert "/etc/ssh/sshd_config.d/notes.txt" not in files


def test_sshd_config_itself_is_not_captured(tmp_path):
    """The main file belongs to openssh. Capturing a whole vendor config
    verbatim is what the pacman-owned filter exists to prevent; the drop-in
    directory is where an admin's changes belong."""
    _w(tmp_path, "etc/ssh/sshd_config", "# the openssh default\n")

    assert "/etc/ssh/sshd_config" not in _files(tmp_path)


# --------------------------------------------------------------------------- #
#  /etc/samba/smb.conf
# --------------------------------------------------------------------------- #

_SMB = "[global]\n   server role = standalone server\n\n[shared]\n   path = /srv/shared\n"


def test_smb_conf_is_captured(tmp_path):
    _w(tmp_path, "etc/samba/smb.conf", _SMB)

    assert _files(tmp_path)["/etc/samba/smb.conf"] == _SMB


def test_the_samba_credential_stores_are_never_captured(tmp_path):
    """Whitelisted by exact path, so these are unreachable by construction
    rather than by a blacklist somebody has to keep complete."""
    _w(tmp_path, "etc/samba/smb.conf", _SMB)
    _w(tmp_path, "etc/samba/smbpasswd", "andres:1000:HASH:HASH:[U]:LCT-0:\n")
    _w(tmp_path, "etc/samba/secrets.tdb", "binary\n")
    _w(tmp_path, "etc/samba/passdb.tdb", "binary\n")
    _w(tmp_path, "etc/samba/private/secrets.tdb", "binary\n")

    captured = _files(tmp_path)

    assert list(captured) == ["/etc/samba/smb.conf"]


def test_an_absent_file_captures_nothing(tmp_path):
    (tmp_path / "etc").mkdir()

    assert _files(tmp_path) == {}


def test_a_package_owned_smb_conf_is_skipped(tmp_path):
    """Some distributions ship a default smb.conf. Capturing it would re-encode
    what the package already provides."""
    _w(tmp_path, "etc/samba/smb.conf", _SMB)
    global _OWNED
    _OWNED = _OWNED | {"/etc/samba/smb.conf"}
    try:
        assert "/etc/samba/smb.conf" not in _files(tmp_path)
    finally:
        _OWNED = {"/etc/ssh/sshd_config.d/99-vendor.conf"}


# --------------------------------------------------------------------------- #
#  interaction with what the config already says
# --------------------------------------------------------------------------- #

def test_a_declared_path_is_not_captured_twice(tmp_path):
    """The config declaring the path already wins; discovery must not append a
    second entry for it."""
    _w(tmp_path, "etc/samba/smb.conf", _SMB)
    config = {"files": [{"path": "/etc/samba/smb.conf", "content": "OLD"}]}

    captured = [p for p in _files(tmp_path, config)]

    assert captured == ["/etc/samba/smb.conf"]


def test_a_declared_path_is_refreshed_from_disk(tmp_path):
    """Same rule the other sections follow: a declared entry that exists on the
    target comes back with what the machine actually has."""
    _w(tmp_path, "etc/samba/smb.conf", _SMB)
    config = {"files": [{"path": "/etc/samba/smb.conf", "content": "OLD"}]}

    assert _files(tmp_path, config)["/etc/samba/smb.conf"] == _SMB


def test_discovery_is_off_without_a_target(tmp_path):
    """`import_state` on an action with no target reads no machine at all."""
    _w(tmp_path, "etc/samba/smb.conf", _SMB)
    action = DropFilesAction({}, None)

    assert action.import_state(managed=[])["files"] == []
