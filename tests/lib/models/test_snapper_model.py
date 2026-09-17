"""SnapperConfig.name -- interpolated into /etc/snapper/configs/<name> and
into `rm -f`/`snapper -c <name>` argv (`_config_path`, `_delete_config`).
Unvalidated, a traversal-shaped name would be a path built from an
unvalidated value; snapper itself likely rejects illegal names at
`create-config`, so a name like this can probably never become OWNED in the
first place -- but a cheap regex on the model is defense in depth given the
REMOVE path can delete real snapshot subvolumes (N6)."""
import pytest

from dasik.lib.models.snapper_model import SnapperConfig, SnapperModel


def test_a_normal_name_is_accepted():
    assert SnapperConfig(name="root", subvolume="/").name == "root"


def test_a_name_with_hyphen_and_underscore_is_accepted():
    assert SnapperConfig(name="root-2_b", subvolume="/").name == "root-2_b"


def test_a_traversal_name_is_refused():
    with pytest.raises(ValueError):
        SnapperConfig(name="../etc/passwd", subvolume="/")


def test_a_name_with_a_slash_is_refused():
    with pytest.raises(ValueError):
        SnapperConfig(name="a/b", subvolume="/")


def test_a_name_with_a_space_is_refused():
    with pytest.raises(ValueError):
        SnapperConfig(name="my config", subvolume="/")


def test_an_empty_name_is_refused():
    with pytest.raises(ValueError):
        SnapperConfig(name="", subvolume="/")


def test_default_model_config_is_still_valid():
    assert SnapperModel().configs[0].name == "root"
