"""dasik CLI entry point.

Verbs (slice 1 of declarative-convergence):
  * ``plan <config> [--target / | /mnt]`` — show the diff between config and
    system reality. **Read-only; safe to run on any host.**
  * ``apply <config> [--target / | /mnt] [--yes]`` — converge the system to
    the config (DESTRUCTIVE). Prompts before destructive changes unless
    ``--yes`` is passed.
  * ``sync <config> [--target /]`` — capture system reality back into the
    config file (non-destructive to the system; rewrites the config).
  * ``save <config> [-m MSG] [--no-push]`` — sync, then commit (and push) the
    capture to the Git repository the config lives in, as the invoking user.
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
from dasik.lib.json_parser.etc_tree import extract_to_etc_tree
from dasik.lib.json_parser.home_tree import extract_to_home_tree
from dasik.lib.json_parser.wireguard_extract import extract_to_wireguard_dir
from dasik.lib.json_parser.writeback import write_back
from dasik.lib.state.config_writer import ConfigWriter
from dasik.lib.state.generation_store import GenerationStore
from dasik.lib.state.apply_lock import ApplyLock, ApplyLockBusy
from dasik.lib.state.state_store import StateStore
from dasik.lib.target.target import Target
from dasik.lib.target.target_check import check_target
from dasik.lib.expand import expand_config, subtract_contributions
from dasik.lib.exceptions.exceptions import PasswordHashError
from dasik.lib.git_save import (GitSaveError, chown_to, commit_paths,
                                invoking_user, repo_root)
from dasik.lib.home_archive import (HomeArchiveError, DEFAULT_ROOT,
                                    latest_archives, publish_archives)
from dasik.lib.passwords import SHA512, YESCRYPT, hash_password


# Verbs that shell out to real commands and therefore benefit from an install
# log. Read-only/trivial verbs (check, hash-password) write no log by default.
_LOGGED_VERBS = {"plan", "apply", "sync", "save", "rollback", "generations"}


def _version() -> str:
    """The installed package's version, not a literal.

    A hardcoded string drifts from pyproject.toml the first time somebody
    bumps one and not the other — and the package's own smoke test compares
    `dasik --version` against what the PKGBUILD built.
    """
    try:
        from importlib.metadata import version
        return version("dasik")
    except Exception:      # nosec B110 - running from a source tree, uninstalled
        return "0.0.0+unknown"


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
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {_version()}")
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

    save_p = sub.add_parser(
        "save",
        parents=[common],
        help="sync, then commit the capture to the config's Git repository",
    )
    save_p.add_argument("config", help="Path to the JSON configuration file")
    save_p.add_argument(
        "--target",
        default="/",
        help="Root to read reality from. Default: /.",
    )
    save_p.add_argument(
        "-m", "--message", default=None,
        help="Commit message. Default: '<hostname>: sync <date>'.",
    )
    save_p.add_argument(
        "--no-push", action="store_true",
        help="Commit but do not push.",
    )
    save_p.add_argument(
        "--home", metavar="OWNER/REPO", default=None,
        help="Also publish the newest config-saver archive of each "
             "configuration to this repository's <hostname> release. Encrypted "
             "archives only.",
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
    gens_p.add_argument(
        "--prune",
        type=int,
        metavar="N",
        default=None,
        help="Delete all but the N most recent generations. The current one and "
             "the newest complete one are always kept. Destructive to HISTORY "
             "only — the running system is not touched.",
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
    check_p.add_argument(
        "--resolve-aur",
        action="store_true",
        help="Also resolve every declared package against THIS host's pacman "
             "sync DBs and the AUR, and validate the transitive dependency "
             "closure of the AUR ones (needs network; the repo view is this "
             "host's, so refresh with `pacman -Sy` first).",
    )

    wizard_p = sub.add_parser(
        "partition-wizard",
        parents=[common],
        help="Compose a `disks` block from the real disks (writes a config; "
             "NEVER partitions anything)",
    )
    wizard_p.add_argument(
        "--output", default=None,
        help="Write a new config here. Refuses to overwrite unless --force.")
    wizard_p.add_argument(
        "--merge-into", default=None,
        help="Replace the `disks` block of an EXISTING config, keeping the rest.")
    wizard_p.add_argument(
        "--force", action="store_true",
        help="Allow --output to replace a file that already exists.")
    wizard_p.add_argument(
        "--from-lsblk", default=None,
        help="Read the inventory from a recorded `lsblk -J` file instead of the "
             "live system. For testing, and for composing a config for a machine "
             "you are not sitting at.")

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


_KNOWN_VERBS = {"plan", "apply", "sync", "save", "generations", "rollback",
                "check", "hash-password", "partition-wizard"}


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

    # …and it may declare a directory mirroring /etc, which becomes `files`
    # entries here for the same reason: only the loader knows where the config
    # is, and every action downstream sees ordinary entries.
    from dasik.lib.json_parser.etc_tree import ConfigTreeError, expand_etc_tree
    from dasik.lib.json_parser.home_tree import expand_home_tree
    from dasik.lib.json_parser.wireguard_source import expand_wireguard_sources
    try:
        config = expand_etc_tree(config, config_path.parent)
        config = expand_home_tree(config, config_path.parent)
        config = expand_wireguard_sources(config, config_path.parent)
    except ConfigTreeError as e:
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
    issues = preflight(config, environment=False)
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
        assume_yes=assume_yes,
    )
    plan, results = reconciler.build_plan()
    print(plan.render())

    if plan.is_empty():
        return 0

    # One apply at a time, per target: two of them race for the manifest, and
    # the loser's work ends up unowned. Taken AFTER the plan so a read-only
    # plan never blocks, and released by the kernel even if this process dies.
    try:
        lock = ApplyLock(target).__enter__()
    except ApplyLockBusy as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

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
    finally:
        lock.__exit__()
    if new_manifest is None:
        print("Aborted: no changes applied.", file=sys.stderr)
        return 1

    print(f"Applied: now at generation {new_manifest.generation}.")
    return 0


def _cmd_sync(config_path: Path, target_root: str) -> int:
    """Capture system reality back into the config file (spec §4 sync flow)."""
    return _sync_capture(config_path, target_root)[0]


def _sync_capture(config_path: Path, target_root: str) -> "tuple[int, list[Path]]":
    """`sync`, plus the list of files it wrote — what `save` has to commit."""
    target = _target_or_none(target_root)
    if target is None:
        return 1, []
    try:
        raw_text = config_path.read_text()
    except Exception as e:
        print(f"Error loading config: {e}", file=sys.stderr)
        return 1, []
    # Schema-validate the seed too: sync REWRITES this file, so starting from a
    # config pydantic would reject would silently launder it into a new one.
    # No preflight here — sync's job is to repair a config from reality.
    config = _load_validated_config(config_path)
    if config is None:
        return 1, []

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
        # Capture into the RAW config, but own the EXPANDED one — the set apply
        # actually wrote. Otherwise a sync quietly disowns every file a block
        # derives, and turning that block off stops removing them (issue #197).
        owned_config=expand_config(config),
    )
    new_config, new_manifest = reconciler.sync()
    new_config = subtract_contributions(new_config, config)

    if new_manifest is None:
        print("Nothing to sync (no convergence-aware actions registered).")
        return 0, []

    # A freshly-bootstrapped domain that captured nothing (empty) is not a
    # meaningful change: drop newly-added empty keys so sync doesn't rewrite
    # the file just to add e.g. "packages": [] on a config that omitted it.
    new_config = {k: v for k, v in new_config.items() if k in config or v}
    if new_config == config:
        print("Config already matches system reality - nothing to sync.")
        return 0, []

    backup = config_path.with_suffix(config_path.suffix + ".bak")
    backup.write_text(raw_text)

    # `etc_tree` describes the shape of the REPOSITORY, not of the machine, so
    # no action captures it and it has to survive the capture explicitly.
    for key in ("etc_tree", "home_tree"):
        if config.get(key) and not new_config.get(key):
            new_config[key] = config[key]
    # With a tree declared, captured /etc bodies go into it rather than into the
    # JSON — otherwise a capture undoes the split from the other direction.
    extraction = extract_to_etc_tree(new_config, config_path.parent)
    home = extract_to_home_tree(extraction.config, config_path.parent)
    # A tunnel is always a file next to the config, tree or no tree: its body is
    # a private key, and a JSON string cannot carry the 0600 the file had.
    wg = extract_to_wireguard_dir(home.config, config_path.parent)

    # Written THROUGH the directives: a config split across files keeps its
    # split, and a value that did not change does not reopen its file. The tree
    # writes ride along so the JSON and the tree can never disagree. The backup
    # covers the root only — the rest is what version control is for, which is
    # also where a split config lives.
    written = write_back(config_path, wg.config,
                         extra_writes={**extraction.writes, **home.writes,
                                       **wg.writes},
                         deletions=(extraction.deletions | home.deletions
                                    | wg.deletions))
    for path, mode in {**extraction.modes, **home.modes, **wg.modes}.items():
        os.chmod(path, mode)
    print(f"Synced system reality into {config_path} (backup: {backup}).")
    if len(written) > 1 or (written and written[0] != config_path):
        for path in written:
            print(f"  wrote {path}")
    return 0, written


def _cmd_save(config_path: Path, target_root: str, message: Optional[str],
              push: bool, home_repo: str = "") -> int:
    """`sync`, then commit what it wrote — the whole cycle as one command.

    The order is the point. `check` runs on the capture BEFORE the commit,
    because a config the tool would refuse is a broken capture and committing it
    spreads it to every machine that clones the repository.
    """
    try:
        user = invoking_user()
    except GitSaveError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Refuse before capturing, not after: a `save` that cannot commit should
    # leave the config exactly as it found it.
    repo = repo_root(config_path, user)
    if repo is None:
        print(f"Error: {config_path.parent} is not a Git repository. `save` "
              "commits the capture, so the config has to live in one — use "
              "`dasik sync` for a config that does not.", file=sys.stderr)
        return 1

    rc, written = _sync_capture(config_path, target_root)
    if rc != 0:
        return rc
    if not written:
        return 0            # _sync_capture already said nothing changed

    if user:
        # The capture ran as root; the repository is the user's.
        chown_to(user, written)

    if _cmd_check(config_path) != 0:
        print("Error: the capture does not validate — refusing to commit it. "
              "The config on disk is the capture; `git checkout` it or fix it.",
              file=sys.stderr)
        return 1

    # The hostname the CONFIG declares, not this machine's: the commit and the
    # release both document the machine the config describes, which is what you
    # want when one repository holds several of them.
    captured = _load_validated_config(config_path) or {}
    host = captured.get("hostname") or os.uname().nodename
    if message is None:
        message = f"{host}: sync {datetime.now().strftime('%Y-%m-%d')}"

    try:
        result = commit_paths(repo, written, message, push=push, user=user)
    except GitSaveError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    for path in result.skipped:
        print(f"  wrote {path}  (gitignored, not staged)")
    if not result.committed:
        print("Nothing to commit — the files the capture wrote are unchanged.")
        return 0
    # `sync` leaves <config>.bak. Once the capture is committed the previous
    # commit is a better backup, and an untracked .bak after every save leaves
    # `git status` permanently dirty. It stays when nothing was committed —
    # which is exactly when it is worth having.
    backup = config_path.with_suffix(config_path.suffix + ".bak")
    backup.unlink(missing_ok=True)

    print(f"Committed: {message}")
    if home_repo:
        _publish_home(home_repo, user, host)
    if result.push_error:
        print(f"Not pushed: {result.push_error}", file=sys.stderr)
    elif result.pushed:
        print("Pushed to origin.")
    return 0


def _publish_home(home_repo: str, user: Optional[str], host: str) -> None:
    """Publish the newest config-saver archive of each configuration.

    Separate from the config repository on purpose: one holds text you read in
    a diff, the other holds hundreds of megabytes that change daily. A failure
    here is reported, never fatal — the capture is already committed.
    """
    import pwd
    home = Path(pwd.getpwnam(user).pw_dir) if user else Path.home()
    try:
        archives = latest_archives(home / DEFAULT_ROOT)
        publish_archives(home_repo, host, archives, user)
    except HomeArchiveError as e:
        print(f"Not published: {e}", file=sys.stderr)
        return
    print(f"Published {len(archives)} archive(s) to {home_repo} ({host})")


def _cmd_generations(target_root: str, prune: Optional[int] = None) -> int:
    """List recorded generations, marking the current one; optionally prune.

    Pruning is opt-in and explicit. There is deliberately no cap on `apply` and
    no `keep_generations` in the config: both would delete history as a side
    effect of something else, and the generation somebody is about to roll back
    to is exactly the one an automatic policy takes (issue #233).
    """
    store = GenerationStore(Target(root=target_root))
    if prune is not None:
        try:
            removed = store.prune(keep=prune)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        if removed:
            print(f"Pruned {len(removed)} generation(s): "
                  f"{', '.join(str(n) for n in removed)}")
        else:
            print("Nothing to prune.")
    gens = store.list()
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
        gens = gen_store.list()
        if gens and not any(g.is_current for g in gens):
            # The `current` symlink is repointed by unlink-then-symlink, so a
            # power cut can leave it absent. Saying "no earlier generation"
            # there is false — there are plenty; dasik just does not know which
            # one it is standing on, and only the user can say.
            print(f"Error: no current generation recorded ({gen_store.current_link} is "
                  f"missing), so there is no 'previous' to roll back to. Pick one "
                  f"explicitly: dasik rollback <number> — `dasik generations` lists "
                  f"{', '.join(str(g.number) for g in gens)}.", file=sys.stderr)
            return 1
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
        assume_yes=assume_yes,
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


def _open_curses(disks):
    """`curses.wrapper` around the wizard. Split out so a test can make it fail.

    The wrapper restores the terminal even when the wizard raises, which matters
    on the installer ISO: a half-initialised curses session leaves a console
    nobody can type into, and there is no window manager to escape to.
    """
    import curses
    from dasik.lib.wizard.tui import run_wizard
    return curses.wrapper(lambda screen: run_wizard(screen, disks))


def _run_wizard_screens(disks):
    """Drive the screens, turning "there is no terminal" into a sentence.

    Run from a script, or with stdin redirected, curses ends the session with
    `setupterm: could not find terminal` and nothing else — which is not what
    anyone wants to read at the end of a partitioning session. The wizard is
    interactive by nature; the useful answer is to say so, and to name the flag
    that composes a config without a screen.
    """
    if not sys.stdin.isatty():
        print("Error: partition-wizard needs a terminal — stdin is not a tty. "
              "Run it directly on the machine (a serial console is fine), not "
              "from a pipe or a script.", file=sys.stderr)
        return None
    try:
        return _open_curses(disks)
    except Exception as e:      # noqa: BLE001 - curses failing to start at all
        print(f"Error: could not start the wizard's screen: {e}", file=sys.stderr)
        return None


def _cmd_partition_wizard(output: Optional[str] = None,
                          merge_into: Optional[str] = None,
                          force: bool = False,
                          from_lsblk: Optional[str] = None) -> int:
    """Compose a `disks` block from the real disks. Writes a config; never
    partitions anything.

    The split is the point (issue #190): partitioning is the one irreversible
    thing dasik does, so the assistant stops at the file. `plan` stays the last
    gate before a disk is erased.
    """
    import json as _json
    from dasik.lib.wizard import compose as _compose
    from dasik.lib.wizard.inventory import parse_lsblk, read_inventory
    from dasik.lib.wizard.recipes import custom_disk, find

    if not output and not merge_into:
        print("Error: partition-wizard needs --output <file> or "
              "--merge-into <file>.", file=sys.stderr)
        return 2

    if from_lsblk:
        try:
            disks = parse_lsblk(_json.loads(Path(from_lsblk).read_text()))
        except (OSError, _json.JSONDecodeError) as e:
            print(f"Error: cannot read {from_lsblk}: {e}", file=sys.stderr)
            return 1
    else:
        disks = read_inventory()

    if not disks:
        print("Error: no disks found. `lsblk -J` reported none — run this on "
              "the machine you mean to install, or pass --from-lsblk.",
              file=sys.stderr)
        return 1

    choices = _run_wizard_screens(disks)
    if choices is None:
        print("Wizard abandoned — nothing was written.")
        return 1

    if choices.recipe_key == "custom":
        stanza = custom_disk(choices.device, choices.custom_partitions,
                             wipe=choices.options.wipe)
        from dasik.lib.wizard.recipes import Contribution
        built = Contribution(disk=stanza)
    else:
        built = find(choices.recipe_key).build(choices.options)

    target = Path(merge_into) if merge_into else Path(output)  # type: ignore[arg-type]
    if merge_into:
        try:
            existing = _json.loads(target.read_text())
        except (OSError, _json.JSONDecodeError) as e:
            print(f"Error: cannot read {target}: {e}", file=sys.stderr)
            return 1
        config = _compose.merge_into(existing, built)
    else:
        config = _compose.compose(built, hostname=choices.hostname)

    # The secret first: a config whose `$include_line` points at a file that
    # does not exist is one `check` refuses, and writing it after would leave a
    # window where the config on disk is invalid.
    secret_path = None
    if choices.passphrase:
        secret_path = _compose.write_secret(
            target, choices.options.secret, choices.passphrase)

    try:
        _compose.write_config(target, config, overwrite=bool(merge_into) or force)
    except FileExistsError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"Wrote {target}")
    if secret_path:
        print(f"Wrote {secret_path} (mode 0600) — keep it out of Git.")
    for note in built.notes:
        print(f"  note: {note}")

    # Say whether what was just written is loadable, rather than leaving it to
    # be discovered later.
    if _cmd_check(target) != 0:
        print("The composed config did not validate — please report this.",
              file=sys.stderr)
        return 1

    print("")
    print("Nothing has been partitioned. Next:")
    print(f"  dasik plan {target}      # review every change first")
    print(f"  dasik apply {target}     # and only then, the destructive part")
    return 0


def _cmd_check(config_path: Path, resolve_aur: bool = False) -> int:
    """Validate a config: JSON syntax + the pydantic schema. Read-only, no target.
    Exit 0 when valid, 1 with a readable error otherwise.

    With *resolve_aur*, additionally resolve every declared package against
    THIS host's pacman sync DBs and the AUR RPC, and validate the transitive
    dependency closure of the AUR ones — the pre-trip defense against the
    2026-08-18 failure mode (a dep chain that dies 25 minutes into an
    install)."""
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
    from dasik.lib.json_parser.etc_tree import ConfigTreeError, expand_etc_tree
    from dasik.lib.json_parser.home_tree import expand_home_tree
    from dasik.lib.json_parser.wireguard_source import expand_wireguard_sources
    try:
        data = resolve_includes(data, config_path.parent)
        data = expand_etc_tree(data, config_path.parent)
        data = expand_home_tree(data, config_path.parent)
        data = expand_wireguard_sources(data, config_path.parent)
    except (ConfigIncludeError, ConfigTreeError) as e:
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
    expanded = expand_config(data)
    if _preflight_or_none(expanded) is None:
        return 1
    if resolve_aur and _resolve_aur_check(expanded) != 0:
        return 1
    print(f"{config_path}: OK — valid dasik config.")
    return 0


# Where pacman keeps its sync DBs on the machine running `check`; module-level
# so tests can point it at a fixture directory.
_SYNC_DB_DIR = Path("/var/lib/pacman/sync")
_STALE_DB_AGE_DAYS = 7


def _warn_stale_sync_dbs() -> None:
    """Say when the host's repo view is old enough to lie.

    The exact 2026-08-18 trap: four lib32 packages had left multilib upstream,
    but a 12-day-old sync DB still listed them, so the desktop would have
    called them repo-satisfied while the target's fresh DB disagreed. Best
    effort: an unreadable directory stays silent."""
    try:
        newest = max((p.stat().st_mtime for p in _SYNC_DB_DIR.glob("*.db")),
                     default=None)
    except OSError:
        return
    if newest is None:
        return
    import time
    age_days = (time.time() - newest) / 86400
    if age_days > _STALE_DB_AGE_DAYS:
        from datetime import datetime
        stamp = datetime.fromtimestamp(newest).strftime("%Y-%m-%d")
        print(f"[warning] this host's pacman sync DBs were last refreshed "
              f"{stamp} ({age_days:.0f} days ago): a package that recently "
              f"left a repo would still look repo-satisfied. Refresh with "
              f"`pacman -Sy` first.")


def _resolve_aur_check(config: dict) -> int:
    """Resolve declared packages + validate the AUR closure. 0 ok, 1 broken.

    The repo view is THIS host's sync DBs (there is no target in `check`), so
    it reflects the repos enabled here — good enough for the pre-trip check the
    flag exists for, and the staleness warning covers the known lie."""
    from dasik.lib.actions.package_resolver import AurUnavailableError
    from dasik.lib.actions.packages_action import PackagesAction
    from dasik.lib.exceptions.exceptions import CommandNotFoundException
    from dasik.lib.target.target import Target
    from dasik.lib.validation.aur_closure import validate_aur_closure

    _warn_stale_sync_dbs()
    action = PackagesAction(config)
    target = Target(root="/")
    try:
        resolution = action._resolve_sources(action.desired, target)
        if resolution.unavailable:
            print("[error] aur_unavailable: existence could not be checked "
                  "(retry): " + ", ".join(sorted(resolution.unavailable)),
                  file=sys.stderr)
            return 1
        if resolution.unknown:
            required = [n for n in resolution.unknown
                        if n not in action.optional_packages]
            if required and action.unknown_policy == "error":
                print("[error] unknown_package: not found in any configured "
                      "repo, group, package_sources or the AUR: "
                      + ", ".join(sorted(resolution.unknown)), file=sys.stderr)
                return 1
            print("[warning] packages with no source (skipped at apply): "
                  + ", ".join(sorted(resolution.unknown)))
        if resolution.aur:
            print(f"{len(resolution.aur)} package(s) resolve to the AUR: "
                  + ", ".join(resolution.aur))
        else:
            print("0 packages resolve to the AUR.")
            return 0
        broken = validate_aur_closure(resolution.aur, action._resolver, target)
    except AurUnavailableError as e:
        print(f"[error] aur_unavailable: existence could not be checked "
              f"(retry): {e}", file=sys.stderr)
        return 1
    except CommandNotFoundException as e:
        print(f"[error] --resolve-aur needs pacman on this machine: {e}",
              file=sys.stderr)
        return 1
    for b in broken:
        print(f"[error] aur_dependency_unsatisfiable: {b.render()}",
              file=sys.stderr)
    return 1 if broken else 0


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

        if args.verb == "save":
            path = _validate_config_file(args.config)
            if path is None:
                return 1
            return _cmd_save(path, args.target, args.message, not args.no_push,
                             home_repo=args.home or "")

        if args.verb == "generations":
            return _cmd_generations(args.target, getattr(args, "prune", None))
        if args.verb == "partition-wizard":
            return _cmd_partition_wizard(
                output=args.output, merge_into=args.merge_into,
                force=args.force, from_lsblk=args.from_lsblk)

        if args.verb == "rollback":
            return _cmd_rollback(args.target, args.generation, args.yes)

        if args.verb == "check":
            path = _validate_config_file(args.config)
            if path is None:
                return 1
            return _cmd_check(path, resolve_aur=args.resolve_aur)

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
