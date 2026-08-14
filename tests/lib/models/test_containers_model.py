"""The `containers` block: the runtime, not the containers.

Two runtimes with different shapes — docker has a daemon and a group, podman has
neither and runs rootless — so a field that belongs to one fails closed on the
other rather than being silently ignored.
"""
import pytest
from pydantic import ValidationError

from dasik.lib.models.containers_model import ContainersModel


def test_the_runtime_is_required():
    with pytest.raises(ValidationError):
        ContainersModel()


def test_podman_defaults():
    c = ContainersModel(runtime="podman")
    assert (c.rootless, c.docker_compat, c.compose, c.api_socket) == \
        (True, False, False, False)


def test_docker_is_not_rootless_by_default():
    """Rootless docker is a different daemon setup (dockerd-rootless-setuptool),
    not a flag — so the field only means something for podman."""
    assert ContainersModel(runtime="docker").rootless is False


def test_docker_rejects_rootless():
    with pytest.raises(ValidationError, match="rootless"):
        ContainersModel(runtime="docker", rootless=True)


def test_docker_rejects_docker_compat():
    """podman-docker is podman pretending to be docker; with docker installed it
    would fight over /usr/bin/docker."""
    with pytest.raises(ValidationError, match="docker_compat"):
        ContainersModel(runtime="docker", docker_compat=True)


def test_podman_rejects_daemon_json():
    with pytest.raises(ValidationError, match="daemon_json"):
        ContainersModel(runtime="podman", daemon_json={"live-restore": True})


def test_docker_accepts_daemon_json():
    c = ContainersModel(runtime="docker", daemon_json={"storage-driver": "btrfs"})
    assert c.daemon_json == {"storage-driver": "btrfs"}


def test_unknown_runtime_is_refused():
    with pytest.raises(ValidationError):
        ContainersModel(runtime="containerd")


def test_json_model_accepts_the_block():
    from dasik.lib.models.json_model import JsonModel

    m = JsonModel(
        locales={"selected_locales": [], "desired_locale": "en_US.UTF-8",
                 "desired_tty_layout": "us"},
        timezone={"region": "Europe", "city": "Madrid"},
        network={"type": "NetworkManager", "add_default_hosts": True},
        hostname="arch",
        containers={"runtime": "podman", "docker_compat": True},
    )
    assert m.containers.runtime == "podman"
