from dasik.lib.state.change import Op
from dasik.lib.state.set_math import compute_changes


def test_first_apply_only_installs_no_remove():
    """M=∅ ⇒ REMOVE=∅; pre-existing undeclared items are DRIFT, untouched."""
    changes, drift = compute_changes(
        "packages", desired=["git", "htop"], managed=[], actual=["vim"]
    )
    ops = [(c.op, c.item) for c in changes]
    assert ops == [(Op.INSTALL, "git"), (Op.INSTALL, "htop")]
    assert drift == ["vim"]


def test_bootstrap_everything_is_drift():
    """D=∅, M=∅: nothing to install, nothing to remove, all A → drift."""
    changes, drift = compute_changes(
        "packages", desired=[], managed=[], actual=["git", "vim"]
    )
    assert changes == []
    assert drift == ["git", "vim"]


def test_pure_install():
    changes, drift = compute_changes(
        "packages", desired=["git"], managed=[], actual=[]
    )
    assert [(c.op, c.item) for c in changes] == [(Op.INSTALL, "git")]
    assert drift == []


def test_pure_remove_owned_no_longer_declared():
    changes, drift = compute_changes(
        "packages", desired=[], managed=["vim"], actual=["vim"]
    )
    assert len(changes) == 1
    c = changes[0]
    assert c.op == Op.REMOVE
    assert c.item == "vim"
    assert c.destructive is True
    assert c.reason == "no longer declared"
    assert drift == []


def test_already_converged_no_changes_no_drift():
    changes, drift = compute_changes(
        "packages", desired=["git"], managed=["git"], actual=["git"]
    )
    assert changes == []
    assert drift == []


def test_mixed_install_and_drift():
    """Declared + missing installs; manually-installed surfaces as drift."""
    changes, drift = compute_changes(
        "packages",
        desired=["git", "htop"],
        managed=["git"],
        actual=["git", "vim"],
    )
    ops = [(c.op, c.item) for c in changes]
    assert ops == [(Op.INSTALL, "htop")]
    assert drift == ["vim"]


def test_remove_only_targets_owned_items():
    """A \\ D \\ M = drift, NOT removal — primary safety property of the model."""
    changes, drift = compute_changes(
        "packages", desired=[], managed=[], actual=["user-installed"]
    )
    assert changes == []
    assert drift == ["user-installed"]


def test_custom_ops_for_domain():
    """Domains like systemd want ENABLE/DISABLE instead of INSTALL/REMOVE."""
    changes, drift = compute_changes(
        "systemd",
        desired=["NetworkManager.service"],
        managed=["sshd.service"],
        actual=["sshd.service"],
        op_install=Op.ENABLE,
        op_remove=Op.DISABLE,
    )
    ops = [(c.op, c.item) for c in changes]
    assert ops == [
        (Op.ENABLE, "NetworkManager.service"),
        (Op.DISABLE, "sshd.service"),
    ]
    assert drift == []
    assert changes[1].destructive is True  # DISABLE is destructive


def test_output_is_deterministic_sorted():
    """Changes and drift are sorted so renders/diffs are stable across runs."""
    changes, drift = compute_changes(
        "packages",
        desired=["zsh", "git", "htop"],
        managed=[],
        actual=["nano", "vim"],
    )
    install_items = [c.item for c in changes]
    assert install_items == sorted(install_items)
    assert drift == sorted(drift)


def test_accepts_any_iterable_not_just_lists():
    """Sets, tuples, generators all work — they're hashed internally."""
    changes, drift = compute_changes(
        "packages",
        desired={"git"},
        managed=("git",),
        actual=iter(["git", "vim"]),
    )
    assert changes == []
    assert drift == ["vim"]


def test_remove_fires_even_when_item_already_absent():
    """M\\D generates REMOVE regardless of A — action layer absorbs the no-op.

    Documents that an externally-removed managed item (e.g. user ran ``pacman -R``
    after dasik installed it) still produces a REMOVE Change. The action's
    ``apply()`` is responsible for tolerating a no-op removal.
    """
    changes, drift = compute_changes(
        "packages", desired=[], managed=["vim"], actual=[]
    )
    assert len(changes) == 1
    assert changes[0].op == Op.REMOVE
    assert changes[0].item == "vim"
    assert drift == []
