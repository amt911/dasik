"""A random-key swap and hibernation are mutually exclusive, provably.

The key is drawn from /dev/urandom at every boot and discarded at shutdown, so a
resume image written with the previous key can never be decrypted. That is an
error rather than a warning because the failure mode is silent: hibernation
succeeds, and the session is lost on the way back.
"""
from dasik.lib.validation.preflight import preflight


def _cfg(**over):
    cfg = {"disks": {"disks": [{"device": "/dev/vda", "partitions": [
        {"label": "swap", "size": "2GiB", "filesystem": "swap",
         "swap_encryption": "random"}]}]}}
    cfg.update(over)
    return cfg


def _codes(issues, level):
    return {i.code for i in issues if i.level == level}


def _errors(config):
    return _codes(preflight(config, efi_boot=True), "error")


def test_a_random_swap_with_a_resume_parameter_is_an_error():
    assert "random_swap_hibernation" in _errors(
        _cfg(kernel_cmdline=["resume=/dev/mapper/swap"]))


def test_the_message_says_which_parameter_and_what_to_do_instead():
    issues = preflight(_cfg(kernel_cmdline=["resume=/dev/mapper/swap"]), efi_boot=True)
    message = next(i.message for i in issues if i.code == "random_swap_hibernation")
    assert "resume=/dev/mapper/swap" in message
    assert "encrypt" in message          # points at the LUKS swap that does work


def test_a_random_swap_alone_is_accepted():
    assert "random_swap_hibernation" not in _errors(_cfg())


def test_a_plain_swap_with_resume_is_left_alone():
    cfg = {"kernel_cmdline": ["resume=/dev/vda2"],
           "disks": {"disks": [{"device": "/dev/vda", "partitions": [
               {"label": "swap", "size": "2GiB", "filesystem": "swap"}]}]}}
    assert "random_swap_hibernation" not in _errors(cfg)


def test_a_verbatim_crypttab_that_omits_the_derived_line_is_an_error():
    # dasik yields /etc/crypttab to the config when the config declares it, so
    # the swap would never be opened — silently.
    assert "random_swap_crypttab_conflict" in _errors(
        _cfg(files=[{"path": "/etc/crypttab", "content": "# nothing here\n"}]))


def test_a_verbatim_crypttab_that_carries_the_entry_is_fine():
    cfg = _cfg(files=[{"path": "/etc/crypttab",
                       "content": "swap LABEL=cryptswap /dev/urandom "
                                  "swap,offset=2048\n"}])
    assert "random_swap_crypttab_conflict" not in _errors(cfg)


def test_the_derived_label_is_not_reported_as_an_undeclared_device():
    # crypttab_undeclared_device is an ERROR for a `swap` entry (it reformats
    # what it names), so dasik's own derived label must count as declared.
    cfg = _cfg(files=[{"path": "/etc/crypttab",
                       "content": "swap LABEL=cryptswap /dev/urandom "
                                  "swap,offset=2048\n"}])
    assert "crypttab_undeclared_device" not in _errors(cfg)


def test_an_undeclared_label_is_still_reported():
    cfg = _cfg(files=[{"path": "/etc/crypttab",
                       "content": "swap LABEL=cryptswap /dev/urandom swap,offset=2048\n"
                                  "other LABEL=nosuchthing /dev/urandom swap\n"}])
    assert "crypttab_undeclared_device" in _errors(cfg)
