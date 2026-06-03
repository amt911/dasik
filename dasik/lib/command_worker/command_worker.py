
from ..exceptions.exceptions import CommandNotFoundException, CommandExecutionError
from ..target.target import Target
from shutil import which
import subprocess
import sys


class Command:
    """Thin wrapper around subprocess.run with optional arch-chroot support."""

    # When True, long destructive commands run through ``execute_checked`` stream
    # their output live instead of capturing it (so pacstrap/pacman/grub progress
    # is visible). Set by ``__main__`` from the ``-v/--verbose`` flag.
    verbose: bool = False

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
                        target: "Target | None" = None, capture: bool = False):
        """Like :meth:`execute`, but **raises** on a non-zero exit.

        For destructive must-succeed steps (pacstrap, pacman -S, partitioning,
        mkfs, mount, grub-install, bootctl, …). A failure here used to be
        swallowed and only surfaced much later as a confusing downstream error
        (e.g. a missing ``/mnt/etc/locale.gen`` after a silently-failed
        pacstrap). On failure the captured output is echoed and a
        ``CommandExecutionError`` is raised carrying the command + stderr.

        Output handling:
        - ``capture=True`` always PIPEs stdout (callers like ``genfstab`` read
          ``result.stdout``).
        - otherwise, when ``Command.verbose`` is set, the output streams live to
          the terminal (so a long pacstrap isn't a silent black screen); else it
          is captured (PIPE) so the error message can carry the real stderr.

        On success the completed process is returned.
        """
        chroot_cmd = Command._chroot_prefix(run_as_chroot, target)
        stream = Command.verbose and not capture
        kwargs = {} if stream else {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
        result = subprocess.run(chroot_cmd + [cmd, *args], **kwargs)
        if result.returncode != 0:
            stderr = result.stderr
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", "replace")
            stdout = result.stdout
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", "replace")
            # surface the real error instead of swallowing it (already streamed
            # in verbose mode, so only echo what we captured)
            if stdout and stdout.strip():
                print(stdout, file=sys.stderr)
            if stderr and stderr.strip():
                print(stderr, file=sys.stderr)
            detail = (stderr.strip() if stderr else "") or "(see output above)"
            raise CommandExecutionError(
                f"{cmd} {' '.join(args)} failed (rc={result.returncode}): {detail}"
            )
        return result
