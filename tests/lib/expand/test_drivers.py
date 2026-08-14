"""`expand_drivers`: the `drivers` list installs the actual GPU driver packages.

Before this toggle, `drivers: ["nvidia"]` only pulled VA-API *helper* packages
(via expand_hwaccel) — the real kernel/userspace driver (`nvidia`, `mesa`,
`vulkan-*`) was never installed, so an NVIDIA-passthrough target booted without
its driver. Package names are the canonical Arch ones (verified against the
arch-wiki NVIDIA / Hardware_video_acceleration pages). lib32 variants are added
only when multilib is enabled (they live in the [multilib] repo).
"""
from dasik.lib.expand.toggles import expand_drivers


def test_no_drivers_empty():
    assert expand_drivers({}) == {}
    assert expand_drivers({"drivers": []}) == {}


def test_nvidia_installs_driver_and_utils():
    pkgs = expand_drivers({"drivers": ["nvidia"]})["packages"]
    # `nvidia` (the proprietary module) no longer exists in the repos —
    # nvidia-open Replaces it — so the declaration can only mean the open ones.
    assert "nvidia-open" in pkgs
    assert "nvidia" not in pkgs
    assert "nvidia-utils" in pkgs
    assert "nvidia-settings" in pkgs


def test_amd_installs_mesa_and_vulkan_radeon():
    pkgs = expand_drivers({"drivers": ["amd"]})["packages"]
    assert "mesa" in pkgs
    assert "vulkan-radeon" in pkgs


def test_intel_installs_mesa_and_vulkan_intel():
    pkgs = expand_drivers({"drivers": ["intel"]})["packages"]
    assert "mesa" in pkgs
    assert "vulkan-intel" in pkgs


def test_lib32_only_with_multilib():
    # No multilib → no lib32 packages.
    plain = expand_drivers({"drivers": ["nvidia"]})["packages"]
    assert not any(p.startswith("lib32-") for p in plain)
    # multilib enabled → lib32 variant present (Steam needs 32-bit driver libs).
    ml = expand_drivers({"drivers": ["nvidia"], "pacman": {"multilib": True}})["packages"]
    assert "lib32-nvidia-utils" in ml


def test_packages_deduped_across_drivers():
    # amd + intel both pull mesa — must appear once.
    pkgs = expand_drivers({"drivers": ["amd", "intel"]})["packages"]
    assert pkgs.count("mesa") == 1


def test_unknown_driver_key_contributes_nothing():
    # A non-standard key (e.g. "nvidia_old") is not auto-mapped — no crash,
    # no packages. The user can still list the exact package in `packages`.
    assert expand_drivers({"drivers": ["nvidia_old"]}) == {}


# --- packages the repos no longer have ------------------------------------- #
#
# The failure mode is loud but late: `pacman -S nvidia` aborts the whole
# transaction with "target not found", after the disk has been partitioned.
# These pin the two names upstream retired, so a future edit cannot reintroduce
# them without a test saying why.

def test_the_retired_proprietary_nvidia_module_is_never_declared():
    """NVIDIA stopped shipping it; nvidia-open `Replaces: nvidia<=580.119.02-2`."""
    for driver in ("nvidia", "nvidia-open"):
        pkgs = expand_drivers({"drivers": [driver]})["packages"]
        assert "nvidia" not in pkgs
        assert "nvidia-dkms" not in pkgs


def test_libva_mesa_driver_is_never_declared():
    """`mesa` provides AND replaces it as of 1:24.2.7 — naming it aborts."""
    from dasik.lib.expand.toggles import expand_hwaccel

    for cfg in ({"drivers": ["amd"]},
                {"drivers": ["amd"], "hardware_acceleration": {"enable": True}}):
        pkgs = set(expand_drivers(cfg).get("packages", []))
        pkgs |= set(expand_hwaccel(cfg).get("packages", []))
        assert "libva-mesa-driver" not in pkgs
        # …and what actually ships the VA-API driver is still installed.
        assert "mesa" in pkgs


def test_a_retired_qemu_block_driver_is_never_declared():
    """`qemu-block-gluster` is gone from the Arch repos (glusterfs' block driver
    went with it). Naming a package that does not resolve aborts the whole
    pacman transaction — in phase 3, with the disk already partitioned — so the
    kvm toggle must not name it. `qemu-full` pulls whatever block drivers still
    exist.

    Caught by the CI job that resolves every declarable name, which is exactly
    the guard the 2026-08-12 package audit asked for.
    """
    from dasik.lib.expand.toggles import expand_kvm

    packages = expand_kvm({"kvm": {"install": True}})["packages"]

    assert "qemu-block-gluster" not in packages
    assert "qemu-full" in packages
