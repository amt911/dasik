"""The split configs must stay identical to the single-file ones.

`config/test-config-split/` is `config/test-config.json` spread over 19 files.
Two copies of the same config drift the moment someone edits one of them, so the
equality is asserted here rather than trusted: assembling the split must produce
the tracked monolith, byte for byte in value terms.

The laptop pair is checked the same way when it is present — its single-file
form is deliberately untracked (it carries a real password hash), so a clone
without it skips instead of failing.
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
    assert assembled == expected


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
