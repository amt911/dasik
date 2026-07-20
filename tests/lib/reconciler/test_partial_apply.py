"""A failed apply must record what it DID do — as a partial, non-converged state.

F-01: on 2026-07-19 the reconciler mutated the disk, installed the base system
and most packages, then raised. Because the manifest and the generation are only
written after the whole loop, nothing at all was recorded: no generation, no
ownership, no trace of where it stopped.

The rules the tests below pin down:

* a partial state IS persisted (state + generation), so `generations` shows what
  happened and when;
* it is flagged ``partial`` and never claims the domains of actions that failed
  or were never reached — those carry the PREVIOUS manifest's entries forward
  (we do not know they changed, and forgetting ownership must never turn into a
  spurious removal);
* the original exception still propagates: apply failed, and the CLI must say so;
* a partial generation cannot be rolled back TO — it is not a converged state.
"""
from types import SimpleNamespace

import pytest

from dasik.lib.exceptions.exceptions import CommandExecutionError
from dasik.lib.reconciler.reconciler import ActionPlanResult, Reconciler
from dasik.lib.state.change import Change, Op, Plan
from dasik.lib.state.state_store import Manifest
from dasik.lib.target.target import Target


class _FakeAction:
    """Minimal v3 action double: one domain, optionally raising on apply."""

    def __init__(self, domain, items, boom=False):
        self._domain = domain
        self._items = list(items)
        self._boom = boom
        self.applied = False

    def apply(self, changes):
        if self._boom:
            raise CommandExecutionError(f"{self._domain} failed (exit 1)")
        self.applied = True

    def managed_keys(self):
        return {self._domain: list(self._items)}

    def state_metadata(self):
        return {}


class _Store:
    def __init__(self):
        self.saved = None

    def save(self, manifest):
        self.saved = manifest


class _GenStore:
    def __init__(self):
        self.records = []

    def new(self, config, manifest_dict):
        self.records.append((config, manifest_dict))
        return len(self.records)


def _plan_with(*items):
    plan = Plan()
    for domain, item in items:
        plan.add(Change(domain, Op.INSTALL, item))
    return plan


def _reconciler(manifest=None, state_store=None, gen_store=None):
    return Reconciler(
        config={"packages": ["git"]},
        target=Target(root="/mnt"),
        manifest=manifest,
        action_metas=[],
        state_store=state_store,
        generation_store=gen_store,
    )


def _run(results, manifest=None):
    state, gens = _Store(), _GenStore()
    rec = _reconciler(manifest=manifest, state_store=state, gen_store=gens)
    plan = _plan_with(("packages", "git"))
    with pytest.raises(CommandExecutionError):
        rec.apply(plan, results, assume_yes=True)
    return state, gens


def test_failed_apply_persists_the_completed_actions():
    done = _FakeAction("timezone", ["Europe/Madrid"])
    boom = _FakeAction("packages", ["git"], boom=True)
    state, _ = _run([ActionPlanResult(done, []), ActionPlanResult(boom, [])])
    assert state.saved is not None, "a failed apply recorded nothing at all"
    assert state.saved.managed["timezone"] == ["Europe/Madrid"]
    assert state.saved.partial is True


def test_failed_action_domain_is_not_claimed():
    boom = _FakeAction("packages", ["git", "vim"], boom=True)
    state, _ = _run([ActionPlanResult(boom, [])])
    assert "packages" not in state.saved.managed


def test_unreached_actions_keep_the_previous_ownership():
    """Forgetting ownership is not free: M is what a later plan diffs against."""
    previous = {"generation": 3, "managed": {"packages": ["git"],
                                             "systemd": ["sshd.service"]}}
    boom = _FakeAction("packages", ["git", "vim"], boom=True)
    never = _FakeAction("systemd", ["sshd.service", "cups.service"])
    state, _ = _run([ActionPlanResult(boom, []), ActionPlanResult(never, [])],
                    manifest=previous)
    assert never.applied is False
    assert state.saved.managed["packages"] == ["git"]          # previous, not desired
    assert state.saved.managed["systemd"] == ["sshd.service"]  # previous, not desired


def test_partial_generation_is_recorded():
    boom = _FakeAction("packages", ["git"], boom=True)
    _, gens = _run([ActionPlanResult(boom, [])])
    assert len(gens.records) == 1
    _config, manifest_dict = gens.records[0]
    assert manifest_dict["partial"] is True
    assert manifest_dict["generation"] == 1


def test_partial_generation_number_follows_the_previous_one():
    boom = _FakeAction("packages", ["git"], boom=True)
    state, _ = _run([ActionPlanResult(boom, [])],
                    manifest={"generation": 7, "managed": {}})
    assert state.saved.generation == 8


def test_successful_apply_is_not_partial():
    ok = _FakeAction("packages", ["git"])
    state, gens = _Store(), _GenStore()
    rec = _reconciler(state_store=state, gen_store=gens)
    manifest = rec.apply(_plan_with(("packages", "git")),
                         [ActionPlanResult(ok, [])], assume_yes=True)
    assert manifest.partial is False
    assert state.saved.partial is False


def test_manifest_round_trips_the_partial_flag():
    m = Manifest(generation=2, partial=True, managed={"packages": ["git"]})
    assert Manifest.from_dict(m.to_dict()).partial is True
    # pre-existing manifests have no such key and must load as complete
    assert Manifest.from_dict({"generation": 1}).partial is False
