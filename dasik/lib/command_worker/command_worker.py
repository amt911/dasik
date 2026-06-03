
from ..exceptions.exceptions import CommandNotFoundException, CommandExecutionError
from ..target.target import Target
from shutil import which
import subprocess
import sys


class Command:
    """Thin wrapper around subprocess.run with optional arch-chroot support."""

    @staticmethod
    def _locate_binary(name: str) -> str:
        path = which(name)
        if not path:
            raise CommandNotFoundException(f"Binary not found: {name}")
        return path

    @staticmethod
    def _chroot_prefix(run_as_chroot: bool, target: "Target | None") -> list[str]:
        if target is not None:
            if target.is_chroot:
                return [Command._locate_binary("arch-chroot"), target.root]
            return []
        if run_as_chroot:
            return [Command._locate_binary("arch-chroot"), "/mnt"]
        return []

    @staticmethod
    def execute(cmd: str, args: list[str], run_as_chroot: bool = False,
                target: "Target | None" = None):
        """Run *cmd* with *args*, optionally inside ``arch-chroot <root>``.

        Captures stdout/stderr and does NOT check the return code — callers that
        treat a non-zero exit as a signal (e.g. ``pacman -Qi`` for an absent
        package, ``systemctl is-enabled``) rely on this. For must-succeed
        destructive steps use :meth:`execute_checked` instead.

        Chroot root resolution:
        - if *target* is given it decides: ``target.is_chroot`` -> arch-chroot
          ``target.root``; otherwise (root="/") run directly on the host.
        - else if *run_as_chroot* is True, fall back to the legacy "/mnt"
          (preserves existing install-time callers that pass run_as_chroot=True).
        """
        chroot_cmd = Command._chroot_prefix(run_as_chroot, target)
        return subprocess.run(
            chroot_cmd + [cmd, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    @staticmethod
    def execute_checked(cmd: str, args: list[str], run_as_chroot: bool = False,
                        target: "Target | None" = None):
        """Like :meth:`execute`, but **raises** on a non-zero exit.

        For destructive must-succeed steps (pacstrap, pacman -S, partitioning,
        mkfs, mount, grub-install, bootctl, …). A failure here used to be
        swallowed and only surfaced much later as a confusing downstream error
        (e.g. a missing ``/mnt/etc/locale.gen`` after a silently-failed
        pacstrap). On failure the captured output is echoed and a
        ``CommandExecutionError`` is raised carrying the command + stderr.

        On success the completed process is returned (``result.stdout`` is
        available for callers like ``genfstab``).
        """
        chroot_cmd = Command._chroot_prefix(run_as_chroot, target)
        result = subprocess.run(
            chroot_cmd + [cmd, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            stdout = result.stdout
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", "replace")
            stderr = result.stderr
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", "replace")
            # surface the real error instead of swallowing it
            if stdout.strip():
                print(stdout, file=sys.stderr)
            if stderr.strip():
                print(stderr, file=sys.stderr)
            raise CommandExecutionError(
                f"{cmd} {' '.join(args)} failed (rc={result.returncode}): "
                f"{stderr.strip() or '(no stderr)'}"
            )
        return result
