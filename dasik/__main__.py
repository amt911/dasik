"""dasik CLI entry point.

Verbs (slice 1 of declarative-convergence):
  * ``plan <config> [--target / | /mnt]`` — show the diff between config and
    system reality. **Read-only; safe to run on any host.**
  * ``apply <config> [--target / | /mnt] [--yes]`` — converge the system to
    the config (DESTRUCTIVE). Prompts before destructive changes unless
    ``--yes`` is passed.
  * ``sync <config> [--target /]`` — capture system reality back into the
    config file (non-destructive to the system; rewrites the config).
  * ``generations [--target /]`` — list recorded generations, marking the
    current one. **Read-only.**
  * ``rollback [N] [--target /] [--yes]`` — restore generation N's config and
    re-apply it (DESTRUCTIVE; defaults N to the generation before current).
  * ``check <config>`` — validate the config (JSON syntax + schema) without
    touching the system. **Read-only.**

The old no-verb ``dasik <config>`` form (the legacy ``ActionsHandler`` install
path) has been REMOVED — it is now rejected with a message pointing at
``dasik apply`` / ``dasik plan``.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from dasik.lib.actions.actions_handler_v2 import setup_actions
from dasik.lib.actions.action_registry import get_default_registry
from dasik.lib.logging import run_logger
from dasik.lib.reconciler.reconciler import Reconciler
from dasik.lib.state.config_writer import ConfigWriter
from dasik.lib.state.generation_store import GenerationStore
from dasik.lib.state.state_store import StateStore
from dasik.lib.target.target import Target
from dasik.lib.target.target_check import check_target
from dasik.lib.expand import expand_config, subtract_contributions
from dasik.lib.exceptions.exceptions import PasswordHashError
from dasik.lib.passwords import SHA512, YESCRYPT, hash_password


# Verbs that shell out to real commands and therefore benefit from an install
# log. Read-only/trivial verbs (check, hash-password) write no log by default.
_LOGGED_VERBS = {"plan", "apply", "sync", "rollback", "generations"}


def _default_log_path(verb: str) -> Path:
    """``./dasik-<verb>-<YYYYmmdd-HHMMSS>.log`` in the current directory."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path.cwd() / f"dasik-{verb}-{stamp}.log"


def _configure_logging(args: argparse.Namespace) -> None:
    """Install the process-wide RunLogger from parsed CLI args.

    ``--no-log`` disables the file; ``--log PATH`` overrides the location;
    otherwise a logged verb gets ``./dasik-<verb>-<date>.log``. ``--verbose``
    additionally echoes the live command stream to the console.
    """
    verbose = bool(getattr(args, "verbose", False))
    verb = getattr(args, "verb", None) or "dasik"

    if getattr(args, "no_log", False):
        log_path: Optional[Path] = None
    elif getattr(args, "log", None):
        log_path = Path(args.log)
    elif verb in _LOGGED_VERBS:
        log_path = _default_log_path(verb)
    else:
        log_path = None

    run_logger.configure(log_path=log_path, verbose=verbose)


