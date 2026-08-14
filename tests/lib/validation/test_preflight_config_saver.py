"""Preflight: a config-saver timer that has nothing to save.

config-saver 3.3.0 stopped falling back to the examples the package ships: with
no configuration in either active level (/etc/config-saver/configs, written by
dasik, and ~/.config/config-saver/configs.d, the user's own) it exits 6 with
"No configurations found". Before 3.3.0 the same config "worked" — it archived
default-config/etc-files/wallpapers/zsh, i.e. somebody else's examples, which
reach ~/.ssh and ~/.config/rclone.

So `timer_users` without `configs` now means a timer that fails on every fire.
It is a warning rather than an error because the user level is real: a
configuration in the user's own ~/.config/config-saver/configs.d satisfies
config-saver and is invisible to dasik.
"""
from dasik.lib.validation.preflight import preflight


def _warnings(cfg):
    return [i for i in preflight(cfg) if i.level == "warning"]


def _errors(cfg):
    return [i for i in preflight(cfg) if i.level == "error"]


def test_timer_users_without_any_config_warns():
    cfg = {"packages": ["config-saver"],
           "config_saver": {"timer_users": ["andres"]}}
    warns = [w for w in _warnings(cfg) if w.code == "config_saver_timer_without_configs"]
    assert len(warns) == 1
    assert "andres" in warns[0].message
    assert _errors(cfg) == []


def test_timer_users_with_a_declared_config_is_accepted():
    cfg = {"packages": ["config-saver"],
           "config_saver": {
               "configs": {"dotfiles": {"directories": ["$HOME/.config"]}},
               "timer_users": ["andres"]}}
    assert [w for w in _warnings(cfg)
            if w.code == "config_saver_timer_without_configs"] == []


def test_configs_without_a_timer_is_accepted():
    """`config-saver --compress` by hand is a legitimate way to use it."""
    cfg = {"packages": ["config-saver"],
           "config_saver": {"configs": {"dotfiles": {"directories": ["$HOME"]}}}}
    assert [w for w in _warnings(cfg)
            if w.code == "config_saver_timer_without_configs"] == []


def test_empty_block_installs_the_package_and_warns_about_nothing():
    """An empty block still means something: install config-saver."""
    cfg = {"packages": ["config-saver"], "config_saver": {}}
    assert [w for w in _warnings(cfg)
            if w.code == "config_saver_timer_without_configs"] == []


def test_no_config_saver_block_is_silent():
    cfg = {"packages": ["firefox"]}
    assert [w for w in _warnings(cfg)
            if w.code == "config_saver_timer_without_configs"] == []
