"""AurInstaller — hybrid AUR install (helper path or own transitive resolution).

Never builds for real: every Command.execute is mocked and the module must use
NO raw subprocess. Tests cover the DECISION logic — dependency classification,
topological build order, --asdeps marking, helper delegation, safe argv, cleanup,
and the abort-before-build guarantees (unknown dep, cycle, AUR unavailable).
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from dasik.lib.actions.aur_installer import AurInstaller
from dasik.lib.actions.package_resolver import AurUnavailableError
from dasik.lib.exceptions.exceptions import CommandExecutionError, ConfigValidationError
from dasik.lib.target.target import Target


BUILD_ROOT = AurInstaller.BUILD_ROOT



@pytest.fixture(autouse=True)
def _quiet_run_logger(monkeypatch):
    """The leftover-sudoers warning logs through the process-wide run_logger;
    stub it so these tests never write to a capture stream another test already
    closed (it only shows up under the mutation gate's ordering)."""
    monkeypatch.setattr("dasik.lib.actions.aur_installer.run_logger.get",
                        lambda: MagicMock())

def _su_script_and_payload(args):
    """Return the fixed shell script and the values that become $1 onward.

    Models the real util-linux boundary: everything up to and including ``--``
    belongs to ``su``; ``sh`` is the child shell's $0; the rest is its argv.
    """
    command_index = args.index("-c")
    script = args[command_index + 1]
    assert args[command_index + 2:command_index + 4] == ["--", "sh"]
    return script, args[command_index + 4:]


def _srcinfo(name, depends=()):
    lines = [f"pkgname = {name}"]
    lines += [f"\tdepends = {d}" for d in depends]
    return "\n".join(lines) + "\n"


class _StubResolver:
    """Injected resolver: repo_names + aur_info, no network."""

    def __init__(self, repo=(), aur=(), unavailable=False):
        self._repo = set(repo)
        self._aur = set(aur)
        self._unavailable = unavailable
        self.aur_calls = []

    def repo_names(self, target):
        return set(self._repo)

    def aur_info(self, names):
        self.aur_calls.append(list(names))
        if self._unavailable:
            raise AurUnavailableError("network down")
        return {n for n in names if n in self._aur}


class _Harness:
    """Dispatches Command.execute by (cmd, args). `srcinfo` maps pkg -> deps."""

    def __init__(self, *, satisfied=(), sp_ok=(), installed=(), srcinfo=None,
                 user_exists=False):
        self.satisfied = set(satisfied)     # pacman -T rc 0
        self.sp_ok = set(sp_ok)             # pacman -Sp rc 0 (virtual/provides)
        self.installed = set(installed)     # pacman -Q rc 0
        self.srcinfo = srcinfo or {}        # pkg -> {deps}
        self.user_exists = user_exists
        self.runs = []                      # list of (cmd, args)

    # (cmd, args) -> CompletedProcess-like
    def command_execute(self, cmd, args=None, *a, **kw):
        args = list(args or [])
        self.runs.append((cmd, args))
        rc, out = 0, b""

        if cmd == "pacman":
            sub = args[0] if args else ""
            if sub == "-T":
                dep = args[1] if len(args) > 1 else ""
                rc = 0 if dep in self.satisfied else 127
            elif sub == "-Sp":
                name = args[-1]
                rc = 0 if name in self.sp_ok else 1
            elif sub == "-Q":
                name = args[1] if len(args) > 1 else ""
                rc = 0 if name in self.installed else 1
            # -S / -D -> rc 0
        elif cmd == "id":
            rc = 0 if self.user_exists else 1
        elif cmd == "su":
            rc, out = self._su(args)
        # useradd / userdel / rm -> rc 0
        return MagicMock(returncode=rc, stdout=out, stderr=b"")

    def _su(self, args):
        # ["-", user, "-c", script, "--", "sh", *tail]
        script, tail = _su_script_and_payload(args)
        if "--printsrcinfo" in script:
            pkg = self._pkg(tail[0]) if tail else ""
            deps = self.srcinfo.get(pkg, set())
            return 0, _srcinfo(pkg, deps).encode()
        if "makepkg -sri" in script:
            pkg = self._pkg(tail[0]) if tail else ""
            self.installed.add(pkg)          # built -> installed
            self.satisfied.add(pkg)
            return 0, b""
        if script == 'exec "$@"':
            # helper invocation: mark the requested package names installed
            helper = tail[0] if tail else ""
            for name in tail[1:]:
                if not name.startswith("-"):
                    self.installed.add(name)
            return 0, b""
        # git clone / other -> rc 0
        return 0, b""

    @staticmethod
    def _pkg(build_dir):
        return str(build_dir).rsplit("/", 1)[-1]

    def joined(self):
        return [f"{c} {' '.join(a)}" for c, a in self.runs]


def _install(pkgs, harness, resolver, exists=lambda p: "sudoers" in str(p),
             helper=None):
    inst = AurInstaller(Target(root="/"), resolver=resolver)
    with patch("dasik.lib.actions.aur_installer.Command.execute",
               side_effect=harness.command_execute), \
         patch("dasik.lib.actions.aur_installer.os.path.exists", side_effect=exists), \
         patch("dasik.lib.actions.aur_installer.os.remove"), \
         patch("builtins.open", MagicMock()):
        inst.install(pkgs, helper=helper)
    return inst


def _makepkgs(harness):
    """The package names built via `makepkg -sri`, in run order."""
    out = []
    for cmd, args in harness.runs:
        if cmd != "su":
            continue
        script, payload = _su_script_and_payload(args)
        if "makepkg -sri" in script:
            out.append(_Harness._pkg(payload[0]))
    return out


def _helper_payloads(harness):
    """The `$1..` payloads of every `exec "$@"` helper invocation, in run order."""
    out = []
    for cmd, args in harness.runs:
        if cmd != "su":
            continue
        script, payload = _su_script_and_payload(args)
        if script == 'exec "$@"':
            out.append(payload)
    return out


def _clones(harness):
    out = []
    for cmd, args in harness.runs:
        if cmd != "su":
            continue
        script, payload = _su_script_and_payload(args)
        if "git clone" in script:
            # git clone "$1" "$2" -> payload = [url, build_dir]
            out.append(_Harness._pkg(payload[1]))
    return out


# --- single package, no deps --------------------------------------------- #

def test_single_pkg_no_deps_builds_with_makepkg():
    h = _Harness(srcinfo={"hello": set()})
    r = _StubResolver(repo=[], aur=[])
    _install(["hello"], h, r)
    assert _makepkgs(h) == ["hello"]
    assert ("pacman", ["-Q", "hello"]) in h.runs   # verified installed


# --- transitive AUR dep: asunder -> gtk2 (both AUR) ----------------------- #

def test_aur_dep_built_before_dependent_and_marked_asdeps():
    h = _Harness(srcinfo={"asunder": {"gtk2"}, "gtk2": set()})
    r = _StubResolver(repo=[], aur=["gtk2"])
    _install(["asunder"], h, r)
    builds = _makepkgs(h)
    assert builds.index("gtk2") < builds.index("asunder")   # dep first
    assert ("pacman", ["-D", "--asdeps", "gtk2"]) in h.runs  # discovered dep
    # the declared package keeps explicit reason (no -D for it)
    assert ("pacman", ["-D", "--asdeps", "asunder"]) not in h.runs


def test_dep_satisfied_skipped():
    # gtk2 already satisfied -> never classified as AUR, never cloned/built
    h = _Harness(srcinfo={"asunder": {"gtk2"}}, satisfied=["gtk2"])
    r = _StubResolver(repo=[], aur=["gtk2"])
    _install(["asunder"], h, r)
    assert "gtk2" not in _clones(h)
    assert "gtk2" not in _makepkgs(h)


def test_repo_dep_left_to_makepkg():
    # gtk2 in the official repos -> makepkg -s covers it, no clone/build
    h = _Harness(srcinfo={"asunder": {"gtk2"}})
    r = _StubResolver(repo=["gtk2"], aur=[])
    _install(["asunder"], h, r)
    assert "gtk2" not in _clones(h)
    assert _makepkgs(h) == ["asunder"]


def test_virtual_dep_resolved_via_sp():
    # `sh` not in repo -Slq but pacman -Sp resolves it (provides) -> makepkg
    h = _Harness(srcinfo={"tool": {"sh"}}, sp_ok=["sh"])
    r = _StubResolver(repo=[], aur=[])
    _install(["tool"], h, r)
    assert "sh" not in _clones(h)
    assert _makepkgs(h) == ["tool"]


# --- abort-before-build guarantees --------------------------------------- #

def test_unknown_dep_aborts_before_any_build():
    h = _Harness(srcinfo={"asunder": {"ghost"}})
    r = _StubResolver(repo=[], aur=[])       # ghost not in repo nor AUR
    with pytest.raises(CommandExecutionError) as exc:
        _install(["asunder"], h, r)
    assert "ghost" in str(exc.value) and "asunder" in str(exc.value)
    assert _makepkgs(h) == []                # nothing built
    assert not any(c == "pacman" and a[:1] == ["-S"] for c, a in h.runs)


def test_dependency_cycle_raises():
    h = _Harness(srcinfo={"a": {"b"}, "b": {"a"}})
    r = _StubResolver(repo=[], aur=["a", "b"])
    with pytest.raises(CommandExecutionError, match="cycle"):
        _install(["a"], h, r)
    assert _makepkgs(h) == []


def test_aur_unavailable_aborts_retryable():
    h = _Harness(srcinfo={"asunder": {"gtk2"}})
    r = _StubResolver(repo=[], aur=[], unavailable=True)
    with pytest.raises(CommandExecutionError, match="retry"):
        _install(["asunder"], h, r)
    assert _makepkgs(h) == []


# --- helper path ---------------------------------------------------------- #

def test_helper_built_via_makepkg_then_rest_via_helper():
    h = _Harness(srcinfo={"yay": set(), "asunder": set()})
    r = _StubResolver(repo=[], aur=["yay", "asunder"])
    _install(["yay", "asunder"], h, r)
    # yay built from source; asunder installed via `yay -S`
    assert _makepkgs(h) == ["yay"]
    payloads = _helper_payloads(h)
    assert payloads, h.joined()
    tail = payloads[0]
    assert tail[0] == "yay" and "asunder" in tail
    # the helper's own flags must survive su's option parsing
    assert "-S" in tail


def test_helper_not_passed_to_itself():
    h = _Harness(srcinfo={"yay": set(), "asunder": set()})
    r = _StubResolver(repo=[], aur=["yay", "asunder"])
    _install(["yay", "asunder"], h, r)
    tail = _helper_payloads(h)[0]
    assert tail.count("yay") == 1        # yay is the helper, not in its own -S list


def test_only_helper_declared_skips_helper_invocation():
    h = _Harness(srcinfo={"yay": set()})
    r = _StubResolver(repo=[], aur=["yay"])
    _install(["yay"], h, r)
    assert _makepkgs(h) == ["yay"]
    assert _helper_payloads(h) == []


def test_helper_invocation_safe_argv():
    h = _Harness(srcinfo={"yay": set(), "asunder": set()})
    r = _StubResolver(repo=[], aur=["yay", "asunder"])
    _install(["yay", "asunder"], h, r)
    helper = [a for c, a in h.runs
              if c == "su" and _su_script_and_payload(a)[0] == 'exec "$@"'][0]
    script, payload = _su_script_and_payload(helper)
    # the script token is a fixed `exec "$@"`; package names are positional args
    assert script == 'exec "$@"'
    assert "asunder" in payload
    head = helper[:helper.index("-c") + 4]      # everything su itself consumes
    assert not any("asunder" in tok for tok in head)


def test_preinstalled_declared_helper_is_reused_without_rebuild():
    """Retry after a partial apply: yay is already installed and not in the delta,
    so it must be reused as the helper — never cloned or rebuilt."""
    harness = _Harness(installed=["yay"])
    resolver = _StubResolver(repo=[], aur=["asunder"])
    _install(["asunder"], harness, resolver, helper="yay")

    assert _makepkgs(harness) == []
    assert _clones(harness) == []
    assert _helper_payloads(harness) == [
        ["yay", "-S", "--noconfirm", "--needed", "asunder"]
    ]
    assert ("pacman", ["-Q", "yay"]) in harness.runs
    assert ("pacman", ["-Q", "asunder"]) in harness.runs


def test_selected_retry_helper_must_already_be_installed():
    """A declared helper that is neither in the delta nor installed aborts loudly
    instead of silently falling back to another strategy."""
    harness = _Harness(installed=[])
    resolver = _StubResolver(repo=[], aur=["asunder"])
    with pytest.raises(
        CommandExecutionError,
        match="declared AUR helper 'yay' is not installed",
    ):
        _install(["asunder"], harness, resolver, helper="yay")

    assert _helper_payloads(harness) == []


def test_unsupported_explicit_helper_rejected():
    harness = _Harness()
    resolver = _StubResolver(repo=[], aur=["asunder"])
    with pytest.raises(CommandExecutionError, match="Unsupported AUR helper"):
        _install(["asunder"], harness, resolver, helper="pacaur")


# --- cleanup / prerequisites --------------------------------------------- #

def test_cleanup_on_build_failure():
    h = _Harness(srcinfo={"asunder": set()})
    r = _StubResolver(repo=[], aur=[])
    inst = AurInstaller(Target(root="/"), resolver=r)
    removed = MagicMock()
    with patch("dasik.lib.actions.aur_installer.Command.execute",
               side_effect=h.command_execute), \
         patch("dasik.lib.actions.aur_installer.os.path.exists",
               side_effect=lambda p: "sudoers" in str(p)), \
         patch("dasik.lib.actions.aur_installer.os.remove", removed), \
         patch("builtins.open", MagicMock()), \
         patch.object(AurInstaller, "_build_one", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            inst.install(["asunder"])
    removed.assert_called_once()                              # sudoers removed
    assert any(c == "userdel" for c, a in h.runs)             # created user removed


def test_preexisting_build_user_not_deleted():
    h = _Harness(srcinfo={"asunder": set()}, user_exists=True)
    r = _StubResolver(repo=[], aur=[])
    _install(["asunder"], h, r)
    assert not any(c == "userdel" for c, a in h.runs)
    assert not any(c == "useradd" for c, a in h.runs)


def test_prereq_pacman_failure_raises():
    h = _Harness(srcinfo={"asunder": set()})
    r = _StubResolver(repo=[], aur=[])

    def boom(cmd, args=None, *a, **kw):
        if cmd == "pacman" and args and args[0] == "--noconfirm" and "base-devel" in args:
            raise CommandExecutionError("pacman base-devel failed")
        return h.command_execute(cmd, args, *a, **kw)

    inst = AurInstaller(Target(root="/"), resolver=r)
    with patch("dasik.lib.actions.aur_installer.Command.execute", side_effect=boom), \
         patch("dasik.lib.actions.aur_installer.os.path.exists", return_value=False), \
         patch("dasik.lib.actions.aur_installer.os.remove"), \
         patch("builtins.open", MagicMock()):
        with pytest.raises(CommandExecutionError):
            inst.install(["asunder"])


# --- security ------------------------------------------------------------- #

def test_malicious_dep_name_rejected_before_argv():
    evil = "foo;rm -rf /"
    h = _Harness(srcinfo={"asunder": {evil}})
    r = _StubResolver(repo=[], aur=[])
    with pytest.raises((ConfigValidationError, CommandExecutionError)):
        _install(["asunder"], h, r)
    # the malicious name never reached a clone or any argv
    assert not any(evil in " ".join(a) for c, a in h.runs)
    assert "foo" not in _clones(h)


def test_no_raw_subprocess_used():
    import dasik.lib.actions.aur_installer as mod
    # the module must not shell out with raw subprocess — everything via Command
    assert getattr(mod, "subprocess", None) is None


def test_long_commands_stream():
    # clone + makepkg carry stream=True (live output for long builds)
    h = _Harness(srcinfo={"hello": set()})
    r = _StubResolver(repo=[], aur=[])
    calls = []
    inst = AurInstaller(Target(root="/"), resolver=r)

    def rec(cmd, args=None, *a, **kw):
        calls.append((cmd, list(args or []), kw.get("stream", False)))
        return h.command_execute(cmd, args, *a, **kw)

    with patch("dasik.lib.actions.aur_installer.Command.execute", side_effect=rec), \
         patch("dasik.lib.actions.aur_installer.os.path.exists",
               side_effect=lambda p: "sudoers" in str(p)), \
         patch("dasik.lib.actions.aur_installer.os.remove"), \
         patch("builtins.open", MagicMock()):
        inst.install(["hello"])

    su_streamed = [c for c in calls if c[0] == "su" and c[2] is True]
    scripts = [c[1][3] for c in su_streamed]
    assert any("git clone" in s for s in scripts)
    assert any("makepkg -sri" in s for s in scripts)


# --- failure labelling (F-26) --------------------------------------------- #

def test_helper_and_makepkg_runs_are_labelled_with_the_logical_command():
    """Every build shells out through `su`, so without a label the user is told
    "su failed (exit 1)" and has to guess which package/command broke."""
    from unittest.mock import patch as _patch
    inst = AurInstaller(Target(root="/mnt"))
    with _patch.object(AurInstaller, "_run") as run:
        run.return_value = SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        inst._build_one("sunshine")
        inst._install_with_helper("yay", ["yay", "sunshine"])
    labels = [c.kwargs.get("label") for c in run.call_args_list
              if c.kwargs.get("stream")]
    assert any(lbl and "makepkg" in lbl and "sunshine" in lbl for lbl in labels)
    assert any(lbl and lbl.startswith("yay -S") for lbl in labels)
