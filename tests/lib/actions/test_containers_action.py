"""ContainersAction — subuid/subgid convergence, and capturing the runtime back.

Everything else the block does (packages, units, the docker group,
/etc/docker/daemon.json) rides the existing domains through the expand toggle;
what has no other owner is the id map that makes rootless podman work, and the
capture that reads the whole block back off a machine.
"""
from unittest.mock import MagicMock, patch

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.containers_action import ContainersAction
from dasik.lib.expand import expand_config, subtract_contributions
from dasik.lib.target.target import Target


_USERS = [{"username": "andres", "hashed_password": "$6$a$b"},
          {"username": "root", "hashed_password": "$6$a$b"}]


def _machine(tmp_path, subuid="", podman=False, docker=False,
             compose=False, docker_bin=False, daemon_json=None, passwd=True):
    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    (tmp_path / "usr/bin").mkdir(parents=True, exist_ok=True)
    if passwd:
        (tmp_path / "etc/passwd").write_text(
            "root:x:0:0::/root:/bin/bash\n"
            "andres:x:1000:1000::/home/andres:/bin/bash\n")
    (tmp_path / "etc/subuid").write_text(subuid)
    (tmp_path / "etc/subgid").write_text(subuid)
    if podman:
        (tmp_path / "usr/bin/podman").write_text("")
    if docker:
        (tmp_path / "usr/bin/dockerd").write_text("")
    if docker_bin:
        (tmp_path / "usr/bin/docker").write_text("")
    if compose:
        (tmp_path / "usr/bin/podman-compose").write_text("")
    if daemon_json is not None:
        (tmp_path / "etc/docker").mkdir(parents=True, exist_ok=True)
        (tmp_path / "etc/docker/daemon.json").write_text(daemon_json)
    return tmp_path


def _action(root, containers=None, users=_USERS):
    config = {"users": users}
    if containers is not None:
        config["containers"] = containers
    return ContainersAction(config, ActionContext(target=Target(root=str(root))))


def _plan(root, containers=None, managed=(), **kw):
    return [(c.op.name, c.item) for c in
            _action(_machine(root, **kw), containers).plan(managed=list(managed))]


_PODMAN = {"runtime": "podman"}


# --- subuid / subgid -------------------------------------------------------- #

def test_a_user_without_an_id_map_is_planned(tmp_path):
    assert _plan(tmp_path, _PODMAN, podman=True) == [("CREATE", "andres")]


def test_a_user_that_already_has_one_plans_nothing(tmp_path):
    assert _plan(tmp_path, _PODMAN, podman=True,
                 subuid="andres:100000:65536\n") == []


def test_root_never_gets_an_id_map(tmp_path):
    """root is uid 0 in every namespace; a subuid range for it means nothing."""
    items = [c[1] for c in _plan(tmp_path, _PODMAN, podman=True)]
    assert "root" not in items


def test_docker_plans_no_id_maps(tmp_path):
    assert _plan(tmp_path, {"runtime": "docker"}, docker=True) == []


def test_rootless_off_plans_no_id_maps(tmp_path):
    assert _plan(tmp_path, {"runtime": "podman", "rootless": False},
                 podman=True) == []


def test_dropping_the_block_removes_the_map_dasik_owns(tmp_path):
    assert _plan(tmp_path, None, managed=["andres"],
                 subuid="andres:100000:65536\n") == [("REMOVE", "andres")]


def test_an_unowned_id_map_is_left_alone(tmp_path):
    assert _plan(tmp_path, None, subuid="someone:100000:65536\n") == []


# --- apply ------------------------------------------------------------------ #

def test_apply_writes_both_maps(tmp_path):
    root = _machine(tmp_path, podman=True)
    action = _action(root, _PODMAN)
    action.apply(action.plan(managed=[]))

    assert (root / "etc/subuid").read_text() == "andres:100000:65536\n"
    assert (root / "etc/subgid").read_text() == "andres:100000:65536\n"


def test_apply_does_not_collide_with_an_existing_range(tmp_path):
    """useradd hands the first user 100000-165535; the next range starts after
    whatever is already reserved, or two users map onto the same host uids."""
    root = _machine(tmp_path, podman=True, subuid="otro:100000:65536\n")
    action = _action(root, _PODMAN)
    action.apply(action.plan(managed=[]))

    assert (root / "etc/subuid").read_text() == \
        "otro:100000:65536\nandres:165536:65536\n"


def test_apply_is_a_no_op_when_useradd_already_made_the_entry(tmp_path):
    """The plan is computed before UsersAction runs, and modern shadow gives new
    users a range itself — so apply must re-check rather than duplicate it."""
    root = _machine(tmp_path, podman=True)
    action = _action(root, _PODMAN)
    changes = action.plan(managed=[])
    (root / "etc/subuid").write_text("andres:100000:65536\n")
    (root / "etc/subgid").write_text("andres:100000:65536\n")
    action.apply(changes)

    assert (root / "etc/subuid").read_text() == "andres:100000:65536\n"


