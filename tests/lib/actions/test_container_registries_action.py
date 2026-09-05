"""ContainerRegistriesAction — the drop-in that makes a short image name resolve.

Arch configures NO registries for podman (ArchWiki, Podman#Registries), so
`postgres:17.5` in a compose file dies with

    short-name "postgres:17.5" did not resolve to an alias and no
    containers-registries.conf(5) was found

The wiki's answer is one drop-in, and that file is what this domain owns. Both
of this repo's machines had it written by hand, so it survived no reinstall and
no `sync` ever saw it — the same shape of hole as the libvirt autostart symlink.
"""
import os

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.container_registries_action import (
    ContainerRegistriesAction, DROP_IN,
)
from dasik.lib.actions.containers_action import ContainersAction
from dasik.lib.target.target import Target


_DOMAIN = "container_registries"


def _machine(tmp_path, registries=None, raw=None):
    """A target root, optionally already carrying the drop-in."""
    path = tmp_path / DROP_IN.lstrip("/")
    path.parent.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        path.write_text(raw)
    elif registries is not None:
        listed = ", ".join(f'"{r}"' for r in registries)
        path.write_text(f"unqualified-search-registries = [{listed}]\n")
    return tmp_path


def _action(root, containers=None):
    config = {}
    if containers is not None:
        config["containers"] = containers
    return ContainerRegistriesAction(
        config, ActionContext(target=Target(root=str(root))))


def _plan(root, containers=None, managed=()):
    return [(c.op.name, c.item)
            for c in _action(root, containers).plan(managed=list(managed))]


def _content(root):
    return (root / DROP_IN.lstrip("/")).read_text()


_DOCKER_IO = {"runtime": "podman", "search_registries": ["docker.io"]}


# --- plan ------------------------------------------------------------------- #


def test_a_declared_registry_is_planned_when_the_file_is_absent(tmp_path):
    assert _plan(_machine(tmp_path), _DOCKER_IO) == [("CREATE", "docker.io")]


def test_a_registry_already_in_the_file_plans_nothing(tmp_path):
    root = _machine(tmp_path, registries=["docker.io"])
    assert _plan(root, _DOCKER_IO) == []


def test_dropping_the_block_removes_the_registry_dasik_owns(tmp_path):
    """The reconciler hands an action its EMPTY config when a previous
    generation owned the domain — not the same thing as the empty value."""
    root = _machine(tmp_path, registries=["docker.io"])
    assert _plan(root, None, managed=["docker.io"]) == [("REMOVE", "docker.io")]


def test_dropping_only_the_field_removes_the_registry(tmp_path):
    root = _machine(tmp_path, registries=["docker.io"])
    assert _plan(root, {"runtime": "podman"}, managed=["docker.io"]) == \
        [("REMOVE", "docker.io")]


def test_a_hand_written_registry_is_left_alone(tmp_path):
    """Present, undeclared, unowned: drift, never a removal."""
    root = _machine(tmp_path, registries=["quay.io"])
    assert _plan(root, {"runtime": "podman"}) == []


def test_a_second_registry_is_planned_on_its_own(tmp_path):
    root = _machine(tmp_path, registries=["docker.io"])
    plan = _plan(root, {"runtime": "podman",
                        "search_registries": ["docker.io", "quay.io"]},
                 managed=["docker.io"])
    assert plan == [("CREATE", "quay.io")]


def test_docker_owns_no_registries(tmp_path):
    """dockerd never reads registries.conf; the model refuses the field there,
    and the domain stays out of a docker machine's plan."""
    root = _machine(tmp_path)
    assert _plan(root, {"runtime": "docker"}) == []


# --- apply ------------------------------------------------------------------ #


def test_apply_writes_the_drop_in_the_wiki_names(tmp_path):
    root = _machine(tmp_path)
    action = _action(root, _DOCKER_IO)
    action.apply(action.plan(managed=[]))
    assert _content(root).splitlines()[-1] == \
        'unqualified-search-registries = ["docker.io"]'


def test_apply_then_plan_is_silent(tmp_path):
    root = _machine(tmp_path)
    action = _action(root, _DOCKER_IO)
    action.apply(action.plan(managed=[]))
    assert _plan(root, _DOCKER_IO, managed=["docker.io"]) == []


def test_apply_writes_the_declared_search_order(tmp_path):
    """The list IS a search order: podman tries the registries in turn."""
    root = _machine(tmp_path)
    declared = {"runtime": "podman",
                "search_registries": ["registry.example", "docker.io"]}
    action = _action(root, declared)
    action.apply(action.plan(managed=[]))
    assert _action(root, declared).actual() == ["registry.example", "docker.io"]


def test_apply_keeps_a_registry_someone_else_added(tmp_path):
    root = _machine(tmp_path, registries=["quay.io"])
    action = _action(root, _DOCKER_IO)
    action.apply(action.plan(managed=[]))
    assert set(_action(root, _DOCKER_IO).actual()) == {"docker.io", "quay.io"}


def test_removing_the_last_owned_registry_takes_the_file_away(tmp_path):
    """An empty list in the file is not the same as no file: podman reads
    `unqualified-search-registries = []` as 'search nothing', which is the
    broken state this domain exists to fix."""
    root = _machine(tmp_path, registries=["docker.io"])
    action = _action(root, None)
    action.apply(action.plan(managed=["docker.io"]))
    assert not os.path.exists(root / DROP_IN.lstrip("/"))


def test_removing_one_registry_leaves_the_other(tmp_path):
    root = _machine(tmp_path, registries=["docker.io", "quay.io"])
    action = _action(root, None)
    action.apply(action.plan(managed=["docker.io"]))
    assert _action(root, None).actual() == ["quay.io"]


def test_a_commented_out_line_is_not_a_registry(tmp_path):
    root = _machine(tmp_path, raw='# unqualified-search-registries = ["quay.io"]\n')
    assert _action(root, None).actual() == []


# --- capture ---------------------------------------------------------------- #


def _captured(root, seed=None):
    config = {"users": []}
    if seed is not None:
        config["containers"] = seed
    action = ContainersAction(config,
                              ActionContext(target=Target(root=str(root))))
    return action.import_state([]).get("containers", {})


def _podman(root):
    (root / "usr/bin").mkdir(parents=True, exist_ok=True)
    (root / "usr/bin/podman").write_text("")
    (root / "etc").mkdir(parents=True, exist_ok=True)
    (root / "etc/subuid").write_text("")
    (root / "etc/subgid").write_text("")
    return root


def test_sync_reports_the_registries_the_machine_searches(tmp_path):
    root = _podman(_machine(tmp_path, registries=["docker.io"]))
    assert _captured(root)["search_registries"] == ["docker.io"]


def test_sync_invents_no_registries_when_the_file_is_absent(tmp_path):
    root = _podman(_machine(tmp_path))
    assert "search_registries" not in _captured(root)


def test_sync_clears_a_declared_list_the_machine_does_not_have(tmp_path):
    """sync reports reality: a config claiming docker.io on a machine whose
    drop-in was deleted must not keep claiming it."""
    root = _podman(_machine(tmp_path))
    captured = _captured(root, seed={"runtime": "podman",
                                     "search_registries": ["docker.io"]})
    assert captured.get("search_registries") == []


def test_the_captured_block_replans_to_nothing(tmp_path):
    root = _podman(_machine(tmp_path, registries=["docker.io"]))
    captured = _captured(root)
    assert _plan(root, captured, managed=["docker.io"]) == []
