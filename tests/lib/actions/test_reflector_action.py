"""`sync` reconstructs the `reflector` block from /etc/xdg/reflector/reflector.conf.

The conf is delivered by the expand toggle as a plain file, and file discovery
only scans the /etc directories in DropFilesAction._SECTIONS — /etc/xdg is not
one of them — so a synced config lost the mirrorlist policy entirely: the
package and the timer came back, the options did not.
"""
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.reflector_action import ReflectorAction
from dasik.lib.expand.toggles import expand_reflector
from dasik.lib.models.reflector_model import ReflectorModel
from dasik.lib.target.target import Target


def _ctx(root):
    return ActionContext(target=Target(root=str(root)))


def _conf(tmp_path, text):
    path = tmp_path / "etc/xdg/reflector"
    path.mkdir(parents=True)
    (path / "reflector.conf").write_text(text)


def _capture(tmp_path):
    return ReflectorAction({}, _ctx(tmp_path)).import_state()


def test_captures_a_dasik_written_conf(tmp_path):
    _conf(tmp_path, "# Managed by dasik\n--country ES\n--protocol https\n"
                    "--latest 20\n--sort rate\n--save /etc/pacman.d/mirrorlist\n")

    assert _capture(tmp_path)["reflector"] == {
        "countries": ["ES"],
        "protocols": ["https"],
        "latest": 20,
        "sort": "rate",
        "save": "/etc/pacman.d/mirrorlist",
    }


def test_captures_repeated_and_comma_separated_countries(tmp_path):
    """The package's own conf writes `--country France,Germany` on one line."""
    _conf(tmp_path, "--country France,Germany\n--country ES\n")

    assert _capture(tmp_path)["reflector"]["countries"] == ["France", "Germany", "ES"]


def test_accepts_the_equals_form(tmp_path):
    _conf(tmp_path, "--sort=age\n--latest=5\n")

    captured = _capture(tmp_path)["reflector"]

    assert captured["sort"] == "age"
    assert captured["latest"] == 5


def test_ignores_comments_and_blank_lines(tmp_path):
    _conf(tmp_path, "# --country Germany\n\n   \n--country ES\n")

    assert _capture(tmp_path)["reflector"]["countries"] == ["ES"]


def test_a_conf_without_latest_captures_it_as_off(tmp_path):
    """Defaulting it back to 20 would silently add a filter the machine has
    never had; None round-trips as "no --latest line"."""
    _conf(tmp_path, "--sort rate\n")

    assert _capture(tmp_path)["reflector"]["latest"] is None


def test_no_conf_captures_nothing(tmp_path):
    assert _capture(tmp_path) == {}


def test_the_captured_block_is_a_valid_reflector_declaration(tmp_path):
    _conf(tmp_path, "--country ES\n--protocol https\n--protocol rsync\n"
                    "--latest 10\n--sort age\n--save /etc/pacman.d/mirrorlist\n")

    model = ReflectorModel(**_capture(tmp_path)["reflector"])

    assert model.protocols == ["https", "rsync"]


def test_the_captured_block_rewrites_the_same_file(tmp_path):
    """Round-trip: applying a synced config must not touch the conf again."""
    original = ("# Managed by dasik\n--country ES\n--protocol https\n"
                "--latest 20\n--sort rate\n--save /etc/pacman.d/mirrorlist\n")
    _conf(tmp_path, original)

    rewritten = expand_reflector(_capture(tmp_path))["files"][0]["content"]

    assert rewritten == original


def test_capture_only_action_plans_nothing_but_is_reached_by_sync(tmp_path):
    action = ReflectorAction({"reflector": {"countries": ["ES"]}}, _ctx(tmp_path))

    assert action.plan(managed=[]) == []
    assert ReflectorAction.is_v3() is True
