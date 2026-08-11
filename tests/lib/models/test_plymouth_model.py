"""The `plymouth` block: a boot splash, optionally themed."""
import pytest
from pydantic import ValidationError

from dasik.lib.models.plymouth_model import PlymouthModel
from dasik.lib.models.json_model import JsonModel


def test_an_empty_block_is_valid_and_leaves_the_theme_alone():
    assert PlymouthModel().theme is None


def test_the_theme_is_kept_verbatim():
    assert PlymouthModel(theme="bgrt").theme == "bgrt"


@pytest.mark.parametrize("bad", ["../../etc/passwd", "two words", "semi;colon", ""])
def test_a_theme_that_is_not_a_plain_name_is_rejected(bad):
    """The theme reaches a config file and a themes directory path."""
    with pytest.raises(ValidationError):
        PlymouthModel(theme=bad)


def test_json_model_accepts_the_block_and_defaults_to_absent():
    assert JsonModel(hostname="box").plymouth is None
    assert JsonModel(hostname="box", plymouth={"theme": "spinner"}).plymouth.theme == "spinner"
