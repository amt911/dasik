"""mkinitcpio needs the `resume` hook for hibernation — including when encrypted.

dracut got its module in the previous branch. mkinitcpio is dasik's DEFAULT
generator and was worse off: the encrypted rewrite actively *stripped* `resume`
from HOOKS (`elif h in ("usr", "resume", "consolefont"): continue`), so a
mkinitcpio config that declared hibernation had the one hook it needed removed.

Per mkinitcpio(8)'s hook table the hook is what "adds the systemd-hibernate-resume
binary" on the systemd path and what "tries to resume from the suspend-to-disk
state" on the busybox one — it is required either way. It goes before
`filesystems`: resuming after the real root is mounted is how a filesystem gets
eaten.
"""
from dasik.lib.actions.initramfs.mkinitcpio import MkinitcpioBackend
from dasik.lib.target.target import Target


def _cfg(*, swap=False, encrypt=False, resume_param=False, fs="ext4"):
    parts = [{"label": "root", "filesystem": fs, "mountpoint": "/"}]
    if encrypt:
        parts[0].update({"encrypt": True, "luks_name": "cryptroot"})
    if swap:
        parts.insert(0, {"label": "swap", "filesystem": "swap"})
    cfg = {"disks": {"disks": [{"partitions": parts}]}}
    if resume_param:
        cfg["kernel_cmdline"] = ["resume=/dev/mapper/cryptswap"]
    return cfg


def _hooks(cfg):
    b = MkinitcpioBackend(cfg, Target(root="/"))
    return b.desired_value().split()


def test_hibernation_adds_the_resume_hook():
    assert "resume" in _hooks(_cfg(swap=True))


def test_no_hibernation_no_resume_hook():
    assert "resume" not in _hooks(_cfg())


def test_encrypted_hibernation_keeps_the_hook_instead_of_stripping_it():
    hooks = _hooks(_cfg(swap=True, encrypt=True))
    assert "resume" in hooks
    assert "sd-encrypt" in hooks          # the encrypted rewrite still applies


def test_resume_comes_before_filesystems():
    hooks = _hooks(_cfg(swap=True, encrypt=True))
    assert hooks.index("resume") < hooks.index("filesystems")


def test_resume_comes_after_the_hook_that_opens_the_device():
    hooks = _hooks(_cfg(swap=True, encrypt=True))
    assert hooks.index("resume") > hooks.index("sd-encrypt")


def test_a_resume_parameter_alone_is_enough():
    assert "resume" in _hooks(_cfg(resume_param=True))


def test_the_hook_is_not_duplicated():
    hooks = _hooks(_cfg(swap=True, encrypt=True, resume_param=True))
    assert hooks.count("resume") == 1
