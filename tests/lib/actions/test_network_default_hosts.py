"""`/etc/hosts` carries the wiki's block unless you say otherwise.

`nss-myhostname` resolves the local hostname for most software, but some reads
`/etc/hosts` directly — and without an entry there it resolves the machine's own
name **over the network**. Network_configuration(7)'s answer is three lines, and
dasik has written them for a long time; they were just behind a flag that
defaulted to off, so a config that said nothing got the unsafe file.

The flag stays, because a machine whose /etc/hosts is managed elsewhere is a
real case. What changed is which way it points when nobody says.
"""
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.network_action import NetworkAction
from dasik.lib.models.json_model import JsonModel
from dasik.lib.models.network_model import NetworkModel
from dasik.lib.target.target import Target

_BLOCK = "127.0.0.1 localhost\n::1 localhost\n127.0.1.1 box\n"


def _action(tmp_path, config):
    return NetworkAction(config, ActionContext(target=Target(root=str(tmp_path))))


def _machine(tmp_path, hosts="", hostname="box"):
    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/hostname").write_text(hostname)
    (tmp_path / "etc/hosts").write_text(hosts)
    return tmp_path


def test_the_model_defaults_to_writing_the_block():
    assert NetworkModel(type="NetworkManager").add_default_hosts is True


def test_a_config_that_says_nothing_still_gets_the_block():
    m = JsonModel(hostname="box", network={"type": "NetworkManager"})
    assert m.network.add_default_hosts is True


def test_a_machine_without_the_block_is_planned(tmp_path):
    """The action must default the same way the model does — it reads the raw
    dict, so a model-only change would leave the two disagreeing."""
    _machine(tmp_path, hosts="# Static table lookup for hostnames.\n")

    action = _action(tmp_path, {"hostname": "box",
                                "network": {"type": "NetworkManager"}})

    assert action.plan(managed=[]) != []


def test_a_machine_with_the_block_plans_nothing(tmp_path):
    _machine(tmp_path, hosts=f"# comment\n{_BLOCK}")

    action = _action(tmp_path, {"hostname": "box",
                                "network": {"type": "NetworkManager"}})

    assert action.plan(managed=[]) == []


def test_saying_false_still_means_false(tmp_path):
    """The escape hatch for a machine whose /etc/hosts somebody else manages."""
    _machine(tmp_path, hosts="# Static table lookup for hostnames.\n")

    action = _action(tmp_path, {"hostname": "box",
                                "network": {"type": "NetworkManager",
                                            "add_default_hosts": False}})

    assert action.plan(managed=[]) == []


def test_apply_writes_the_three_lines_the_wiki_recommends(tmp_path):
    _machine(tmp_path, hosts="# Static table lookup for hostnames.\n")
    action = _action(tmp_path, {"hostname": "box",
                                "network": {"type": "NetworkManager"}})

    action.apply(action.plan(managed=[]))

    assert (tmp_path / "etc/hosts").read_text().endswith(_BLOCK)


def test_the_capture_still_reports_reality_not_the_default(tmp_path):
    """A machine without the block captures False. sync reports what IS —
    defaulting the capture to True would describe a file nobody wrote."""
    _machine(tmp_path, hosts="127.0.0.1 localhost\n")

    captured = _action(tmp_path, {"hostname": "box",
                                  "network": {"type": "NetworkManager"}}).import_state()

    assert captured["network"]["add_default_hosts"] is False