def test_apply_removes_the_line_for_a_dropped_user(tmp_path):
    root = _machine(tmp_path, subuid="andres:100000:65536\notro:165536:65536\n")
    action = _action(root, None)
    action.apply(action.plan(managed=["andres"]))

    assert (root / "etc/subuid").read_text() == "otro:165536:65536\n"


# --- expansion --------------------------------------------------------------- #

def test_podman_installs_podman_and_enables_nothing():
    expanded = expand_config({"containers": _PODMAN, "users": _USERS})

    assert "podman" in expanded["packages"]
    assert expanded.get("systemd", {}).get("enable_units", []) == []


def test_docker_installs_docker_the_unit_and_the_group():
    expanded = expand_config({"containers": {"runtime": "docker"}, "users": _USERS})

    assert "docker" in expanded["packages"]
    assert "docker.service" in expanded["systemd"]["enable_units"]
    assert "docker" in expanded["users"][0]["groups"]


def test_the_api_socket_replaces_the_service():
    expanded = expand_config({"containers": {"runtime": "docker", "api_socket": True}})
    units = expanded["systemd"]["enable_units"]

    assert "docker.socket" in units
    assert "docker.service" not in units


def test_docker_compat_adds_podman_docker():
    expanded = expand_config({"containers": {"runtime": "podman",
                                             "docker_compat": True}})
    assert "podman-docker" in expanded["packages"]


def test_compose_follows_the_runtime():
    podman = expand_config({"containers": {"runtime": "podman", "compose": True}})
    docker = expand_config({"containers": {"runtime": "docker", "compose": True}})

    assert "podman-compose" in podman["packages"]
    assert "docker-compose" in docker["packages"]


def test_the_daemon_config_is_written_as_a_file():
    expanded = expand_config({"containers": {"runtime": "docker",
                                             "daemon_json": {"storage-driver": "btrfs"}}})
    entry = [f for f in expanded["files"] if f["path"] == "/etc/docker/daemon.json"]

    assert entry and '"storage-driver": "btrfs"' in entry[0]["content"]


# --- capture ----------------------------------------------------------------- #

def _captured(tmp_path, seed=None, **kw):
    action = _action(_machine(tmp_path, **kw), seed)
    with patch("dasik.lib.actions.containers_action.Command.execute",
               MagicMock(return_value=MagicMock(stdout=b"disabled\n", returncode=1))):
        return action.import_state([]).get("containers")


def test_sync_reports_podman(tmp_path):
    captured = _captured(tmp_path, podman=True, subuid="andres:100000:65536\n")

    assert captured["runtime"] == "podman"
    assert captured["rootless"] is True


def test_sync_reports_a_podman_without_id_maps_as_not_rootless(tmp_path):
    assert _captured(tmp_path, podman=True)["rootless"] is False


def test_sync_reports_docker_and_its_daemon_config(tmp_path):
    captured = _captured(tmp_path, docker=True, docker_bin=True,
                         daemon_json='{"storage-driver": "btrfs"}\n')

    assert captured["runtime"] == "docker"
    assert captured["daemon_json"] == {"storage-driver": "btrfs"}


def test_sync_reports_docker_compat_only_for_podman(tmp_path):
    captured = _captured(tmp_path, podman=True, docker_bin=True)

    assert captured["runtime"] == "podman"
    assert captured["docker_compat"] is True


def test_sync_invents_nothing_on_a_machine_with_no_runtime(tmp_path):
    assert _captured(tmp_path) is None


def test_sync_reports_the_enabled_socket(tmp_path):
    action = _action(_machine(tmp_path, podman=True), _PODMAN)
    with patch("dasik.lib.actions.containers_action.Command.execute",
               MagicMock(return_value=MagicMock(stdout=b"enabled\n", returncode=0))):
        captured = action.import_state([])["containers"]

    assert captured["api_socket"] is True


def test_the_captured_block_re_derives_the_same_config(tmp_path):
    captured = {"containers": _captured(tmp_path, podman=True, docker_bin=True,
                                        subuid="andres:100000:65536\n"),
                "users": _USERS}

    assert "podman-docker" in expand_config(captured)["packages"]


def test_the_derived_pieces_are_not_captured_as_hand_written_ones(tmp_path):
    config = {"containers": {"runtime": "docker",
                             "daemon_json": {"storage-driver": "btrfs"}},
              "users": _USERS}
    captured = subtract_contributions(expand_config(config), config)

    assert captured["files"] == []
    assert "docker" not in captured["packages"]
