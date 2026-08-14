#!/usr/bin/env python3
"""Resolve every package name dasik can produce, and fail on the ones that are gone.

Both bugs of PR #187 — `nvidia` and `libva-mesa-driver`, each of which aborted a
whole install with "target not found" *after* the disk had been partitioned —
were invisible for the same reason: the package names dasik derives are **data
nobody resolves**. The unit suite asserts that the amd toggle contributes
`mesa`; nothing asked pacman whether `mesa` still exists.

Two name sets, checked with different strictness:

* **derived** — everything the expand toggles can contribute, with every toggle
  turned on and both multilib settings. These MUST exist in a pacman repo (or be
  a group): dasik puts them in a single `pacman -S` transaction, so one missing
  name takes the whole install down.
* **sample configs** — the tracked `config/*.json`. These may legitimately live
  in the AUR or in a `package_sources` Git PKGBUILD, so they are resolved
  against repos, groups, the AUR, and the config's own declared sources.

Usage:
    scripts/check-package-names.py            # both sets
    scripts/check-package-names.py --derived  # only what dasik itself derives
    scripts/check-package-names.py --json     # machine-readable report

Needs pacman with synced databases (`pacman -Sy`) — so an Arch host, or the
`archlinux` container the scheduled workflow runs in. Network is used only for
the AUR RPC, and an unreachable AUR is reported as "unresolved", never as
"missing": we must not turn "we could not look" into "it does not exist".
"""
from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404 - runs pacman with a fixed argv, no shell
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dasik.lib.expand import contributions  # noqa: E402
from dasik.lib.expand.toggles import (  # noqa: E402
    _DRIVER_PKGS,
    _HWACCEL_DRIVER_PKGS,
)

AUR_RPC = "https://aur.archlinux.org/rpc/v5/info"


# --- the names dasik can derive ------------------------------------------- #

def _maximal_config(multilib: bool, drivers: List[str]) -> dict:
    """A config with every toggle on, so `contributions` yields everything."""
    return {
        "bluetooth": {"enable": True},
        "cups": {"install": True},
        "enable_trim": True,
        "kvm": {"install": True},
        "wireguard": {"enable": True, "interface_name": "wg0"},
        "snapper": {"enable": True},
        "firewall": {"enable": True},
        "hardware_acceleration": {"enable": True},
        "drivers": drivers,
        "pacman": {"multilib": multilib},
        "initramfs": "dracut",
        "zram": {"zram0": {"zram-size": "ram / 2"}},
        "cpu": {"scaling_driver": "amd_pstate", "governor": "performance"},
        "reflector": {"countries": ["ES"]},
        "plymouth": {"theme": "bgrt"},
        "apparmor": {"enable": True, "audit": True, "desktop_notifications": True},
        "pam": {"pwquality": {"enable": True}},
        "bootloader": "sd-boot",
        # A syntactically valid crypt hash so the toggles that read `users`
        # see one; it is a literal in a name-resolution script, not a secret.
        "users": [{"username": "u", "hashed_password": "$6$a$b"}],  # nosec B105
        "containers": {"runtime": "podman", "docker_compat": True, "compose": True},
    }


def derived_names() -> Set[str]:
    names: Set[str] = set()
    all_drivers = sorted(_DRIVER_PKGS) + sorted(_HWACCEL_DRIVER_PKGS)
    for multilib in (False, True):
        names |= set(contributions(_maximal_config(multilib, all_drivers))["packages"])
    # The docker half of the containers toggle, which podman excludes.
    for runtime in ("docker",):
        cfg = _maximal_config(False, [])
        cfg["containers"] = {"runtime": runtime, "compose": True}
        names |= set(contributions(cfg)["packages"])
    # config-saver is built from a Git PKGBUILD by design: it is in no repo.
    names.discard("config-saver")
    return names


