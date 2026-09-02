"""The `uv_tools` block — Python programs installed per user with `uv tool`.

The reason this is its own domain and not a `packages` entry: the tools that
belong here are the ones whose upstream says so. graphify's own documentation
recommends `uv tool install graphifyy` and does not package for Arch at all;
the AUR build of it pulls 26 tree-sitter grammars that are in no official
repository. An isolated per-user environment is the shape upstream ships.
"""
import pytest
from pydantic import ValidationError

from dasik.lib.models.uv_tools_model import UvToolsModel


def _model(**over):
    base = {"tools": ["graphifyy"]}
    base.update(over)
    return UvToolsModel(**base)


def test_defaults_are_empty_users_and_warn_and_continue():
    model = _model()
    assert model.users == []
    assert model.failure_policy == "warn-and-continue"
    assert model.tools == ["graphifyy"]


def test_an_explicit_user_list_is_kept():
    assert _model(users=["andres", "otro"]).users == ["andres", "otro"]


def test_duplicate_users_are_rejected():
    with pytest.raises(ValidationError):
        _model(users=["andres", "andres"])


def test_duplicate_tools_are_rejected():
    with pytest.raises(ValidationError):
        _model(tools=["graphifyy", "graphifyy"])


def test_a_tool_name_must_be_a_bare_distribution_name():
    # It reaches a command line. A name with a space, a slash or a shell
    # metacharacter is refused rather than quoted and hoped for.
    for bad in ("graphifyy semgrep", "../graphifyy", "graphifyy;rm -rf /",
                "graph$ifyy", "graphifyy&&x", ""):
        with pytest.raises(ValidationError):
            _model(tools=[bad])


def test_a_version_pin_is_accepted():
    # `uv tool install graphifyy==0.9.53` is a legitimate declaration: unlike a
    # plugin marketplace, uv is the only thing that would move this version.
    assert _model(tools=["graphifyy==0.9.53"]).tools == ["graphifyy==0.9.53"]


def test_an_extras_declaration_is_accepted():
    assert _model(tools=["semgrep[all]"]).tools == ["semgrep[all]"]


def test_an_unknown_key_is_rejected():
    with pytest.raises(ValidationError):
        _model(verison="1")


def test_failure_policy_abort_is_accepted():
    assert _model(failure_policy="abort").failure_policy == "abort"


def test_an_unknown_failure_policy_is_rejected():
    with pytest.raises(ValidationError):
        _model(failure_policy="retry-forever")


def test_an_empty_block_is_valid():
    assert UvToolsModel().tools == []
