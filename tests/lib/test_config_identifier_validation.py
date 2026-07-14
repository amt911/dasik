"""Config identifiers that reach a command line, a device-mapper name, or a
filesystem path must be validated at the boundary. Like package names (see
test_packages_action_validation), these come from the user's JSON and the v3
apply path does not go through the strict JsonModel, so validation lives where
each value is consumed/modelled.

- luks_name  -> `rd.luks.name=<uuid>=<name>` in the kernel cmdline + cryptsetup
- username   -> useradd argv + /etc/passwd parsing (leading '-'/':' are unsafe)
- timezone   -> symlink target /usr/share/zoneinfo/<region>/<city> (path traversal)
"""
import pytest
from pydantic import ValidationError

from dasik.lib.models.disk_model import Partition
from dasik.lib.actions.users_action import UsersAction
from dasik.lib.actions.timezone_action import TimezoneAction
from dasik.lib.exceptions.exceptions import ConfigValidationError


def _partition(**kw):
    base = dict(label="ROOT", size="rest", filesystem="ext4",
                partition_type="linux", mountpoint="/")
    base.update(kw)
    return Partition.model_validate(base)


# --- luks_name (device-mapper name -> kernel cmdline) --------------------- #

@pytest.mark.parametrize("bad", [
    "cryptroot foo=bar",       # cmdline param injection
    "root rd.break",           # extra kernel param
    "a;b", "a/b", "a=b", "a$x", "a b",
])
def test_luks_name_rejects_unsafe(bad):
    with pytest.raises(ValidationError):
        _partition(encrypt=True, luks_name=bad)


@pytest.mark.parametrize("good", ["cryptroot", "crypt_root", "luks-0", "root"])
def test_luks_name_accepts_valid(good):
    p = _partition(encrypt=True, luks_name=good)
    assert p.luks_name == good


# --- username (useradd argv + passwd parsing) ----------------------------- #

@pytest.mark.parametrize("bad", ["-rf", "a:b", "a b", "a;b", "root/x", "", "1abc"])
def test_username_rejects_unsafe(bad):
    with pytest.raises(ConfigValidationError):
        UsersAction(config={"users": [{"username": bad}]}, context=None)


@pytest.mark.parametrize("good", ["andres", "_svc", "user1", "a-b_c"])
def test_username_accepts_valid(good):
    a = UsersAction(config={"users": [{"username": good}]}, context=None)
    assert good in a._by_name


# --- timezone (zoneinfo symlink target -> path traversal) ----------------- #

@pytest.mark.parametrize("region,city", [
    ("../../etc", "shadow"),    # traversal
    ("Europe", "../.."),
    ("Europe/..", "Madrid"),
    ("Eu rope", "Madrid"),
    ("Europe", "Mad;rid"),
])
def test_timezone_rejects_traversal_or_unsafe(region, city):
    with pytest.raises(ConfigValidationError):
        TimezoneAction(config={"region": region, "city": city}, context=None)


@pytest.mark.parametrize("region,city", [
    ("Europe", "Madrid"),
    ("America", "Argentina/Buenos_Aires"),
    ("Etc", "UTC"),
])
def test_timezone_accepts_valid(region, city):
    a = TimezoneAction(config={"region": region, "city": city}, context=None)
    assert a.region == region and a.city == city
