
from ..exceptions.exceptions import CommandExecutionError, CommandNotFoundException
from ..logging import run_logger
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
                env: "dict[str, str] | None" = None, check: bool = False):
        """Run *cmd* with *args*, optionally inside ``arch-chroot <root>``.

        Chroot root resolution:
        - if *target* is given it decides: ``target.is_chroot`` -> arch-chroot
          ``target.root``; otherwise (root="/") run directly on the host.
        - else if *run_as_chroot* is True, fall back to the legacy "/mnt"
          (preserves existing install-time callers that pass run_as_chroot=True).

        Every run is recorded to the process-wide :mod:`run_logger` (argv +
        stdout/stderr + exit code to the log file; echoed to the console under
        ``--verbose``). When *check* is True a non-zero exit is surfaced in red
        and raised as ``CommandExecutionError`` — mutating callers (``pacman -S``,
        ``dracut`` …) pass ``check=True`` so a failure can never masquerade as
        success. The default (``check=False``) preserves the historical contract:
        return the ``CompletedProcess`` and let the caller inspect ``returncode``
        (benign probes such as ``pacman -Qi <missing>`` rely on this).
        """
        chroot_cmd: list[str] = []
        if target is not None:
            if target.is_chroot:
                chroot_path = Command._locate_binary("arch-chroot")
                chroot_cmd = [chroot_path, target.root]
        elif run_as_chroot:
            chroot_path = Command._locate_binary("arch-chroot")
            chroot_cmd = [chroot_path, "/mnt"]

        argv = chroot_cmd + [cmd, *args]

        # env (if given) is merged over the current environment — arch-chroot
        # passes $PASSWORD etc. through with --keep-env-vars? No: arch-chroot
        # keeps a minimal env, so env vars are set on the arch-chroot process and
        # forwarded via the shell only for direct (non-chroot) runs. For chroot
        # runs the caller must rely on argv, not env — used here only for host-run
        # systemd-cryptenroll ($PASSWORD).
        full_env = {**os.environ, **env} if env else None
        result = subprocess.run(
            argv,
            input=input,
            env=full_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        logger = run_logger.get()
        logger.record(
            argv,
            getattr(result, "returncode", 0),
            getattr(result, "stdout", b""),
            getattr(result, "stderr", b""),
        )

        if check and getattr(result, "returncode", 0) != 0:
            stderr = getattr(result, "stderr", b"") or b""
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            rc = getattr(result, "returncode", "?")
            logger.error(
                f"command failed (exit {rc}): {' '.join(argv)}",
                detail=stderr.strip(),
            )
            raise CommandExecutionError(
                f"{cmd} failed (exit {rc}): {stderr.strip()[-2000:]}"
            )

        return result