def sample_config_names() -> Dict[str, Tuple[Set[str], Set[str]]]:
    """{config file: (declared names, names with a package_sources entry)}."""
    out: Dict[str, Tuple[Set[str], Set[str]]] = {}
    for path in sorted((REPO_ROOT / "config").glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        names: Set[str] = set()
        for entry in data.get("packages") or []:
            name = entry if isinstance(entry, str) else (entry or {}).get("name")
            if isinstance(name, str) and name:
                names.add(name[4:] if name.startswith("aur-") else name)
        if names:
            out[path.name] = (names, set(data.get("package_sources") or {}))
    return out


# --- resolution ------------------------------------------------------------ #

def _pacman(args: List[str]) -> Set[str]:
    try:
        res = subprocess.run(["pacman", *args], capture_output=True,  # nosec B603, B607
                             text=True, check=False)
    except FileNotFoundError:
        raise SystemExit("pacman not found — run this on Arch or in the "
                         "archlinux container (see the workflow).")
    return {line.strip() for line in res.stdout.splitlines() if line.strip()}


def repo_names() -> Set[str]:
    """Every package name and group name the synced databases know."""
    return _pacman(["-Slq"]) | _pacman(["-Sgq"])


def _provided(names: Iterable[str]) -> Set[str]:
    """Names some package *provides* (a rename leaves a provides behind).

    `pacman -Ssq '^name$'` also matches a provider, which is exactly the
    "renamed but still installable" case — `libva-mesa-driver` was NOT one of
    those, which is why it broke.
    """
    found = set()
    for name in names:
        if _pacman(["-Ssq", f"^{name}$"]):
            found.add(name)
    return found


def aur_names(names: Iterable[str]) -> Tuple[Set[str], bool]:
    """(names the AUR knows, reachable). Batched under the URI length cap."""
    todo = sorted(names)
    known: Set[str] = set()
    batch: List[str] = []
    reachable = True

    def flush(items: List[str]) -> None:
        nonlocal reachable
        if not items:
            return
        query = "&".join(f"arg[]={urllib.parse.quote(n)}" for n in items)
        try:
            with urllib.request.urlopen(f"{AUR_RPC}?{query}", timeout=30) as resp:  # nosec B310 - fixed https URL
                payload = json.load(resp)
        except (urllib.error.URLError, ValueError, TimeoutError):
            reachable = False
            return
        for result in payload.get("results", []):
            known.add(result.get("Name", ""))

    for name in todo:
        batch.append(name)
        if len("&".join(batch)) > 3500:
            flush(batch)
            batch = []
    flush(batch)
    return known, reachable


# --- report ----------------------------------------------------------------- #

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--derived", action="store_true",
                        help="only the names dasik itself derives")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    args = parser.parse_args()

    repos = repo_names()
    derived = derived_names()
    missing_derived = sorted(n for n in derived - repos if n not in _provided(derived - repos))

    report: Dict[str, object] = {"derived": {"checked": len(derived),
                                             "missing": missing_derived}}
    failed = bool(missing_derived)

    if not args.derived:
        samples = sample_config_names()
        every = set().union(*(names for names, _ in samples.values())) if samples else set()
        unknown_to_pacman = {n for n in every - repos if not _pacman(["-Ssq", f"^{n}$"])}
        in_aur, reachable = aur_names(unknown_to_pacman)
        per_config: Dict[str, List[str]] = {}
        for filename, (names, sources) in samples.items():
            gone = sorted(n for n in names
                          if n in unknown_to_pacman - in_aur and n not in sources
                          and not n.startswith("dasik-package-does-not-exist"))
            if gone:
                per_config[filename] = gone
        report["configs"] = {"checked": len(every), "missing": per_config,
                             "aur_reachable": reachable}
        # An unreachable AUR is not evidence of absence.
        failed = failed or (bool(per_config) and reachable)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"derived names checked: {len(derived)}")
        if missing_derived:
            print("  MISSING from every repo (an install would abort):")
            for name in missing_derived:
                print(f"    - {name}")
        else:
            print("  all resolve.")
        if not args.derived:
            configs = report["configs"]           # type: ignore[index]
            print(f"sample-config names checked: {configs['checked']}"       # type: ignore[index]
                  f" (AUR reachable: {configs['aur_reachable']})")           # type: ignore[index]
            for filename, gone in sorted(configs["missing"].items()):        # type: ignore[index]
                print(f"  {filename}: {', '.join(gone)}")
            if not configs["missing"]:                                       # type: ignore[index]
                print("  all resolve.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
