"""Model for the `containers` block — the container RUNTIME.

dasik installs and configures docker or podman; it does not manage containers.
The two runtimes are shaped differently and the model refuses to blur that:
docker has a daemon (`daemon_json`) and a group whose members are effectively
root, podman has neither and its whole point is running rootless.
"""
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class ContainersModel(BaseModel):
    """A container runtime, installed and configured. No containers."""

    runtime: Literal["podman", "docker"] = Field(
        ...,
        description="Which engine to install. Exactly one — both own "
                    "/usr/bin/docker and the same bridge networks.",
    )
    rootless: bool = Field(
        default=True,
        description="podman only: give every declared user a subuid/subgid "
                    "range, which is what rootless containers map into. "
                    "Ignored for docker, where rootless is a different daemon "
                    "setup entirely.",
    )
    docker_compat: bool = Field(
        default=False,
        description="podman only: install podman-docker, so `docker` on the "
                    "command line is podman.",
    )
    compose: bool = Field(
        default=False,
        description="Install the compose implementation for the chosen runtime "
                    "(docker-compose / podman-compose).",
    )
    api_socket: bool = Field(
        default=False,
        description="Enable the socket unit (docker.socket / podman.socket) "
                    "instead of, for docker, the always-on docker.service. "
                    "Socket activation starts the engine on first use.",
    )
    daemon_json: Optional[Dict[str, Any]] = Field(
        default=None,
        description="docker only: the contents of /etc/docker/daemon.json "
                    "(storage-driver, log-driver, …). Written verbatim as JSON.",
    )

    @model_validator(mode="after")
    def _fields_belong_to_one_runtime(self) -> "ContainersModel":
        """A field of the other engine is refused, never ignored.

        Silently dropping `daemon_json` under podman would leave a config that
        describes a storage driver nobody applies — and it would keep describing
        it after every `plan` said "no changes".
        """
        if self.runtime == "docker":
            wrong = [name for name, on in
                     (("rootless", self.rootless),
                      ("docker_compat", self.docker_compat)) if on]
            if wrong:
                raise ValueError(
                    f"containers.{', '.join(wrong)} is a podman field; with "
                    "runtime 'docker' it would be ignored. Rootless docker is a "
                    "separate daemon setup (dockerd-rootless-setuptool), not a "
                    "flag, and podman-docker would fight docker over "
                    "/usr/bin/docker."
                )
        elif self.daemon_json is not None:
            raise ValueError(
                "containers.daemon_json is a docker field: podman has no daemon "
                "and never reads /etc/docker/daemon.json."
            )
        return self

    @model_validator(mode="before")
    @classmethod
    def _docker_is_rooted_by_default(cls, data: Any) -> Any:
        """`rootless` defaults to true, which is only right for podman."""
        if isinstance(data, dict) and data.get("runtime") == "docker" \
                and "rootless" not in data:
            data = {**data, "rootless": False}
        return data
