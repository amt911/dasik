"""kvm toggle grants the libvirt group to every declared user (expand).

Previously the kvm section installed qemu/libvirt + enabled the daemon but the
user was never added to the `libvirt` group, so virt-manager needed root. The
toggle now contributes `user_groups: ["libvirt"]`, which expand_config merges
into each user's groups (UsersAction then reconciles it idempotently), and
subtract_contributions removes on sync so it's attributed to the toggle.
"""
from dasik.lib.expand import expand_config, subtract_contributions
from dasik.lib.expand.toggles import expand_kvm


def test_kvm_toggle_contributes_libvirt_group():
    out = expand_kvm({"kvm": {"install": True}})
    assert out["user_groups"] == ["libvirt"]


def test_expand_adds_libvirt_to_every_user():
    cfg = {
        "kvm": {"install": True},
        "users": [
            {"username": "alice", "groups": ["wheel"]},
            {"username": "bob"},
        ],
    }
    out = expand_config(cfg)
    by = {u["username"]: u for u in out["users"]}
    assert "libvirt" in by["alice"]["groups"] and "wheel" in by["alice"]["groups"]
    assert by["bob"]["groups"] == ["libvirt"]


def test_expand_is_idempotent_no_duplicate_group():
    cfg = {"kvm": {"install": True},
           "users": [{"username": "alice", "groups": ["libvirt", "wheel"]}]}
    out = expand_config(cfg)
    assert out["users"][0]["groups"].count("libvirt") == 1


def test_no_kvm_leaves_users_untouched():
    cfg = {"users": [{"username": "alice", "groups": ["wheel"]}]}
    out = expand_config(cfg)
    assert out["users"][0]["groups"] == ["wheel"]


def test_subtract_removes_toggle_group_but_keeps_declared():
    original = {"kvm": {"install": True},
                "users": [{"username": "alice", "groups": ["wheel"]},
                          {"username": "bob", "groups": ["libvirt"]}]}
    expanded = expand_config(original)
    back = subtract_contributions(expanded, original)
    by = {u["username"]: u for u in back["users"]}
    # alice: libvirt was toggle-added → removed; wheel stays
    assert by["alice"]["groups"] == ["wheel"]
    # bob declared libvirt himself → kept
    assert "libvirt" in by["bob"]["groups"]
