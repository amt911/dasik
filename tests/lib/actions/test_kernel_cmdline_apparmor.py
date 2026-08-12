"""AppArmor is turned on by a kernel parameter, not by a package.

`pacman -S apparmor` + `systemctl enable apparmor` leaves a machine where
`aa-enabled` says "No" and every profile is inert: the LSM has to be named in
`lsm=` at boot, with apparmor as the first *major* module. That parameter is
derived from the block, exactly as `cpu` and `sysrq` derive theirs.
"""
from dasik.lib.actions.kernel_cmdline_action import KernelCmdlineAction

_LSM = "lsm=landlock,lockdown,yama,integrity,apparmor,bpf"


def _derived(cfg):
    return KernelCmdlineAction(cfg, None)._derived()


def test_no_block_derives_no_lsm():
    assert _LSM not in _derived({})


def test_a_declared_block_makes_apparmor_the_active_lsm():
    assert _LSM in _derived({"apparmor": {}})


def test_a_disabled_block_derives_nothing():
    assert _LSM not in _derived({"apparmor": {"enable": False}})


def test_apparmor_is_the_first_major_module():
    # The order in lsm= is the initialisation order; a major module ahead of
    # apparmor takes the slot and apparmor never initialises.
    modules = _LSM.split("=", 1)[1].split(",")
    assert modules.index("apparmor") < modules.index("bpf")
    assert "capability" not in modules      # the kernel always includes it


def test_audit_adds_the_audit_parameters():
    params = _derived({"apparmor": {"audit": True}})
    assert "audit=1" in params
    assert "audit_backlog_limit=8192" in params


def test_without_audit_the_audit_parameters_stay_out():
    assert "audit=1" not in _derived({"apparmor": {}})


def test_an_explicit_lsm_wins_over_the_derived_one():
    action = KernelCmdlineAction(
        {"apparmor": {}, "kernel_cmdline": ["lsm=apparmor"]}, None)
    merged = action.desired_params
    assert "lsm=apparmor" in merged
    assert _LSM not in merged
