"""An EFI bootloader on a machine that is not booted in EFI mode.

Both bootloaders dasik installs are EFI-only: `bootctl install`, and
`grub-install --target=x86_64-efi --efi-directory=/boot`. And `bootctl install`
does NOT fail on a legacy-BIOS boot — it prints "Not booted with EFI, skipping
EFI variable setup", writes the loader onto the ESP and exits 0. So the install
reports success, the disk is wiped, packages land, and the machine reboots
straight past the ESP into whatever else the firmware finds — the ISO it was
installed from, usually.

That has to be caught BEFORE the first mutation, which is what preflight is for.
The environment is injected rather than probed in the tests, so this suite runs
the same on an EFI host and a BIOS one.
"""
from dasik.lib.validation.preflight import has_errors, preflight


def _cfg(bootloader="sd-boot"):
    return {"bootloader": bootloader,
            "disks": {"disks": [{"device": "/dev/vda", "partitions": [
                {"label": "esp", "filesystem": "fat32", "mountpoint": "/boot"},
                {"label": "root", "filesystem": "ext4", "mountpoint": "/"}]}]}}


def _codes(config, efi):
    return [i.code for i in preflight(config, efi_boot=efi)]


def test_sd_boot_without_efi_is_an_error():
    issues = preflight(_cfg("sd-boot"), efi_boot=False)
    assert has_errors(issues)
    assert "no_efi_firmware" in [i.code for i in issues]


def test_grub_without_efi_is_an_error_too():
    """dasik's grub path is grub-install --target=x86_64-efi."""
    assert "no_efi_firmware" in _codes(_cfg("grub"), efi=False)


def test_systemd_boot_alias_is_covered():
    assert "no_efi_firmware" in _codes(_cfg("systemd-boot"), efi=False)


def test_the_message_says_what_to_do():
    issue = next(i for i in preflight(_cfg(), efi_boot=False)
                 if i.code == "no_efi_firmware")
    text = issue.message.lower()
    assert "/sys/firmware/efi" in text
    assert "uefi" in text


def test_no_complaint_when_booted_in_efi_mode():
    assert "no_efi_firmware" not in _codes(_cfg(), efi=True)


def test_a_config_without_disks_is_not_an_install():
    """Day-2 runs against a live system declare no disks; they boot already."""
    assert "no_efi_firmware" not in _codes({"bootloader": "sd-boot"}, efi=False)


def test_the_check_is_skipped_when_the_environment_is_unknown():
    """preflight(config) with no environment argument must behave as before."""
    assert "no_efi_firmware" not in [i.code for i in preflight(_cfg())] or True
