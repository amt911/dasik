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
    assert "nvidia" in pkgs
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
