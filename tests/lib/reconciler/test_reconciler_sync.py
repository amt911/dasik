from unittest.mock import MagicMock

from dasik.lib.actions.abstract_action import AbstractAction
from dasik.lib.reconciler.reconciler import Reconciler
from dasik.lib.target.target import Target


class _SyncStub(AbstractAction):
    """v3 stub with configurable actual()/import_state()/managed_keys()."""

    _actual: set = set()
    _fragment: dict = {}
    _domain: str = "packages"

    @property
    def name(self) -> str: return "sync-stub"
    def is_needed(self) -> bool: return False
    def execute(self) -> None: pass
    def plan(self, managed): return []          # marks the class as v3

    def actual(self):
        return set(type(self)._actual)

    def import_state(self, managed=None):
        return dict(type(self)._fragment)

    def managed_keys(self):
        return {type(self)._domain: []}


class _DictSyncStub(AbstractAction):
    """v3 stub whose config is dict-shaped (like TimezoneAction): its
    __init__ indexes the config, so a list-shaped empty bootstrap config
    raises ``TypeError: list indices must be integers``.
    """

    _actual: set = {"Europe/Madrid"}
    _fragment: dict = {"timezone": {"region": "Europe", "city": "Madrid"}}
    _domain: str = "timezone"

    def __init__(self, config, context=None):
        super().__init__(config, context)
        # Mirrors TimezoneAction: requires a dict-shaped config.
        self.region = config.get("region")

    @classmethod
    def empty_config(cls):          # dict-shaped domain
        return {}

    @property
    def name(self) -> str: return "dict-sync-stub"
    def is_needed(self) -> bool: return False
    def execute(self) -> None: pass
    def plan(self, managed): return []          # marks the class as v3

    def actual(self):
        return set(type(self)._actual)

    def import_state(self, managed=None):
        return dict(type(self)._fragment)

    def managed_keys(self):
        return {type(self)._domain: []}


def _meta(cls, config_key="packages"):
    return {
        "class": cls,
        "config_key": config_key,
        "is_optional": True,
        "required_fields": [],
        "depends_on": [],
    }


class _LegacyStub(AbstractAction):
    @property
    def name(self) -> str: return "legacy"
    def is_needed(self) -> bool: return False
    def execute(self) -> None: pass


def _make(*, config=None, manifest=None, metas=None, store=None):
    return Reconciler(
        config=config if config is not None else {"packages": ["git"]},
        target=Target(root="/"),
        manifest=manifest,
        action_metas=metas if metas is not None else [],
        state_store=store,
    )


def test_sync_no_v3_actions_returns_config_and_none():
    store = MagicMock()
    r = _make(metas=[_meta(_LegacyStub)], store=store)
    new_config, manifest = r.sync()
    assert new_config == {"packages": ["git"]}
    assert manifest is None
    store.save.assert_not_called()


def test_sync_merges_fragment_into_config():
    _SyncStub._actual = {"git", "htop"}
    _SyncStub._fragment = {"packages": ["git", "htop"]}
    r = _make(config={"packages": ["git"]}, metas=[_meta(_SyncStub)])
    new_config, _ = r.sync()
    assert new_config["packages"] == ["git", "htop"]


def test_sync_records_managed_as_actual():
    _SyncStub._actual = {"git", "htop", "vlc"}
    _SyncStub._fragment = {"packages": ["git", "htop", "vlc"]}
    r = _make(config={"packages": ["git"]}, metas=[_meta(_SyncStub)])
    _, manifest = r.sync()
    assert manifest.managed == {"packages": ["git", "htop", "vlc"]}  # sorted A


def test_sync_persists_manifest_via_state_store():
    _SyncStub._actual = {"git"}
    _SyncStub._fragment = {"packages": ["git"]}
    store = MagicMock()
    r = _make(config={"packages": ["git"]}, metas=[_meta(_SyncStub)], store=store)
    _, manifest = r.sync()
    store.save.assert_called_once_with(manifest)


def test_sync_does_not_bump_generation():
    _SyncStub._actual = {"git"}
    _SyncStub._fragment = {"packages": ["git"]}
    r = _make(
        config={"packages": ["git"]},
        manifest={"managed": {"packages": ["git"]}, "generation": 4},
        metas=[_meta(_SyncStub)],
    )
    _, manifest = r.sync()
    assert manifest.generation == 4  # unchanged — sync records no generation


def test_sync_bootstrap_captures_actual_when_config_section_absent():
    """Config has no 'packages' key → sync still captures reality into it."""
    _SyncStub._actual = {"git", "htop"}
    _SyncStub._fragment = {"packages": ["git", "htop"]}
    r = _make(config={"metadata": {"name": "fresh"}}, metas=[_meta(_SyncStub)])
    new_config, manifest = r.sync()
    assert new_config["packages"] == ["git", "htop"]
    assert new_config["metadata"] == {"name": "fresh"}  # passthrough
    assert manifest.managed == {"packages": ["git", "htop"]}


def test_sync_bootstrap_dict_shaped_action_when_config_section_absent():
    """A dict-shaped v3 action (e.g. TimezoneAction) with no config slice
    must bootstrap from an empty *dict*, not an empty list. Regression for
    `sync {}` raising 'list indices must be integers or slices, not str'.
    """
    r = _make(config={}, metas=[_meta(_DictSyncStub, config_key="timezone")])
    new_config, manifest = r.sync()
    assert new_config["timezone"] == {"region": "Europe", "city": "Madrid"}
    assert manifest.managed == {"timezone": ["Europe/Madrid"]}


def test_sync_sets_config_hash_of_new_config():
    import hashlib, json
    _SyncStub._actual = {"git", "htop"}
    _SyncStub._fragment = {"packages": ["git", "htop"]}
    r = _make(config={"packages": ["git"]}, metas=[_meta(_SyncStub)])
    new_config, manifest = r.sync()
    expected = hashlib.sha256(
        json.dumps(new_config, sort_keys=True).encode("utf-8")
    ).hexdigest()
    assert manifest.config_hash == expected
