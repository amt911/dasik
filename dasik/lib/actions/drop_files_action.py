"""Action: write declarative files (udev rules, modprobe, profile.d, /etc/environment).

v3 domain "files": each entry is an explicit {name, content}; the on-disk
filename is the chosen name (stable identity). CREATE/DELETE by canonical path
(set-math) + MODIFY on content drift. actual() is scoped to declared paths that
exist (no directory glob). Registered config_key="__root__".
"""
from __future__ import annotations
import hashlib
import os
import re
from typing import Any, Dict, List, Optional
from .abstract_action import AbstractAction
from .initramfs.base import detect_encryption
from ..command_worker.command_worker import Command
from ..logging import run_logger
from ..state.change import Change, Op


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# (config key, target directory) for the per-file sections. These /etc/*.d dirs
# are the LOCAL admin layer (package defaults live under /usr/lib/*), so sync
# discovers user-created files here — modprobe options/blacklists, udev rules,
# modules-load lists, profile.d snippets.
_SECTIONS = [
    ("udev_rules", "/etc/udev/rules.d"),
    ("modprobe_conf", "/etc/modprobe.d"),
    ("modules_load", "/etc/modules-load.d"),
    ("sysctl_d", "/etc/sysctl.d"),
    ("tmpfiles_d", "/etc/tmpfiles.d"),
    ("sddm_conf_d", "/etc/sddm.conf.d"),
    ("profile_d", "/etc/profile.d"),
]
_ENV_PATH = "/etc/environment"
_CRYPTTAB_PATH = "/etc/crypttab"
_FILES_DOMAIN = "files"