def _validate_config_file(config_path: str) -> Optional[Path]:
    """Return the Path if valid, else print error to stderr and return None."""
    path = Path(config_path)
    if not path.exists():
        print(f"Error: Configuration file '{config_path}' does not exist.",
              file=sys.stderr)
        return None
    if not path.is_file():
        print(f"Error: '{config_path}' is not a file.", file=sys.stderr)
        return None
    if path.suffix != ".json":
        print(f"Warning: '{config_path}' does not have .json extension.",
              file=sys.stderr)
    return path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dasik",
        description="Declarative Arch Linux installer / configuration manager",
    )
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Echo the live command stream and show errors in red")
    parser.add_argument("--log", default=None,
                        help="Write the run log to this path (default: "
                             "./dasik-<verb>-<date>.log for command verbs)")
    parser.add_argument("--no-log", action="store_true",
                        help="Do not write a run log file")

    # Shared parent so --verbose/--log/--no-log also work AFTER the verb
    # (dasik apply config.json -v). SUPPRESS defaults keep a pre-verb -v from
    # being clobbered by the subparser copy when it is omitted post-verb.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true",
                        default=argparse.SUPPRESS)
    common.add_argument("--log", default=argparse.SUPPRESS)
    common.add_argument("--no-log", action="store_true",
                        default=argparse.SUPPRESS)

    sub = parser.add_subparsers(dest="verb")

    plan_p = sub.add_parser(
        "plan",
        parents=[common],
        help="Show what would change to converge the system to the config",
    )
    plan_p.add_argument("config", help="Path to the JSON configuration file")
    plan_p.add_argument(
        "--target",
        default="/mnt",
        help="Root commands run against (/ for the live host, /mnt for an "
             "install target). Default: /mnt.",
    )
    apply_p = sub.add_parser(
        "apply",
        parents=[common],
        help="Converge the system to the config (DESTRUCTIVE)",
    )
    apply_p.add_argument("config", help="Path to the JSON configuration file")
    apply_p.add_argument(
        "--target",
        default="/mnt",
        help="Root commands run against (/ for the live host, /mnt for an "
             "install target). Default: /mnt.",
    )
    apply_p.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip the destructive-change confirmation prompt.",
    )

    sync_p = sub.add_parser(
        "sync",
        parents=[common],
        help="Capture system reality back into the config file (non-destructive)",
    )
    sync_p.add_argument("config", help="Path to the JSON configuration file")
    sync_p.add_argument(
        "--target",
        default="/",
        help="Root to read reality from (/ for the live host, /mnt for an "
             "install target). Default: /.",
    )

    gens_p = sub.add_parser(
        "generations",
        parents=[common],
        help="List recorded generations",
    )
    gens_p.add_argument(
        "--target",
        default="/",
        help="Root whose generations to list. Default: /.",
    )

    rollback_p = sub.add_parser(
        "rollback",
        parents=[common],
        help="Restore a generation's config and re-apply it (DESTRUCTIVE)",
    )
    rollback_p.add_argument(
        "generation",
        nargs="?",
        type=int,
        default=None,
        help="Generation number to roll back to. Default: the generation "
             "before the current one.",
    )
    rollback_p.add_argument(
        "--target",
        default="/",
        help="Root to converge (day-2 host management). Default: / "
             "(unlike apply, which defaults to /mnt).",
    )
    rollback_p.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip the destructive-change confirmation prompt.",
    )

    check_p = sub.add_parser(
        "check",
        parents=[common],
        help="Validate a config file (JSON syntax + schema) without touching the "
             "system. Read-only; no --target.",
    )
    check_p.add_argument("config", help="Path to the JSON configuration file")

    hash_p = sub.add_parser(
        "hash-password",
        parents=[common],   # -v/--log/--no-log, like every other verb
        help="Prompt for a password (twice) and print its crypt hash for use "
             "as a user's hashed_password.",
    )
    hash_p.add_argument(
        "--method",
        choices=[YESCRYPT, SHA512],
        default=YESCRYPT,
        help=f"Hash format. Default: {YESCRYPT} — what Arch's own passwd writes "
             f"(login.defs ENCRYPT_METHOD) and what sync captures back. "
             f"{SHA512} is the older sha512crypt ($6$).",
    )

    return parser


_KNOWN_VERBS = {"plan", "apply", "sync", "generations", "rollback", "check",
                "hash-password"}


def _is_legacy_invocation(raw: list[str]) -> Optional[str]:
    """If argv matches the deprecated ``dasik <config>`` form, return the
    config path. Otherwise return None and let argparse handle it.

    A first argument that is not a verb but does not look like a config file
    either — `dasik aply cfg.json` — is a typo, not the legacy form. Answering
    it with "use `dasik plan aply`" is advice about a file that does not exist,
    so those go to argparse, which says `invalid choice` and lists the verbs.
    """
    non_flags = [a for a in raw if not a.startswith("-")]
    if not non_flags or non_flags[0] in _KNOWN_VERBS:
        return None
    first = non_flags[0]
    looks_like_a_config = first.endswith(".json") or os.path.sep in first \
        or os.path.exists(first)
    return first if looks_like_a_config else None


def _load_validated_config(config_path: Path) -> Optional[dict]:
    """Read *config_path* and validate it against the pydantic schema.

    Returns the raw config dict, or None after printing the reason. Every verb
    that can reach a mutating action goes through here: `dasik check` used to be
    the only place `JsonModel.model_validate()` ran, so skipping it meant a
    syntactically-valid-but-wrong JSON reached the disk actions.
    """
    try:
        config = json.loads(config_path.read_text())
    except Exception as e:
        print(f"Error loading config: {e}", file=sys.stderr)
        return None

    # A config may be assembled from fragments ($include / $include_text /
    # $concat), resolved relative to the file that names them, BEFORE the schema
    # sees it — the model validates the finished config, not the split.
    from dasik.lib.json_parser.includes import ConfigIncludeError, resolve_includes
    try:
        config = resolve_includes(config, config_path.parent)
    except ConfigIncludeError as e:
        print(f"Error in {config_path}: {e}", file=sys.stderr)
        return None

    from pydantic import ValidationError
    from dasik.lib.models.json_model import JsonModel
    try:
        JsonModel.model_validate(config)
    except ValidationError as e:
        print(f"Config {config_path} is invalid:\n{e}", file=sys.stderr)
        return None
    return config


