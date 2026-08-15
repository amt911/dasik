"""The split configs must stay identical to the single-file ones.

`config/test-config-split/` is `config/test-config.json` spread over 19 files.
Two copies of the same config drift the moment someone edits one of them, so the
equality is asserted here rather than trusted: assembling the split must produce
the tracked monolith, byte for byte in value terms.

The laptop pair is checked the same way. Both forms are tracked and carry only
placeholder credentials — the real ones live in the split's untracked
`secrets/`. So a value the split reads from there is compared leniently: filling
in your own secrets (exactly what the .example files tell you to do) must not
"break" the parity of two files you never edited. Every other value is compared
strictly.
"""
import json
import shutil
from pathlib import Path

import pytest

from dasik.lib.json_parser.etc_tree import expand_etc_tree
from dasik.lib.json_parser.includes import resolve_includes
from dasik.lib.models.json_model import JsonModel

REPO = Path(__file__).resolve().parents[2]
PAIRS = [
    ("config/test-config.json", "config/test-config-split/main.json"),
    ("config/laptop-p14s.json", "config/laptop-p14s-split/main.json"),
]


def _with_fake_secrets(split_main: Path, tmp_path: Path) -> Path:
    """A copy of the split with placeholder secrets, for a checkout that has none.

    The real ones are gitignored, so relying on them means the comparison is
    skipped exactly where it matters most — a clean clone, i.e. CI. (It did:
    this file's own laptop assertions failed on main for a day because a
    developer machine had the secrets and the runner did not.)
    """
    scratch = tmp_path / split_main.parent.name
    shutil.copytree(split_main.parent, scratch)
    secrets = scratch / "secrets"
    if secrets.is_dir():
        for example in secrets.glob("*.example"):
            real = example.with_suffix("")
            if real.exists():
                continue
            # A hash has to look like one: the model refuses a plaintext
            # password outright, which is the point of that validator.
            plausible = ("$6$dasiktest$c2VjcmV0.placeholder.hash.for.tests"
                         if "hash" in real.name or "password" in real.name
                         else "placeholder")
            real.write_text(plausible + "\n")
    return scratch / split_main.name


def _assembled(split_main: Path):
    """The split as the loader sees it: directives resolved, `etc_tree`
    expanded. Both steps happen before anything else looks at a config."""
    data = resolve_includes(json.loads(split_main.read_text()), split_main.parent)
    return expand_etc_tree(data, split_main.parent)


# Keys where the two forms are allowed to differ, each for a stated reason.
# Everything else is compared strictly.
_FILE_KEYS = ("files", "udev_rules", "modprobe_conf", "modules_load")
_ALLOWED_EXTRA = {
    # The laptop split is also the worked example of managing /etc as a
    # directory and of driving config-saver, so it declares both. The files
    # themselves are still compared — see _etc_files.
    "config/laptop-p14s-split/main.json": {"etc_tree", "etc_tree_modes",
                                           "config_saver"},
}
_ALLOWED_EXTRA_PACKAGES = {
    "config/laptop-p14s-split/main.json": {"config-saver"},
}


def _etc_files(config) -> dict:
    """Every /etc file a config declares, however it declares it.

    A file may arrive as a `files` entry, as a snippet section, or from an
    `etc_tree` directory. Which of the three is a matter of style; *which files
    end up on the machine* is not, so that is what gets compared.
    """
    out = {e["path"]: e["content"] for e in config.get("files", [])}
    for key, directory in (("udev_rules", "/etc/udev/rules.d"),
                           ("modprobe_conf", "/etc/modprobe.d"),
                           ("modules_load", "/etc/modules-load.d"),
                           ("sysctl_d", "/etc/sysctl.d"),
                           ("tmpfiles_d", "/etc/tmpfiles.d"),
                           ("sddm_conf_d", "/etc/sddm.conf.d"),
                           ("profile_d", "/etc/profile.d")):
        for entry in config.get(key, []):
            out[f"{directory}/{entry['name']}"] = entry["content"]
    return out


def _package_names(config) -> set:
    return {p if isinstance(p, str) else p["name"] for p in config.get("packages", [])}


def _secret_values(split_main: Path) -> set:
    """The literal strings this split reads out of its untracked `secrets/`."""
    secrets = split_main.parent / "secrets"
    if not secrets.is_dir():
        return set()
    values = set()
    for path in secrets.iterdir():
        if path.suffix == ".example" or not path.is_file():
            continue
        text = path.read_text()
        # $include_line takes the first non-comment line; $include_text the lot.
        values.add(text)
        values.add(text.strip())
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                values.add(line)
                break
    return values


