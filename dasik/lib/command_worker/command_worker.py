
from ..exceptions.exceptions import CommandNotFoundException
from ..target.target import Target
from shutil import which
import os
import subprocess


class Command:
    """Thin wrapper around subprocess.run with optional arch-chroot support."""

    @staticmethod
    def _locate_binary(name: str) -> str:
        path = which(name)
        if not path:
            raise CommandNotFoundException(f"Binary not found: {name}")
        return path

    @staticmethod
    def execute(cmd: str, args: list[str], run_as_chroot: bool = False,
                target: "Target | None" = None, input: "bytes | None" = None,
                env: "dict[str, str] | None" = None):
        """Run *cmd* with *args*, optionally inside ``arch-chroot <root>``.

        Chroot root resolution:
        - if *target* is given it decides: ``target.is_chroot`` -> arch-chroot
          ``target.root``; otherwise (root="/") run directly on the host.
        - else if *run_as_chroot* is True, fall back to the legacy "/mnt"
          (preserves existing install-time callers that pass run_as_chroot=True).
        """
        chroot_cmd: list[str] = []
        if target is not None:
            if target.is_chroot:
                chroot_path = Command._locate_binary("arch-chroot")
                chroot_cmd = [chroot_path, target.root]
        elif run_as_chroot:
            chroot_path = Command._locate_binary("arch-chroot")
            chroot_cmd = [chroot_path, "/mnt"]

        # env (if given) is merged over the current environment — arch-chroot
        # passes $PASSWORD etc. through with --keep-env-vars? No: arch-chroot
        # keeps a minimal env, so env vars are set on the arch-chroot process and
        # forwarded via the shell only for direct (non-chroot) runs. For chroot
        # runs the caller must rely on argv, not env — used here only for host-run
        # systemd-cryptenroll ($PASSWORD).
        full_env = {**os.environ, **env} if env else None
        return subprocess.run(
            chroot_cmd + [cmd, *args],
            input=input,
            env=full_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
