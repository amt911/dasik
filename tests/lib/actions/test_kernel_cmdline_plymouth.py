"""A declared `plymouth` block derives `splash` on the kernel cmdline."""
from dasik.lib.actions.kernel_cmdline_action import KernelCmdlineAction


def _derived(config):
    return KernelCmdlineAction(config, None)._derive_from_plymouth()


def test_no_block_derives_nothing():
    assert _derived({}) == []


def test_the_block_derives_splash():
    assert _derived({"plymouth": {"theme": "bgrt"}}) == ["splash"]


def test_an_empty_block_still_derives_splash():
    """`"plymouth": {}` is a declaration: the splash, with plymouth's default
    theme. Only an ABSENT block means no splash."""
    assert _derived({"plymouth": {}}) == ["splash"]


def test_splash_reaches_the_full_derived_set():
    action = KernelCmdlineAction({"plymouth": {}}, None)
    assert "splash" in action._derived()
