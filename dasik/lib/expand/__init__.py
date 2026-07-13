"""NixOS-style expansion of feature toggles into the shared base domains.

`expand_config(config)` returns a derived config (used for plan/apply) with each
active toggle's packages/units/sockets/modprobe_conf/files merged into the base
`packages` / `systemd` / `modprobe_conf` / `files` sections.

`subtract_contributions(new_config, original)` removes toggle-owned items from a
captured config so `sync` does not duplicate them into the file — a resource a
toggle contributes is attributed to the toggle, not the base domain.
"""
from __future__ import annotations
import copy
from typing import Any, Dict

from .toggles import TOGGLES

_LIST_KEYS = ("packages", "units", "sockets", "modprobe_conf", "files", "user_groups")


def contributions(config: Dict[str, Any]) -> Dict[str, Any]:
    """Aggregate every active toggle's contribution (order-preserving, de-duped)."""
    out: Dict[str, list] = {k: [] for k in _LIST_KEYS}
    for fn in TOGGLES:
        frag = fn(config) or {}
        for key in _LIST_KEYS:
            for item in frag.get(key, []):
                if item not in out[key]:
                    out[key].append(item)
    return out


def _merge_list(base: list, extra: list) -> list:
    out = list(base)
    for item in extra:
        if item not in out:
            out.append(item)
    return out


def expand_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of config with toggle contributions merged into base domains."""
    merged = copy.deepcopy(config)
    c = contributions(config)

    if c["packages"]:
        merged["packages"] = _merge_list(merged.get("packages", []), c["packages"])

    if c["units"] or c["sockets"]:
        sd = dict(merged.get("systemd", {}) or {})
        sd["enable_units"] = _merge_list(sd.get("enable_units", []), c["units"])
        sd["enable_sockets"] = _merge_list(sd.get("enable_sockets", []), c["sockets"])
        merged["systemd"] = sd

    if c["modprobe_conf"]:
        merged["modprobe_conf"] = _merge_list(merged.get("modprobe_conf", []), c["modprobe_conf"])

    if c["files"]:
        merged["files"] = _merge_list(merged.get("files", []), c["files"])

    if c["user_groups"]:
        # A toggle (e.g. kvm → libvirt) can grant a group to every declared
        # user; UsersAction then reconciles the membership idempotently.
        users = merged.get("users") or []
        merged["users"] = [
            {**u, "groups": _merge_list(u.get("groups", []), c["user_groups"])}
            for u in users
        ]

    return merged


def subtract_contributions(new_config: Dict[str, Any], original: Dict[str, Any]) -> Dict[str, Any]:
    """Drop toggle-contributed items from new_config not present in original base."""
    result = copy.deepcopy(new_config)
    c = contributions(original)

    orig_pkgs = set(original.get("packages", []))
    if "packages" in result:
        result["packages"] = [
            p for p in result["packages"] if p not in c["packages"] or p in orig_pkgs
        ]

    if "systemd" in result and isinstance(result["systemd"], dict):
        sd = result["systemd"]
        orig_sd = original.get("systemd", {}) or {}
        for key, items in (("enable_units", c["units"]), ("enable_sockets", c["sockets"])):
            if key in sd:
                keep = set(orig_sd.get(key, []))
                sd[key] = [x for x in sd[key] if x not in items or x in keep]

    for key, items in (("modprobe_conf", c["modprobe_conf"]), ("files", c["files"])):
        if key in result:
            orig_items = original.get(key, [])
            result[key] = [x for x in result[key] if x not in items or x in orig_items]

    if c["user_groups"] and "users" in result:
        orig_groups = {u.get("username"): set(u.get("groups", []))
                       for u in original.get("users", [])}
        for u in result["users"]:
            keep = orig_groups.get(u.get("username"), set())
            u["groups"] = [g for g in u.get("groups", [])
                           if g not in c["user_groups"] or g in keep]

    return result