def _preflight_or_none(config: dict) -> Optional[dict]:
    """Run the cross-field checks on the EXPANDED *config*.

    Returns the config when it is coherent (warnings are printed and do not
    block), None after printing the errors — an error here is a deterministic
    failure later (missing group, unit no package provides, destructive crypttab
    entry), and it must be caught before the first partition is touched.
    """
    from dasik.lib.validation.preflight import has_errors, preflight, render
    issues = preflight(config)
    if has_errors(issues):
        print("Config is not coherent — refusing to continue:\n" + render(issues),
              file=sys.stderr)
        return None
    if issues:
        print("Preflight warnings:\n" + render(issues))
    return config


def _target_or_none(target_root: str) -> Optional[Target]:
    """The Target for *target_root*, or None (message printed) if unusable.

    Runs before any action so a run that cannot possibly work — a chroot target
    on a host without ``arch-chroot`` — fails immediately and says what to do,
    instead of dying mid-probe on "Binary not found: arch-chroot".
    """
    target = Target(root=target_root)
    problem = check_target(target)
    if problem is not None:
        print(f"Error: {problem}", file=sys.stderr)
        return None
    return target


def _cmd_plan(config_path: Path, target_root: str) -> int:
    """Run the read-only plan flow."""
    target = _target_or_none(target_root)
    if target is None:
        return 1
    config = _load_validated_config(config_path)
    if config is None:
        return 1

    config = expand_config(config)
    if _preflight_or_none(config) is None:
        return 1
    setup_actions()
    registry = get_default_registry()

    # The SAME manifest apply loads. Ownership is what makes a REMOVE: the
    # set-math is M \ D over the manifest, so planning with `manifest=None`
    # left M empty and no removal could ever show up in the dry run — while
    # apply, reading the real one, would carry them out unannounced. A plan
    # that cannot say "this will be removed" is not a dry run.
    manifest_dict = StateStore(target).load().to_dict()

    reconciler = Reconciler(
        config=config,
        target=target,
        manifest=manifest_dict,
        action_metas=registry.get_all_actions(),
    )
    plan, _results = reconciler.build_plan()
    print(plan.render())
    return 0


def _cmd_apply(config_path: Path, target_root: str, assume_yes: bool) -> int:
    """Run the destructive convergence flow."""
    target = _target_or_none(target_root)
    if target is None:
        return 1
    config = _load_validated_config(config_path)
    if config is None:
        return 1

    config = expand_config(config)
    if _preflight_or_none(config) is None:
        return 1
    setup_actions()
    registry = get_default_registry()
    state_store = StateStore(target)
    gen_store = GenerationStore(target)

    manifest_dict = state_store.load().to_dict()

    reconciler = Reconciler(
        config=config,
        target=target,
        manifest=manifest_dict,
        action_metas=registry.get_all_actions(),
        state_store=state_store,
        generation_store=gen_store,
    )
    plan, results = reconciler.build_plan()
    print(plan.render())

    if plan.is_empty():
        return 0

    try:
        new_manifest = reconciler.apply(plan, results, assume_yes=assume_yes)
    except Exception as e:
        # The reconciler already persisted what completed as a PARTIAL
        # generation; say so, because the system HAS been mutated and the next
        # plan will resume from that reality rather than from scratch.
        print(f"error: apply failed: {e}", file=sys.stderr)
        print("The progress made so far was recorded as a partial generation "
              "(see `dasik generations`); it is not a convergence. Fix the "
              "cause and run `dasik apply` again — completed work is not redone.",
              file=sys.stderr)
        return 1
    if new_manifest is None:
        print("Aborted: no changes applied.", file=sys.stderr)
        return 1

    print(f"Applied: now at generation {new_manifest.generation}.")
    return 0


