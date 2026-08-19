"""Shared pytest fixtures for the dasik test suite."""
import pytest

from dasik.lib.target.target import Target


@pytest.fixture(autouse=True)
def _run_log_in_tmp(tmp_path, monkeypatch):
    """Keep the CLI's default run log out of the working tree.

    ``dasik <verb>`` writes ``./dasik-<verb>-<date>.log`` in the CURRENT
    directory, so every test that drives ``main()`` dropped a real (empty) file
    into the repo root — 348 of them had piled up by 2026-08-08. Point the
    default at the test's tmp_path instead; tests that assert on the naming
    scheme call ``_default_log_path`` directly and are unaffected.
    """
    import dasik.__main__ as main_module

    original = main_module._default_log_path        # keeps the naming scheme
    monkeypatch.setattr(
        main_module, "_default_log_path",
        lambda verb: tmp_path / original(verb).name,
    )


@pytest.fixture(autouse=True)
def _arch_chroot_present(monkeypatch):
    """Pretend `arch-chroot` is installed for every test that drives the CLI.

    The verbs gate on it (``target_check.check_target``) because a chroot target
    is unusable without it, but no test ever chroots — every command is mocked —
    and the dev/CI host is not an install ISO, so without this the whole CLI
    suite would fail on a missing binary. Tests that exercise the gate itself
    re-patch ``which`` and win, being set up after this autouse fixture.
    """
    monkeypatch.setattr("dasik.lib.target.target_check.which",
                        lambda name: f"/usr/bin/{name}")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """No unit test may touch the network — fail loudly instead of quietly.

    Found the hard way on 2026-08-18: the new AUR-closure gate runs inside
    ``PackagesAction.apply``, so every apply test whose fake resolution carried
    an AUR name started querying the real aurweb RPC (and the suite "passed",
    thirty seconds slower). ``PackageResolver`` binds its ``http_get`` at
    construction, so patching the module default here catches every resolver a
    test builds without injecting one. Tests that need the AUR inject their own
    ``http_get`` (or stub the resolver/validator), which this never intercepts.
    """
    def _refuse(url):
        raise AssertionError(
            f"unit test tried to reach the network: {url!r}. Inject an "
            f"http_get / stub the resolver instead."
        )

    monkeypatch.setattr(
        "dasik.lib.actions.package_resolver._default_http_get", _refuse)


@pytest.fixture
def tmp_target(tmp_path):
    """A Target rooted at a temporary directory.

    Because root != "/", Target.is_chroot is True, but the state/generation
    stores only do path mapping (no chroot commands run), so this gives an
    isolated on-disk root for filesystem tests.
    """
    return Target(root=str(tmp_path))


@pytest.fixture(autouse=True)
def _no_real_pacman(monkeypatch):
    """No unit test may reach the HOST's pacman — fail loudly instead.

    Found the hard way on 2026-08-19. `plan` started asking `pacman -Qqe`
    through `PackagesAction._explicit_raw` (the install-reason probe), and every
    test that stubbed `actual()` but not that one began querying the developer's
    machine. On Arch the suite stayed green — with the host's package list
    leaking into the assertions — and on CI, where there is no pacman at all, it
    died with `FileNotFoundError: 'pacman'`.

    A test that legitimately drives pacman mocks `Command.execute`, which never
    reaches `which`; only a real invocation gets here.
    """
    from dasik.lib.command_worker import command_worker

    real_which = command_worker.which

    def _refuse(name):
        if name in ("pacman", "yay", "paru"):
            raise AssertionError(
                f"unit test tried to run the host's {name!r}. Mock "
                f"Command.execute, or stub the probe that calls it "
                f"(_installed_all / actual / _explicit_raw)."
            )
        return real_which(name)

    monkeypatch.setattr(command_worker, "which", _refuse)