class DropFilesAction(AbstractAction):
    """Write config snippets into /etc/... directories on the target."""

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        self._sections = {key: cfg.get(key, []) for key, _ in _SECTIONS}
        self.etc_env_lines: List[str] = cfg.get("etc_environment", [])
        self._etc_files: List[Any] = cfg.get("files", [])
        # When dracut is the generator and encryption is declared, DracutBackend
        # is the SOLE owner of /etc/crypttab (it composes the derived root entry +
        # captured non-root lines). DropFiles must yield it, or the two actions
        # rewrite the file on alternating applies (a non-idempotent oscillation).
        self._dracut_owns_crypttab = (
            cfg.get("initramfs") == "dracut" and detect_encryption(cfg)
        )

    @property
    def name(self) -> str:
        return "Drop Config Files"

    @property
    def is_optional(self) -> bool:
        return True

    # -- paths / desired state ----------------------------------------- #

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _abs(self, canonical: str) -> str:
        t = self._target()
        return t.path(canonical) if t is not None else "/mnt" + canonical

    @staticmethod
    def _entry_fields(entry: Any) -> tuple:
        """Accept a dict or a FileEntry-like object."""
        if isinstance(entry, dict):
            return entry["name"], entry["content"]
        return entry.name, entry.content

    @staticmethod
    def _path_fields(entry: Any) -> tuple:
        """Accept a dict or an EtcFile-like object -> (path, content, mode|None)."""
        if isinstance(entry, dict):
            return entry["path"], entry["content"], entry.get("mode")
        return entry.path, entry.content, getattr(entry, "mode", None)

    def _desired(self) -> Dict[str, str]:
        """Canonical absolute path -> verbatim content."""
        desired: Dict[str, str] = {}
        for key, directory in _SECTIONS:
            for entry in self._sections.get(key, []):
                name, content = self._entry_fields(entry)
                desired[f"{directory}/{name}"] = content
        if self.etc_env_lines:
            desired[_ENV_PATH] = "\n".join(self.etc_env_lines) + "\n"
        for entry in self._etc_files:
            path, content, _mode = self._path_fields(entry)
            if path == _CRYPTTAB_PATH and self._dracut_owns_crypttab:
                continue   # dracut composes/writes /etc/crypttab; yield ownership
            desired[path] = content
        return desired

    def _file_modes(self) -> "Dict[str, int]":
        """Canonical path -> numeric mode, for `files` entries that request one
        (e.g. 0600 for wireguard / NetworkManager keyfiles, which the tools refuse
        to load when world-readable)."""
        modes: Dict[str, int] = {}
        for entry in self._etc_files:
            path, _content, mode = self._path_fields(entry)
            if mode:
                modes[path] = int(mode, 8)
        return modes

    def _read(self, canonical: str) -> str:
        with open(self._abs(canonical), "r") as f:
            return f.read()

    def _exists(self, canonical: str) -> bool:
        return os.path.exists(self._abs(canonical))

    def actual(self) -> set:
        """Declared paths that exist on disk (no directory glob)."""
        if self._target() is None:
            return set()
        return {p for p in self._desired() if self._exists(p)}

    def _needs_write(self, canonical: str, desired: str) -> bool:
        if not self._exists(canonical):
            return True
        return _sha256(self._read(canonical)) != _sha256(desired)

    # -- v3 contract --------------------------------------------------- #

    def plan(self, managed):
        from ..state.set_math import compute_changes
        desired = self._desired()
        actual = self.actual()
        changes, _drift = compute_changes(
            _FILES_DOMAIN,
            desired=list(desired.keys()),
            managed=managed,
            actual=actual,
            op_install=Op.CREATE,
            op_remove=Op.DELETE,
        )
        for p in sorted(set(desired) & actual):
            if self._read(p) != desired[p]:
                changes.append(Change(_FILES_DOMAIN, Op.MODIFY, p, reason="content drift"))
        self._warn_shadowed([c.item for c in changes if c.op is not Op.DELETE])
        return changes

    def _warn_shadowed(self, paths: List[str]) -> None:
        """Warn for each declared file that overrides one a package ships.

        Overriding is a legitimate thing to declare (the fingerprint PAM
        snippets do exactly that), but pacman will then leave a `.pacnew` beside
        it on every upgrade and the override never picks the change up — which
        for a PAM file is how a machine stops accepting logins months later. So:
        say it, do not refuse it.
        """
        for path in paths:
            owner = self._pacman_owner(path)
            vendor = self._vendor_copy(path)
            if not owner and not vendor:
                continue
            source = f"the {owner} package" if owner else f"the vendor file {vendor}"
            run_logger.get().warning(
                f"{path} overrides {source}",
                detail="pacman will write a .pacnew next to it on upgrade; this "
                       "declaration will NOT pick those changes up. Intentional "
                       "for a deliberate override — check it after a big update.",
            )

    def _pacman_owner(self, path: str) -> Optional[str]:
        """`pacman -Qo <path>` package name, or None (unowned / probe failed)."""
        try:
            res = Command.execute("pacman", ["-Qo", path], target=self._target())
        except Exception:      # nosec B110 - a failed probe just means "unknown"
            return None
        if getattr(res, "returncode", 1) != 0:
            return None
        out = getattr(res, "stdout", b"") or b""
        if isinstance(out, bytes):
            out = out.decode("utf-8", errors="replace")
        # "<path> is owned by <pkg> <version>"
        marker = " is owned by "
        return out.split(marker)[1].split()[0] if marker in out else None

    def _effective_env_lines(self) -> "Optional[List[str]]":
        """The settings in /etc/environment, or None when it cannot be read.

        Comments and blank lines are not settings: an untouched Arch install
        ships a header of comments and nothing else, which must capture as
        nothing rather than as five comment lines.
        """
        try:
            text = self._read(_ENV_PATH)
        except OSError:
            return None
        return [ln for ln in text.splitlines()
                if ln.strip() and not ln.lstrip().startswith("#")]

    def _vendor_copy(self, path: str) -> Optional[str]:
        """The /usr/lib counterpart of an /etc file, when one exists.

        Arch ships PAM defaults in /usr/lib/pam.d (plasmalogin, polkit-1,
        kde-fingerprint): nothing under /etc owns them, so `pacman -Qo` finds
        nothing, yet an /etc/pam.d file shadows them completely.
        """
        if not path.startswith("/etc/"):
            return None
        vendor = "/usr/lib/" + path[len("/etc/"):]
        try:
            full = self._abs(vendor)
        except AttributeError:          # no usable target (unit-test doubles)
            return None
        return vendor if os.path.exists(full) else None

    def managed_keys(self) -> dict:
        return {_FILES_DOMAIN: sorted(self._desired().keys())}

    # -- discovery (sync from an empty seed) --------------------------- #

    def _pkg_owned(self, canonical: str) -> bool:
        """True if a pacman package owns <canonical> (a package default, not
        local admin config). Best-effort: no pacman / not owned -> False, so a
        genuinely local file is never dropped. The canonical path is passed
        verbatim — Command.execute handles the chroot, and pacman sees the same
        /etc/... path inside the target."""
        try:
            res = Command.execute("pacman", ["-Qo", canonical], target=self._target())
            return getattr(res, "returncode", 1) == 0
        except Exception:
            return False

    def _discover_section(self, directory: str) -> "List[dict]":
        """User-created files in <directory>: {name, content} for every regular
        file that is not a symlink and not owned by a package. Symlinks and
        package-owned files are the distro's own defaults — capturing them would
        re-encode what a package already provides."""
        base = self._abs(directory)
        out: List[dict] = []
        try:
            names = sorted(os.listdir(base))
        except OSError:
            return out
        for name in names:
            abs_p = os.path.join(base, name)
            if os.path.islink(abs_p) or not os.path.isfile(abs_p):
                continue
            if self._pkg_owned(f"{directory}/{name}"):
                continue
            try:
                with open(abs_p, "r") as f:
                    out.append({"name": name, "content": f.read()})
            except OSError:
                continue
        return out

    def _discover_wireguard(self) -> "List[dict]":
        """Every /etc/wireguard/*.conf as {path, content} (wg-quick interfaces).
        These are always local (wireguard-tools ships no confs there), so no
        pacman-owned filter. NOTE: a wg conf holds the interface PrivateKey — sync
        captures it verbatim (as the `wireguard` config block already does), so the
        secret lands in the JSON; keep synced configs private."""
        base = self._abs("/etc/wireguard")
        out: List[dict] = []
        try:
            names = sorted(os.listdir(base))
        except OSError:
            return out
        for name in names:
            if not name.endswith(".conf"):
                continue
            abs_p = os.path.join(base, name)
            if os.path.islink(abs_p) or not os.path.isfile(abs_p):
                continue
            try:
                with open(abs_p, "r") as f:
                    out.append({"path": f"/etc/wireguard/{name}", "content": f.read(),
                                "mode": "0600"})   # wg-quick refuses world-readable
            except OSError:
                continue
        return out

    def _discover_nm_wireguard(self) -> "List[dict]":
        """NetworkManager WireGuard connections
        (/etc/NetworkManager/system-connections/*.nmconnection with
        `type=wireguard`) as {path, content, mode}. Only wireguard-type keyfiles
        are captured — not wifi/ethernet. NOTE: these hold the interface
        PrivateKey (and PSKs) in cleartext — keep synced configs private. NM
        IGNORES a keyfile that isn't 0600, hence the mode."""
        base = self._abs("/etc/NetworkManager/system-connections")
        out: List[dict] = []
        try:
            names = sorted(os.listdir(base))
        except OSError:
            return out
        for name in names:
            if not name.endswith(".nmconnection"):
                continue
            abs_p = os.path.join(base, name)
            if os.path.islink(abs_p) or not os.path.isfile(abs_p):
                continue
            try:
                with open(abs_p, "r") as f:
                    content = f.read()
            except OSError:
                continue
            if re.search(r"^\s*type\s*=\s*wireguard\s*$", content, re.MULTILINE):
                out.append({
                    "path": f"/etc/NetworkManager/system-connections/{name}",
                    "content": content, "mode": "0600"})
        return out

    def _discover_crypttab(self) -> "Optional[str]":
        """The verbatim /etc/crypttab if it has any real (non-comment, non-blank)
        entry — e.g. an encrypted random-key swap that the disks/LUKS config does
        not otherwise describe. None when absent or only comments."""
        try:
            with open(self._abs(_CRYPTTAB_PATH), "r") as f:
                text = f.read()
        except OSError:
            return None
        real = [ln for ln in text.splitlines()
                if ln.strip() and not ln.lstrip().startswith("#")]
        return text if real else None

    def import_state(self, managed=None) -> dict:
        actual = self.actual()
        discover = self._target() is not None
        result: Dict[str, Any] = {}
        for key, directory in _SECTIONS:
            by_name: Dict[str, str] = {}
            order: List[str] = []
            # declared entries first (refresh manual edits) ...
            for entry in self._sections.get(key, []):
                name, content = self._entry_fields(entry)
                canonical = f"{directory}/{name}"
                if canonical in actual:
                    content = self._read(canonical)
                if name not in by_name:
                    order.append(name)
                by_name[name] = content
            # ... then discovered local files not already declared.
            if discover:
                for d in self._discover_section(directory):
                    if d["name"] not in by_name:
                        order.append(d["name"])
                        by_name[d["name"]] = d["content"]
            result[key] = [{"name": n, "content": by_name[n]} for n in order]

        # Read the file whenever it is THERE, not only when the config already
        # declared it. It belongs to the `pam` package, so file discovery skips
        # it on purpose, and the old `if _ENV_PATH in actual` (declared paths
        # that exist) meant a first capture of a machine with a customised
        # /etc/environment silently dropped every line in it.
        #
        # The stock file is nothing but comments, so "has effective lines" is
        # exactly the question "did somebody put something here".
        env_lines = self._effective_env_lines()
        if env_lines is not None:
            result["etc_environment"] = env_lines
        else:
            result["etc_environment"] = list(self.etc_env_lines)

        files_out = []
        seen_paths = set()
        for entry in self._etc_files:
            path, content, mode = self._path_fields(entry)
            if path in actual:
                content = self._read(path)
            out_entry: Dict[str, Any] = {"path": path, "content": content}
            if mode:
                out_entry["mode"] = mode
            files_out.append(out_entry)
            seen_paths.add(path)
        if discover:
            for wg in self._discover_wireguard() + self._discover_nm_wireguard():
                if wg["path"] not in seen_paths:
                    files_out.append(wg)
                    seen_paths.add(wg["path"])
            if _CRYPTTAB_PATH not in seen_paths:
                crypttab = self._discover_crypttab()
                if crypttab is not None:
                    files_out.append({"path": _CRYPTTAB_PATH, "content": crypttab})
        result["files"] = files_out
        return result

    def apply(self, changes) -> None:
        if self._target() is None:
            return
        desired = self._desired()
        modes = self._file_modes()
        writes = [c.item for c in changes if c.op in (Op.CREATE, Op.MODIFY)]
        deletes = [c.item for c in changes if c.op is Op.DELETE]

        for canonical in writes:                    # additive first
            path = self._abs(canonical)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(desired.get(canonical, ""))
            if canonical in modes:                  # restrict secret files (0600)
                os.chmod(path, modes[canonical])

        for canonical in deletes:
            path = self._abs(canonical)
            if os.path.exists(path):
                os.remove(path)

    # -- legacy is_needed / execute / verify (old executor path) ------- #

    def is_needed(self) -> bool:
        return any(self._needs_write(p, c) for p, c in self._desired().items())

    def execute(self) -> None:
        modes = self._file_modes()
        for canonical, content in self._desired().items():
            if self._needs_write(canonical, content):
                path = self._abs(canonical)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w") as f:
                    f.write(content)
                if canonical in modes:
                    os.chmod(path, modes[canonical])
                print(f"  Wrote {path}")

    def verify(self) -> bool:
        return not any(self._needs_write(p, c) for p, c in self._desired().items())