def _cmd_sync(config_path: Path, target_root: str) -> int:
    """Capture system reality back into the config file (spec §4 sync flow)."""
    target = _target_or_none(target_root)
    if target is None:
        return 1
    try:
        raw_text = config_path.read_text()
    except Exception as e:
        print(f"Error loading config: {e}", file=sys.stderr)
        return 1
    # Schema-validate the seed too: sync REWRITES this file, so starting from a
    # config pydantic would reject would silently launder it into a new one.
    # No preflight here — sync's job is to repair a config from reality.
    config = _load_validated_config(config_path)
    if config is None:
        return 1

    # …and for the same reason it must not flatten a config that was split
    # across files: ConfigWriter emits one document, so every $include would be
    # replaced by the resolved value and the split silently undone.
    from dasik.lib.json_parser.includes import uses_includes
    if uses_includes(json.loads(raw_text)):
        print(
            f"{config_path} is assembled from includes ($include/$include_text/"
            "$concat) and sync would flatten it into a single file.\n"
            "Sync into a scratch copy instead and fold the changes back by hand:\n"
            f"  cp {config_path} /tmp/synced.json && dasik sync /tmp/synced.json",
            file=sys.stderr)
        return 1

    setup_actions()
    registry = get_default_registry()
    state_store = StateStore(target)
    manifest_dict = state_store.load().to_dict()

    reconciler = Reconciler(
        config=config,
        target=target,
        manifest=manifest_dict,
        action_metas=registry.get_all_actions(),
        state_store=state_store,
    )
    new_config, new_manifest = reconciler.sync()
    new_config = subtract_contributions(new_config, config)

    if new_manifest is None:
        print("Nothing to sync (no convergence-aware actions registered).")
        return 0

    # A freshly-bootstrapped domain that captured nothing (empty) is not a
    # meaningful change: drop newly-added empty keys so sync doesn't rewrite
    # the file just to add e.g. "packages": [] on a config that omitted it.
    new_config = {k: v for k, v in new_config.items() if k in config or v}
    if new_config == config:
        print("Config already matches system reality - nothing to sync.")
        return 0

    backup = config_path.with_suffix(config_path.suffix + ".bak")
    backup.write_text(raw_text)
    ConfigWriter.write(new_config, config_path)
    print(f"Synced system reality into {config_path} (backup: {backup}).")
    return 0


def _cmd_generations(target_root: str) -> int:
    """List recorded generations, marking the current one."""
    gens = GenerationStore(Target(root=target_root)).list()
    if not gens:
        print("No generations recorded.")
        return 0
    for g in gens:
        flags = []
        if g.is_current:
            flags.append("current")
        if g.partial:
            # Not a convergence: the apply that produced it failed part-way, so
            # the system was mutated but never reached the declared state.
            flags.append("partial — apply failed part-way")
        marker = f" ({', '.join(flags)})" if flags else ""
        print(f"Generation {g.number}{marker}")
    return 0


def _previous_generation(gen_store: GenerationStore) -> Optional[int]:
    """The generation immediately before the current one, or None."""
    gens = gen_store.list()
    if not gens:
        return None
    current = next((g.number for g in gens if g.is_current), None)
    if current is None:
        return None
    # Skip partial generations: their apply failed part-way, so they never
    # represent a state the system converged to (see Manifest.partial).
    earlier = [g.number for g in gens if g.number < current and not g.partial]
    return max(earlier) if earlier else None


def _cmd_rollback(target_root: str, number: Optional[int], assume_yes: bool) -> int:
    """Restore a generation's config and re-apply it (spec §4 rollback)."""
    target = _target_or_none(target_root)
    if target is None:
        return 1
    state_store = StateStore(target)
    gen_store = GenerationStore(target)

    if number is None:
        number = _previous_generation(gen_store)
        if number is None:
            print("Error: no earlier generation to roll back to.", file=sys.stderr)
            return 1

    # restore() also repoints the `current` symlink at `number` here, before
    # the apply below runs. If the user aborts the apply, `current` points at
    # `number` while the system is unchanged — benign for slice 1 (the next
    # successful apply re-points it). Separating read-from-repoint is a future
    # GenerationStore API change. The restored *config* is the desired state;
    # the *current* manifest (loaded below) stays the owned set M.
    try:
        restored_config, _restored_manifest = gen_store.restore(number)
    except (FileNotFoundError, ValueError) as e:
        # ValueError: the target generation is partial (its apply failed
        # part-way) — restoring it would re-apply a state that never converged.
        print(f"Error: {e}", file=sys.stderr)
        return 1

    restored_config = expand_config(restored_config)
    setup_actions()
    registry = get_default_registry()
    manifest_dict = state_store.load().to_dict()

    reconciler = Reconciler(
        config=restored_config,
        target=target,
        manifest=manifest_dict,
        action_metas=registry.get_all_actions(),
        state_store=state_store,
        generation_store=gen_store,
    )
    plan, results = reconciler.build_plan()
    print(plan.render())

    if plan.is_empty():
        print(f"System already matches generation {number}.")
        return 0

    new_manifest = reconciler.apply(plan, results, assume_yes=assume_yes)
    if new_manifest is None:
        print("Aborted: no changes applied.", file=sys.stderr)
        return 1

    print(
        f"Rolled back to generation {number} "
        f"(recorded as generation {new_manifest.generation})."
    )
    return 0


