
from ..exceptions.exceptions import CommandExecutionError, CommandNotFoundException
from ..logging import run_logger
from ..target.target import Target
from shutil import which
import os
import re
import subprocess

# Lines worth surfacing when a long streamed command fails. `yay`/`makepkg` keep
# printing (optional deps, hooks, "[installed]" lines) long after the real error,
# so the last 2000 characters are usually noise — on 2026-07-19 the user was
# shown "— file dialogs, screen sharing [installed]" while the log held
# "Packages failed to build: sunshine epson-inkjet-printer-escpr epsonscan2".
_ERROR_LINE = re.compile(
    r"(^|\s)(error|ERROR|Error|failed|FAILED|Failed|fatal|No such file|"
    r"not found|Permission denied|404|403)\b|^==> ERROR|^Packages failed",
)
_EXCERPT_LIMIT = 4000


def _failure_excerpt(output: str, limit: int = _EXCERPT_LIMIT) -> str:
    """The most likely cause of a failure, in output order.

    Error-looking lines first (deduped, order preserved); if none match, the tail.
    Bounded to *limit* characters so a runaway build log cannot flood the console.
    """
    lines = output.splitlines()
    picked: "list[str]" = []
    seen: set = set()
    for line in lines:
        stripped = line.strip()
        if stripped and _ERROR_LINE.search(stripped) and stripped not in seen:
            seen.add(stripped)
            picked.append(stripped)
    if not picked:
        picked = [ln for ln in lines[-20:] if ln.strip()]
    text = "\n".join(picked)
    if len(text) > limit:
        # keep both ends: the first cause and the final summary
        head, tail = text[: limit // 2], text[-limit // 2:]
        text = head + "\n…\n" + tail
    return text[:limit]




class Command:
    """Thin wrapper around subprocess.run with optional arch-chroot support."""

    @staticmethod
    def _locate_binary(name: str) -> str:
        path = which(name)
        if not path:
            raise CommandNotFoundException(f"Binary not found: {name}")
        return path

    @staticmethod
    def _locate_chroot() -> str:
        """arch-chroot, or a failure that says how to fix it.

        Every command against a target rooted anywhere but "/" runs inside
        arch-chroot, which ships in `arch-install-scripts` — present on the
        install ISO, usually absent on an installed system. The bare
        "Binary not found: arch-chroot" left day-2 users with no clue that
        `--target /` is the flag they wanted (the CLI gates on this up front;
        this covers callers that bypass it)."""
        path = which("arch-chroot")
        if not path:
            raise CommandNotFoundException(
                "Binary not found: arch-chroot — install it with "
                "`pacman -S arch-install-scripts`, or use --target / to manage "
                "the running system instead of a mounted install target."
            )
        return path

    @staticmethod
    def execute(cmd: str, args: list[str], run_as_chroot: bool = False,
                target: "Target | None" = None, input: "bytes | None" = None,
                env: "dict[str, str] | None" = None, check: bool = False,
                stream: bool = False, label: "str | None" = None):
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

        When *stream* is True the command runs under ``Popen`` and its output is
        echoed line by line as it arrives (console, ``--verbose`` only) — used for
        long installers (``pacman``, ``pacstrap``, ``makepkg``) that otherwise
        stay silent until they finish. stderr is **merged into stdout** to keep
        the temporal order intact, so the log file's stderr section is empty for a
        streamed command; the full output is still written to the file once. It is
        incompatible with *input* (``ValueError``).
        """
        if stream and input is not None:
            raise ValueError("stream=True does not support input=")

        chroot_cmd: list[str] = []
        if target is not None:
            if target.is_chroot:
                chroot_cmd = [Command._locate_chroot(), target.root]
        elif run_as_chroot:
            chroot_cmd = [Command._locate_chroot(), "/mnt"]

        argv = chroot_cmd + [cmd, *args]

        # env (if given) is merged over the current environment — arch-chroot
        # passes $PASSWORD etc. through with --keep-env-vars? No: arch-chroot
        # keeps a minimal env, so env vars are set on the arch-chroot process and
        # forwarded via the shell only for direct (non-chroot) runs. For chroot
        # runs the caller must rely on argv, not env — used here only for host-run
        # systemd-cryptenroll ($PASSWORD).
        full_env = {**os.environ, **env} if env else None

        logger = run_logger.get()

        if stream:
            return Command._run_streaming(cmd, argv, full_env, check, logger,
                                          label=label)

        result = subprocess.run(
            argv,
            input=input,
            env=full_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

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
                f"{label or cmd} failed (exit {rc}): {stderr.strip()[-2000:]}"
            )

        return result

    @staticmethod
    def _run_streaming(cmd, argv, full_env, check, logger, label=None):
        """Run *argv* under Popen, echoing each output line live and recording
        the full output to the log file once. Returns a ``CompletedProcess`` with
        the captured stdout (stderr merged into it), preserving execute()'s
        return contract."""
        proc = subprocess.Popen(
            argv,
            env=full_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        logger.stream_start(argv)
        chunks: list[bytes] = []
        if proc.stdout is not None:
            for raw in proc.stdout:
                chunks.append(raw)
                logger.stream_line(raw.decode("utf-8", errors="replace").rstrip("\n"))
        rc = proc.wait()
        buf = b"".join(chunks)

        # The file gets the whole block once; echoed=True suppresses the
        # duplicate console echo (already printed live above).
        logger.record(argv, rc, buf, b"", echoed=True)
        result = subprocess.CompletedProcess(argv, rc, stdout=buf, stderr=b"")

        if check and rc != 0:
            output = buf.decode("utf-8", errors="replace")
            excerpt = _failure_excerpt(output)
            # *label* is the LOGICAL command (e.g. "yay -S"); argv[0] is often a
            # wrapper (`su`, `arch-chroot`) whose name tells the user nothing.
            name = label or cmd
            log_hint = (f"\nFull output: {logger.log_path}"
                        if getattr(logger, "log_path", None) else "")
            logger.error(
                f"command failed (exit {rc}): {' '.join(argv)}",
                detail=excerpt,
            )
            raise CommandExecutionError(
                f"{name} failed (exit {rc}):\n{excerpt}{log_hint}")

        return result