def _same_but_for_secrets(assembled, expected, secrets: set) -> bool:
    """Deep equality, except where the split's value came from `secrets/`."""
    if isinstance(assembled, dict) and isinstance(expected, dict):
        return assembled.keys() == expected.keys() and all(
            _same_but_for_secrets(assembled[k], expected[k], secrets) for k in assembled)
    if isinstance(assembled, list) and isinstance(expected, list):
        return len(assembled) == len(expected) and all(
            _same_but_for_secrets(a, e, secrets) for a, e in zip(assembled, expected))
    if isinstance(assembled, str) and assembled in secrets:
        return True
    return assembled == expected


@pytest.mark.parametrize("mono_rel,split_rel", PAIRS)
def test_split_assembles_to_the_single_file_config(mono_rel, split_rel, tmp_path):
    mono, split_main = REPO / mono_rel, REPO / split_rel
    if not mono.exists() or not split_main.exists():
        pytest.skip(f"{mono_rel} is not present in this checkout")

    split_main = _with_fake_secrets(split_main, tmp_path)
    assembled = _assembled(split_main)
    expected = json.loads(mono.read_text())
    # The split carries its own note about being a split; the rest must match.
    assembled.pop("metadata", None)
    expected.pop("metadata", None)

    # The same /etc files, no matter which of the three ways declares them.
    assert _etc_files(assembled) == _etc_files(expected), (
        f"{split_rel} and {mono_rel} do not declare the same /etc files")
    # …and the same packages, but for the ones the split's extra blocks need.
    assert _package_names(assembled) - _package_names(expected) == \
        _ALLOWED_EXTRA_PACKAGES.get(split_rel, set())
    assert not _package_names(expected) - _package_names(assembled)

    for key in _FILE_KEYS + ("packages",):
        assembled.pop(key, None)
        expected.pop(key, None)
    for key in _ALLOWED_EXTRA.get(split_rel, set()):
        assembled.pop(key, None)

    assert _same_but_for_secrets(assembled, expected, _secret_values(split_main)), (
        f"{split_rel} does not assemble to {mono_rel}\n"
        f"assembled: {json.dumps(assembled, sort_keys=True)[:2000]}\n"
        f"expected:  {json.dumps(expected, sort_keys=True)[:2000]}")


def test_the_laptop_split_drives_config_saver(tmp_path):
    """The laptop split is the worked example of the whole workflow, so it must
    keep declaring the piece that makes an archive self-sufficient."""
    split_main = _with_fake_secrets(
        REPO / "config/laptop-p14s-split/main.json", tmp_path)
    block = _assembled(split_main)["config_saver"]

    assert block["timer_users"] == ["andres"]
    assert block["source"]["url"].endswith("config-saver-aur.git")
    assert len(block["source"]["ref"]) == 40
    # config-saver ships own-configs.yaml as an EXAMPLE, and examples are never
    # active — on a dasik machine it arrives from no package, so unless the
    # config declares it, a restored archive brings back the data but not the
    # configurations that say what to back up.
    assert "own-configs" in block["configs"]


def test_the_laptop_split_keeps_its_etc_as_real_files():
    tree = REPO / "config/laptop-p14s-split/etc"
    relative = {str(p.relative_to(tree)) for p in tree.rglob("*") if p.is_file()}

    assert {"pam.d/sudo", "udev/rules.d/1-qudelix.rules",
            "modprobe.d/nested_virt.conf"} <= relative
    raw = json.loads((tree.parent / "main.json").read_text())
    assert raw["etc_tree"] == "etc"
    for key in _FILE_KEYS:
        assert key not in raw, f"{key} should live in the tree now"


@pytest.mark.parametrize("_mono_rel,split_rel", PAIRS)
def test_the_assembled_split_still_validates(_mono_rel, split_rel, tmp_path):
    split_main = REPO / split_rel
    if not split_main.exists():
        pytest.skip(f"{split_rel} is not present in this checkout")
    JsonModel.model_validate(_assembled(_with_fake_secrets(split_main, tmp_path)))


def test_the_split_example_needs_no_secrets_to_validate():
    """config/split-example/ is the documentation sample: it must work in a
    fresh clone, so it carries no secrets/ directory at all."""
    main = REPO / "config/split-example/main.json"
    JsonModel.model_validate(_assembled(main))
    assert not (main.parent / "secrets").exists()
