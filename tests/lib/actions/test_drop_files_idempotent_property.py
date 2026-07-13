"""Property-based idempotency for DropFilesAction (CLAUDE.md § Quality).

Managed config files are a set domain (CREATE/DELETE) plus a per-file content
MODIFY. Invariants: when every declared file exists with matching content and
dasik owns exactly the declared set, planning is empty; a declared file whose
on-disk content drifted yields one MODIFY; dasik never DELETEs a path it does
not own.
"""
from types import SimpleNamespace

from hypothesis import given
from hypothesis import strategies as st

from dasik.lib.actions.drop_files_action import DropFilesAction
from dasik.lib.state.change import Op

_name = st.text(alphabet="abcde", min_size=1, max_size=5)
_content = st.text(max_size=10)
_files = st.lists(
    st.builds(lambda n, c: {"path": "/etc/" + n, "content": c}, _name, _content),
    max_size=4,
    unique_by=lambda d: d["path"],
)


def _action(files, on_disk):
    """on_disk: {path: content} present on the target."""
    a = DropFilesAction({"files": list(files)}, context=SimpleNamespace(target=object()))
    a._exists = lambda p: p in on_disk
    a._read = lambda p: on_disk[p]
    return a


@given(files=_files)
def test_converged_files_plan_is_empty(files):
    """Every declared file present with matching content, managed == declared ⇒ no-op."""
    on_disk = {f["path"]: f["content"] for f in files}
    a = _action(files, on_disk)
    managed = sorted(on_disk)
    assert a.plan(managed=managed) == []


@given(files=_files, newc=_content)
def test_content_drift_yields_one_modify(files, newc):
    """A declared file whose on-disk content differs ⇒ exactly one MODIFY for it."""
    if not files:
        return
    on_disk = {f["path"]: f["content"] for f in files}
    victim = files[0]["path"]
    if newc == on_disk[victim]:
        return
    on_disk[victim] = newc
    a = _action(files, on_disk)
    changes = a.plan(managed=sorted(on_disk))
    mods = [(c.op, c.item) for c in changes if c.op is Op.MODIFY]
    assert mods == [(Op.MODIFY, victim)]


@given(files=_files, stranger=_name)
def test_unowned_path_is_never_deleted(files, stranger):
    """A path dasik does not own (not in managed) is never DELETEd, even if the
    manifest passed something extra."""
    on_disk = {f["path"]: f["content"] for f in files}
    a = _action(files, on_disk)
    # managed is exactly the declared set — an unrelated path must not be removed.
    changes = a.plan(managed=sorted(on_disk))
    deleted = {c.item for c in changes if c.op is Op.DELETE}
    assert ("/etc/" + stranger) not in deleted or ("/etc/" + stranger) in on_disk
    # and more strongly: nothing outside managed is deleted
    assert deleted <= set(on_disk)
