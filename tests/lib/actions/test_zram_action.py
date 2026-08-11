from dasik.lib.actions.zram_action import ZramAction, _render, _parse
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.target.target import Target
from dasik.lib.state.change import Op


def _ctx(root):
    return ActionContext(target=Target(root=root))


def _cfg():
    return {"zram": {"zram0": {"zram-size": "min(ram / 2, 8192)", "swap-priority": 100}}}


# --- rendering / parsing -------------------------------------------------

def test_render_canonical_sorted():
    out = _render({"zram0": {"zram-size": "8192", "swap-priority": 100}})
    assert out == "[zram0]\nswap-priority = 100\nzram-size = 8192\n"


def test_render_empty_is_blank():
    assert _render({}) == ""


def test_parse_roundtrips_render():
    sections = {"zram0": {"swap-priority": "100", "zram-size": "min(ram / 2, 8192)"}}
    assert _parse(_render(sections)) == sections


def test_parse_preserves_key_case_and_dashes():
    parsed = _parse("[zram0]\nzram-size = ram / 2\ncompression-algorithm = zstd\n")
    assert parsed["zram0"] == {"zram-size": "ram / 2", "compression-algorithm": "zstd"}


# --- v3 contract ---------------------------------------------------------

def test_is_v3_and_optional():
    a = ZramAction({})
    assert ZramAction.is_v3() is True
    assert a.is_optional is True
    assert a.name == "Zram Configuration"


def test_desired_none_when_no_zram():
    a = ZramAction({}, _ctx("/mnt"))
    assert a._desired_value() is None


def test_desired_renders_canonical(tmp_path):
    a = ZramAction(_cfg(), _ctx(str(tmp_path)))
    assert a._desired_value() == "[zram0]\nswap-priority = 100\nzram-size = min(ram / 2, 8192)\n"


def test_actual_none_when_file_missing(tmp_path):
    a = ZramAction(_cfg(), _ctx(str(tmp_path)))
    assert a._actual_value() is None


def test_apply_writes_conf(tmp_path):
    a = ZramAction(_cfg(), _ctx(str(tmp_path)))
    a._set_value()
    conf = tmp_path / "etc/systemd/zram-generator.conf"
    assert conf.read_text() == "[zram0]\nswap-priority = 100\nzram-size = min(ram / 2, 8192)\n"


# --- idempotency: written config re-plans to nothing ---------------------

def test_reapply_is_noop(tmp_path):
    a = ZramAction(_cfg(), _ctx(str(tmp_path)))
    a._set_value()
    assert a.plan(managed=[]) == []       # already converged -> no change


def test_plan_creates_when_missing(tmp_path):
    a = ZramAction(_cfg(), _ctx(str(tmp_path)))
    changes = a.plan(managed=[])
    assert changes and changes[0].op is Op.MODIFY


def test_actual_ignores_whitespace_and_order(tmp_path):
    # A hand-written conf with reversed key order + padding must be seen as
    # already-converged (canonical compare), so apply stays a no-op.
    conf = tmp_path / "etc/systemd/zram-generator.conf"
    conf.parent.mkdir(parents=True)
    conf.write_text("[zram0]\nzram-size   =   min(ram / 2, 8192)\nswap-priority=100\n")
    a = ZramAction(_cfg(), _ctx(str(tmp_path)))
    assert a.plan(managed=[]) == []


# --- sync capture: import_state reads the live file ----------------------

def test_import_state_captures_existing_conf(tmp_path):
    conf = tmp_path / "etc/systemd/zram-generator.conf"
    conf.parent.mkdir(parents=True)
    conf.write_text("[zram0]\nzram-size = min(ram / 2, 8192)\nswap-priority = 100\n")
    a = ZramAction({}, _ctx(str(tmp_path)))       # empty seed, still captures
    frag = a.import_state(managed=[])
    assert frag == {"zram": {"zram0": {"zram-size": "min(ram / 2, 8192)",
                                       "swap-priority": "100"}}}


def test_import_state_empty_when_no_conf(tmp_path):
    a = ZramAction({}, _ctx(str(tmp_path)))
    assert a.import_state(managed=[]) == {}


def test_import_state_output_reapplies_as_noop(tmp_path):
    conf = tmp_path / "etc/systemd/zram-generator.conf"
    conf.parent.mkdir(parents=True)
    conf.write_text("[zram0]\nzram-size = ram / 2\nswap-priority = 100\n")
    captured = ZramAction({}, _ctx(str(tmp_path))).import_state(managed=[])
    # feed the captured stanza back as config -> plans to nothing
    b = ZramAction(captured, _ctx(str(tmp_path)))
    assert b.plan(managed=[]) == []


# --- sync reports reality, never the config -------------------------------

def test_import_state_clears_a_declared_conf_the_machine_does_not_have(tmp_path):
    """ScalarV3Action falls back to the DESIRED value when the target reads as
    nothing. That is right where "nothing read" is a failure rather than a state
    (a machine always has a timezone) and wrong here: no
    /etc/systemd/zram-generator.conf IS the unset state, so the fallback made
    sync report a zram device nobody configured.

    The block is CLEARED, not omitted: ConfigWriter.merge overwrites keys and
    never deletes them, so an omitted block leaves the stale declaration."""
    declared = {"zram": {"zram0": {"zram-size": "ram / 2"}}}
    a = ZramAction(declared, _ctx(str(tmp_path)))

    assert a.import_state(managed=[]) == {"zram": {}}


def test_import_state_reports_the_file_over_the_config(tmp_path):
    conf = tmp_path / "etc/systemd/zram-generator.conf"
    conf.parent.mkdir(parents=True)
    conf.write_text("[zram0]\nzram-size = 4096\n")
    declared = {"zram": {"zram0": {"zram-size": "ram / 2"}}}

    frag = ZramAction(declared, _ctx(str(tmp_path))).import_state(managed=[])

    assert frag == {"zram": {"zram0": {"zram-size": "4096"}}}


def test_import_state_still_invents_nothing_on_an_undeclared_machine(tmp_path):
    """A bootstrap sync must not add an empty zram block to every config."""
    assert ZramAction({}, _ctx(str(tmp_path))).import_state(managed=[]) == {}
