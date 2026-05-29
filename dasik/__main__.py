"""dasik CLI entry point.

Verbs (slice 1 of declarative-convergence):
  * ``plan <config> [--target / | /mnt]`` — show the diff between config and
    system reality. **Read-only; safe to run on any host.**
  * ``apply <config> [--target / | /mnt] [--yes]`` — converge the system to
    the config (DESTRUCTIVE). Prompts before destructive changes unless
    ``--yes`` is passed.
  * ``sync <config> [--target /]`` — capture system reality back into the
    config file (non-destructive to the system). Defaults to ``--target /``
    for day-2 host management.
  * ``generations [--target /]`` — list recorded generations, marking the
    current one. **Read-only.**
  * (no verb) ``dasik <config>`` — DEPRECATED. Falls back to the legacy
    install path (``ActionsHandler``). Will be removed once ``apply`` lands.

``rollback`` lands in a future plan.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from dasik.lib.actions.actions_handler import ActionsHandler
from dasik.lib.actions.actions_handler_v2 import setup_actions
from dasik.lib.actions.action_registry import get_default_registry
from dasik.lib.reconciler.reconciler import Reconciler
from dasik.lib.state.config_writer import ConfigWriter
from dasik.lib.state.generation_store import GenerationStore
from dasik.lib.state.state_store import StateStore
from dasik.lib.target.target import Target


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
                        help="Enable verbose output")

    sub = parser.add_subparsers(dest="verb")

    plan_p = sub.add_parser(
        "plan",
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
        help="List recorded generations",
    )
    gens_p.add_argument(
        "--target",
        default="/",
        help="Root whose generations to list. Default: /.",
    )

    return parser


_KNOWN_VERBS = {"plan", "apply", "sync", "generations", "rollback"}


def _is_legacy_invocation(raw: list[str]) -> Optional[str]:
    """If argv matches the deprecated ``dasik <config>`` form, return the
    config path. Otherwise return None and let argparse handle it.
    """
    non_flags = [a for a in raw if not a.startswith("-")]
    if non_flags and non_flags[0] not in _KNOWN_VERBS:
        return non_flags[0]
    return None


def _cmd_plan(config_path: Path, target_root: str) -> int:
    """Run the read-only plan flow."""
    try:
        config = json.loads(config_path.read_text())
    except Exception as e:
        print(f"Error loading config: {e}", file=sys.stderr)
        return 1

    setup_actions()
    registry = get_default_registry()

    reconciler = Reconciler(
        config=config,
        target=Target(root=target_root),
        manifest=None,
        action_metas=registry.get_all_actions(),
    )
    plan, _results = reconciler.build_plan()
    print(plan.render())
    return 0


def _cmd_apply(config_path: Path, target_root: str, assume_yes: bool) -> int:
    """Run the destructive convergence flow."""
    try:
        config = json.loads(config_path.read_text())
    except Exception as e:
        print(f"Error loading config: {e}", file=sys.stderr)
        return 1

    setup_actions()
    registry = get_default_registry()
    target = Target(root=target_root)
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

    new_manifest = reconciler.apply(plan, results, assume_yes=assume_yes)
    if new_manifest is None:
        print("Aborted: no changes applied.", file=sys.stderr)
        return 1

    print(f"Applied: now at generation {new_manifest.generation}.")
    return 0


def _cmd_sync(config_path: Path, target_root: str) -> int:
    """Capture system reality back into the config file (spec §4 sync flow)."""
    try:
        raw_text = config_path.read_text()
        config = json.loads(raw_text)
    except Exception as e:
        print(f"Error loading config: {e}", file=sys.stderr)
        return 1

    setup_actions()
    registry = get_default_registry()
    target = Target(root=target_root)
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

    if new_manifest is None:
        print("Nothing to sync (no convergence-aware actions registered).")
        return 0
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
        marker = " (current)" if g.is_current else ""
        print(f"Generation {g.number}{marker}")
    return 0


def _cmd_legacy(config_path_str: str) -> int:
    """Deprecated no-verb form. Delegates to the legacy install handler."""
    print(
        "Warning: invoking `dasik <config>` without a verb is deprecated. "
        "Use `dasik plan <config>` or (Plan 4) `dasik apply <config>`.",
        file=sys.stderr,
    )
    ActionsHandler(config_path_str)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    raw = sys.argv[1:] if argv is None else list(argv)

    args = None
    try:
        legacy_arg = _is_legacy_invocation(raw)
        if legacy_arg is not None:
            path = _validate_config_file(legacy_arg)
            if path is None:
                return 1
            return _cmd_legacy(str(path))

        parser = _build_parser()
        args = parser.parse_args(raw)

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

        parser.print_help(file=sys.stderr)
        return 2

    except KeyboardInterrupt:
        print("\nOperation cancelled by user.", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        if args is not None and getattr(args, "verbose", False):
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
