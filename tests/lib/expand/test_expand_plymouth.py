"""The `plymouth` toggle: the package, and the daemon config when themed."""
from dasik.lib.expand import expand_config
from dasik.lib.expand.toggles import PLYMOUTHD_CONF, expand_plymouth


def test_no_block_contributes_nothing():
    assert expand_plymouth({}) == {}


def test_the_package_is_pulled_in():
    assert expand_plymouth({"plymouth": {}})["packages"] == ["plymouth"]


def test_no_theme_means_no_config_file():
    """An unset theme leaves plymouth's own default alone — writing the file
    with an empty Theme= would override it with nothing."""
    assert "files" not in expand_plymouth({"plymouth": {}})


def test_a_theme_becomes_the_daemon_config():
    files = expand_plymouth({"plymouth": {"theme": "bgrt"}})["files"]
    assert files == [{"path": PLYMOUTHD_CONF,
                      "content": "# Managed by dasik\n[Daemon]\nTheme=bgrt\n"}]


def test_the_toggle_is_wired_into_expand_config():
    assert "plymouth" in expand_config({"plymouth": {"theme": "bgrt"}})["packages"]


def test_expand_config_carries_the_theme_file_through():
    paths = [f["path"] if isinstance(f, dict) else f.path
             for f in expand_config({"plymouth": {"theme": "bgrt"}})["files"]]
    assert PLYMOUTHD_CONF in paths
