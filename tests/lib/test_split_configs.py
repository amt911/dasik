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
from pathlib import Path

import pytest

from dasik.lib.json_parser.includes import resolve_includes
from dasik.lib.models.json_model import JsonModel

REPO = Path(__file__).resolve().parents[2]
PAIRS = [
    ("config/test-config.json", "config/test-config-split/main.json"),
    ("config/laptop-p14s.json", "config/laptop-p14s-split/main.json"),
]


def _assembled(split_main: Path):
    return resolve_includes(json.loads(split_main.read_text()), split_main.parent)


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
def test_split_assembles_to_the_single_file_config(mono_rel, split_rel):
    mono, split_main = REPO / mono_rel, REPO / split_rel
    if not mono.exists() or not split_main.exists():
        pytest.skip(f"{mono_rel} is not present in this checkout")
    if not (split_main.parent / "secrets" / "hashed-password").exists() \
            and (split_main.parent / "secrets").exists():
        pytest.skip("secrets/ not filled in (copy the .example files)")

    assembled = _assembled(split_main)
    expected = json.loads(mono.read_text())
    # The split carries its own note about being a split; the rest must match.
    assembled.pop("metadata", None)
    expected.pop("metadata", None)

    assert _same_but_for_secrets(assembled, expected, _secret_values(split_main)), (
        f"{split_rel} does not assemble to {mono_rel}\n"
        f"assembled: {json.dumps(assembled, sort_keys=True)[:2000]}\n"
        f"expected:  {json.dumps(expected, sort_keys=True)[:2000]}")


@pytest.mark.parametrize("_mono_rel,split_rel", PAIRS)
def test_the_assembled_split_still_validates(_mono_rel, split_rel):
    split_main = REPO / split_rel
    if not split_main.exists():
        pytest.skip(f"{split_rel} is not present in this checkout")
    if (split_main.parent / "secrets").exists() \
            and not (split_main.parent / "secrets" / "hashed-password").exists():
        pytest.skip("secrets/ not filled in (copy the .example files)")
    JsonModel.model_validate(_assembled(split_main))


def test_the_split_example_needs_no_secrets_to_validate():
    """config/split-example/ is the documentation sample: it must work in a
    fresh clone, so it carries no secrets/ directory at all."""
    main = REPO / "config/split-example/main.json"
    JsonModel.model_validate(_assembled(main))
    assert not (main.parent / "secrets").exists()
