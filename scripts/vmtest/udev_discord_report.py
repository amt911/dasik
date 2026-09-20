#!/usr/bin/env python
"""Report on / edit the config `guest-udev-discord.sh` captured, inside the guest.

Kept out of the guest script because it needs a heredoc the script already uses
for something else, and a nested one is how the previous pass silently reported
a TypeError instead of an assertion.

    capture  what `sync` wrote back for the two shared domains
    drop     write /root/cfg/dropped.json: the same capture with the udev files
             and the home tree taken OUT, so `plan` has to REMOVE what a
             previous generation owns
"""
import json
import sys

CAP = "/root/cfg/main.json"
DROPPED = "/root/cfg/dropped.json"


def _files(cfg):
    """The `files` block, or None when it is still the unexpanded $concat form."""
    files = cfg.get("files") or []
    return None if isinstance(files, dict) else files


def capture():
    cfg = json.load(open(CAP))
    files = _files(cfg)
    if files is None:
        # `sync` left the $concat alone: the includes already deliver every path
        # it captured, so there was nothing NEW to write back. Print it so the
        # verdict can tell that apart from a capture that lost the domain.
        print("UD-CAPTURED-RULES: NOT-EXPANDED (sync did not rewrite `files`)")
        print("UD-CAPTURED-CONCAT:", json.dumps(cfg["files"]))
    else:
        print("UD-CAPTURED-RULES:",
              json.dumps([f["path"] for f in files if "udev" in f["path"]]))
    print("UD-CAPTURED-HOME:",
          json.dumps([[h.get("user"), h.get("path")]
                      for h in (cfg.get("home_files") or [])]))
    print("UD-CAPTURED-HOMETREE:", json.dumps(cfg.get("home_tree")))
    # The rules come back as their OWN block: `udev_rules` is a top-level key
    # (json_model.py), one of DropFilesAction's _SECTIONS. A capture that lands
    # here is the domain reading back as itself, not as anonymous `files`.
    print("UD-CAPTURED-UDEVBLOCK:",
          json.dumps([e.get("name") for e in (cfg.get("udev_rules") or [])]))


def drop():
    """The config the user would write after deleting the shared fragment."""
    cfg = json.load(open(CAP))
    files = _files(cfg)
    if files is None:
        # Still the $concat form: drop the $include that carries the rules, the
        # way removing the fragment from a machine's config would.
        parts = cfg["files"]["$concat"]
        cfg["files"] = {"$concat": [
            p for p in parts
            if not (isinstance(p, dict) and "udev" in str(p.get("$include", "")))
        ]}
    else:
        cfg["files"] = [f for f in files if "udev" not in f["path"]]
    cfg.pop("home_files", None)
    cfg.pop("home_tree", None)
    # After a sync the rules are ALSO declared under `udev_rules`; dropping the
    # $include alone leaves them declared, which is why the first pass saw no
    # removal. Undeclaring the domain means both.
    cfg.pop("udev_rules", None)
    json.dump(cfg, open(DROPPED, "w"), indent=2)
    print("UD-DROPPED-WRITTEN: ok")


if __name__ == "__main__":
    {"capture": capture, "drop": drop}[sys.argv[1]]()