def _reject_no_verb(config_path_str: str) -> int:
    """The removed no-verb ``dasik <config>`` form. Point at the verbs."""
    print(
        f"Error: `dasik {config_path_str}` (no verb) is no longer supported — "
        "the legacy install path was removed. Use a verb:\n"
        f"  dasik plan  {config_path_str}    # preview changes (read-only)\n"
        f"  dasik apply {config_path_str}    # converge the system (destructive)",
        file=sys.stderr,
    )
    return 2


def _cmd_check(config_path: Path) -> int:
    """Validate a config: JSON syntax + the pydantic schema. Read-only, no target.
    Exit 0 when valid, 1 with a readable error otherwise."""
    try:
        raw = config_path.read_text()
    except OSError as e:
        print(f"Error reading {config_path}: {e}", file=sys.stderr)
        return 1
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON in {config_path}: {e}", file=sys.stderr)
        return 1
    # Assemble the fragments first: `check` validates the finished config, which
    # is also what makes it the way to verify a split before applying it.
    from dasik.lib.json_parser.includes import ConfigIncludeError, resolve_includes
    try:
        data = resolve_includes(data, config_path.parent)
    except ConfigIncludeError as e:
        print(f"Error in {config_path}: {e}", file=sys.stderr)
        return 1
    from pydantic import ValidationError
    from dasik.lib.models.json_model import JsonModel
    try:
        JsonModel.model_validate(data)
    except ValidationError as e:
        print(f"Config {config_path} is invalid:\n{e}", file=sys.stderr)
        return 1
    # Same cross-field checks plan/apply run, on the expanded config: `check`
    # exists so a coherence problem is found here, not mid-install.
    if _preflight_or_none(expand_config(data)) is None:
        return 1
    print(f"{config_path}: OK — valid dasik config.")
    return 0


def _cmd_hash_password(method: str = YESCRYPT) -> int:
    """Prompt for a password twice and print its hash in *method*'s format.

    Defaults to yescrypt — what Arch's own ``passwd`` writes (login.defs sets
    ENCRYPT_METHOD YESCRYPT) and therefore what ``sync`` reads back out of
    /etc/shadow. Returns 1 on mismatch, empty input, or a hashing failure; 0 on
    success.
    """
    pw = getpass.getpass("Password: ")
    if not pw:
        print("Error: empty password.", file=sys.stderr)
        return 1
    if pw != getpass.getpass("Confirm: "):
        print("Error: passwords do not match.", file=sys.stderr)
        return 1
    try:
        print(hash_password(pw, method=method))
    except PasswordHashError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    raw = sys.argv[1:] if argv is None else list(argv)

    args = None
    try:
        legacy_arg = _is_legacy_invocation(raw)
        if legacy_arg is not None:
            return _reject_no_verb(legacy_arg)

        parser = _build_parser()
        args = parser.parse_args(raw)

        _configure_logging(args)

        if args.verb == "plan":
            path = _validate_config_file(args.config)
            if path is None:
                return 1
            return _cmd_plan(path, args.target)

        if args.verb == "apply":
            path = _validate_config_file(args.config)
            if path is None:
                return 1
            return _cmd_apply(path, args.target, args.yes)

        if args.verb == "sync":
            path = _validate_config_file(args.config)
            if path is None:
                return 1
            return _cmd_sync(path, args.target)

        if args.verb == "generations":
            return _cmd_generations(args.target)

        if args.verb == "rollback":
            return _cmd_rollback(args.target, args.generation, args.yes)

        if args.verb == "check":
            path = _validate_config_file(args.config)
            if path is None:
                return 1
            return _cmd_check(path)

        if args.verb == "hash-password":
            return _cmd_hash_password(args.method)

        parser.print_help(file=sys.stderr)
        return 2

    except KeyboardInterrupt:
        print("\nOperation cancelled by user.", file=sys.stderr)
        return 130
    except Exception as e:
        # Surface the failure in red (and to the log file) via the run logger,
        # so a crash during a run is as visible as an in-band command failure.
        detail = ""
        if args is not None and getattr(args, "verbose", False):
            import traceback
            detail = traceback.format_exc()
        run_logger.get().error(str(e), detail=detail)
        return 1
    finally:
        run_logger.reset()


if __name__ == "__main__":
    sys.exit(main())
