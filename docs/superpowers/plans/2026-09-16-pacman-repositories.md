# `pacman.repositories` / `pacman.keys` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Declare third-party pacman repositories and the signing keys they need, so `[amt911]` (and its key) is installed, detected, captured and removed like any other dasik domain.

**Architecture:** A pure reader/renderer module (`pacman_repos_state.py`) owns the parsing of `pacman.conf` sections and of `gpg --with-colons` output. A new v3 action (`PacmanRepositoriesAction`, domain `pacman_repositories`, items `key:<FPR>` / `repo:<name>`) plans with `compute_changes` plus MODIFY, and applies keys → sections → single-repo sync → key deletions. Capture lives in `PacmanAction._import_fragment`, because `Reconciler.sync` merges fragments by top-level key.

**Tech Stack:** Python ≥ 3.10, pydantic v2, pytest + hypothesis, mutmut, QEMU harness (`scripts/vmtest/qemu.sh`).

**Spec:** `docs/superpowers/specs/2026-09-16-pacman-repositories-design.md`

## Global Constraints

- Plans carry contracts, not literal code (AGENTS.md § Agent orchestration): exact names and signatures below are binding; implementation bodies are the implementer's, under TDD.
- Every shell-out goes through `Command.execute(cmd, args, target=t, check=...)`; config values travel as argv, never interpolated into a shell.
- New domain: `pacman_repositories`. Items: `key:<40 uppercase hex>` and `repo:<name>`.
- Declared repositories are written **above `[core]`**, in declared order.
- The database test is `/var/lib/pacman/sync/<name>.db` under the target.
- A key file whose set of PRIMARY fingerprints is not exactly `{declared}` aborts before `pacman-key --add`.
- Official names refused: `options core extra multilib core-testing extra-testing multilib-testing gnome-unstable kde-unstable testing community`.
- One VM at a time, `DASIK_VM_RAM=4096`; check `MemAvailable` ≥ 7 GiB before each start; nothing heavy (mutation) runs while a VM does.
- Never `git add -A`; stage explicit paths. Never push or merge.
- Temp files inside the target go under `/var/tmp/` unless Task 1 measures that `arch-chroot` leaves `/tmp` shared (it is expected to mount a private tmpfs).

---

### Task 1: Contract probe in a guest (measure before parsing)

**Files:**
- Create: `scripts/vmtest/guest-pacman-repo-probe.sh`
- Create: `tests/fixtures/pacman_repos/` (captured outputs, see list)
- Modify: `docs/FACTS.md` (append the measured facts)

**Interfaces:**
- Produces fixtures consumed by Tasks 3–4: `pacman.conf.stock`, `show-keys-amt911.txt`, `list-keys-absent.txt` (+ rc), `list-keys-added.txt`, `list-keys-lsigned.txt`, `list-sigs-lsigned.txt`, `list-secret-keys.txt`, `archlinux-trusted.head`, `sync-mtimes-before.txt`, `sync-mtimes-after.txt`.
- Produces FACTS lines: `FACT-PR-1` exact keyring listing command that works both on `--target /` and through `arch-chroot`; `FACT-PR-2` how "trusted" shows in colon output (validity field vs local sig class); `FACT-PR-3` whether `arch-chroot` mounts a private `/tmp`; `FACT-PR-4` whether `pacman -Sy --config <single-repo conf>` touches `core.db`/`extra.db` mtimes or deletes them; `FACT-PR-5` format of `/usr/share/pacman/keyrings/*-trusted`.

- [ ] **Step 1:** Write the probe script. It must run on the already-installed `vm-agent-ui-tooling` image (disposable, has network): for each measurement print `PROBE-<n>-BEGIN`, the raw output, `PROBE-<n>-RC=<rc>`, `PROBE-<n>-END`, so fixtures can be cut from the drive log. Measurements, in order:
  1. `cat /etc/pacman.conf`
  2. `curl -fsSL https://amt911.github.io/arch-packages/amt911.gpg -o /var/tmp/amt911.gpg` then `gpg --show-keys --with-colons /var/tmp/amt911.gpg`
  3. `gpg --homedir /etc/pacman.d/gnupg --with-colons --list-keys 6C6568CE34894645A23ABC44B5BD6F8F9023E53B` (key absent) — and the same through `pacman-key --list-keys` for comparison
  4. `pacman-key --add /var/tmp/amt911.gpg`, then measurement 3 again
  5. `pacman-key --lsign-key 6C6568CE34894645A23ABC44B5BD6F8F9023E53B`, then measurement 3 again, plus `--list-sigs` and `--list-secret-keys` in the same homedir
  6. `ls /usr/share/pacman/keyrings/` and `head -3` of each `*-trusted`
  7. `mkdir -p /mnt && mount --bind / /mnt && touch /tmp/probe-host && arch-chroot /mnt sh -c 'findmnt -n /tmp; ls /tmp/probe-host; ls /var/tmp'; umount /mnt`
  8. `stat -c '%n %Y' /var/lib/pacman/sync/*.db`; write `/var/tmp/one.conf` = the `[options]` block of `/etc/pacman.conf` + `[amt911]`/`SigLevel = Required`/`Server = https://amt911.github.io/arch-packages/$arch`; `pacman -Sy --config /var/tmp/one.conf`; `stat` again; `ls /var/lib/pacman/sync/`; `pacman --config /var/tmp/one.conf -Sl amt911 | head -3`
  9. `pacman-key --delete 6C6568CE34894645A23ABC44B5BD6F8F9023E53B`, then measurement 3 again
  End with `PROBE-DONE rc=0`, `sync`, `poweroff -f`.
- [ ] **Step 2:** Check `MemAvailable` ≥ 7 GiB and that no qemu / `http[.]server 8712` runs. Drive it: `DASIK_VM_RAM=4096 DASIK_VM_DRIVE_TIMEOUT=900 scripts/vmtest/qemu.sh drive /var/tmp/dasik-vmtest-agentui/vda.qcow2 guest-pacman-repo-probe.sh PROBE-DONE`.
- [ ] **Step 3:** Cut each block into `tests/fixtures/pacman_repos/`, strip ANSI/CR, and append FACT-PR-1..5 to `docs/FACTS.md` with "verified: guest probe, 2026-09-16".
- [ ] **Step 4:** If FACT-PR-4 shows core/extra DBs touched or deleted, STOP and report to the user: the spec's single-repo sync is wrong and needs redesign (spec § apply step 3).
- [ ] **Step 5:** Commit: `test(vm): pacman repo/key contract probe + captured fixtures`.

---

### Task 2: Model

**Files:**
- Modify: `dasik/lib/models/pacman_model.py`
- Test: `tests/lib/models/test_pacman_model.py` (new)

**Interfaces:**
- Produces: `PacmanRepositoryModel(name: str, servers: List[str] = [], include: Optional[str] = None, sig_level: Optional[str] = None)`; `PacmanKeyModel(fingerprint: str, url: Optional[str] = None)`; `PacmanModel.repositories: List[PacmanRepositoryModel]`, `PacmanModel.keys: List[PacmanKeyModel]` (both `default_factory=list`); `OFFICIAL_REPOS: frozenset[str]` exported from `pacman_model.py`.
- Both new models `extra="forbid"`. `PacmanModel` itself keeps its current (non-forbid) config.

- [ ] **Step 1: Write failing tests** (expected values by hand):

```python
import pytest
from pydantic import ValidationError
from dasik.lib.models.pacman_model import PacmanModel

FPR = "6C6568CE34894645A23ABC44B5BD6F8F9023E53B"
SRV = "https://amt911.github.io/arch-packages/$arch"

def test_defaults_are_empty_lists():
    m = PacmanModel()
    assert m.repositories == [] and m.keys == []

def test_amt911_block_validates():
    m = PacmanModel(repositories=[{"name": "amt911", "sig_level": "Required", "servers": [SRV]}],
                    keys=[{"fingerprint": FPR, "url": "https://amt911.github.io/arch-packages/amt911.gpg"}])
    assert m.repositories[0].servers == [SRV]

@pytest.mark.parametrize("name", ["core", "extra", "multilib", "options", "core-testing",
                                  "extra-testing", "multilib-testing", "gnome-unstable",
                                  "kde-unstable", "testing", "community"])
def test_official_names_refused(name):
    with pytest.raises(ValidationError):
        PacmanModel(repositories=[{"name": name, "servers": [SRV]}])

@pytest.mark.parametrize("name", ["-x", "a b", "a]b", "", "a/b"])
def test_bad_names_refused(name):
    with pytest.raises(ValidationError):
        PacmanModel(repositories=[{"name": name, "servers": [SRV]}])

def test_servers_xor_include():
    with pytest.raises(ValidationError):
        PacmanModel(repositories=[{"name": "x"}])
    with pytest.raises(ValidationError):
        PacmanModel(repositories=[{"name": "x", "servers": [SRV], "include": "/etc/pacman.d/x"}])
    assert PacmanModel(repositories=[{"name": "x", "include": "/etc/pacman.d/x"}]).repositories[0].include

@pytest.mark.parametrize("url", ["http://e/$arch", "https://u:p@e/$arch", "ftp://e", "e/$arch"])
def test_bad_servers_refused(url):
    with pytest.raises(ValidationError):
        PacmanModel(repositories=[{"name": "x", "servers": [url]}])

def test_file_server_allowed():
    PacmanModel(repositories=[{"name": "x", "servers": ["file:///srv/repo/$arch"]}])

def test_relative_include_refused():
    with pytest.raises(ValidationError):
        PacmanModel(repositories=[{"name": "x", "include": "pacman.d/x"}])

@pytest.mark.parametrize("sig", ["Required", "Optional TrustAll", "PackageRequired DatabaseOptional",
                                 "Never", "TrustedOnly"])
def test_sig_level_tokens_accepted(sig):
    PacmanModel(repositories=[{"name": "x", "servers": [SRV], "sig_level": sig}])

@pytest.mark.parametrize("sig", ["Requird", "Required; rm -rf /", ""])
def test_sig_level_garbage_refused(sig):
    with pytest.raises(ValidationError):
        PacmanModel(repositories=[{"name": "x", "servers": [SRV], "sig_level": sig}])

def test_duplicate_repo_names_refused():
    with pytest.raises(ValidationError):
        PacmanModel(repositories=[{"name": "x", "servers": [SRV]}, {"name": "x", "servers": [SRV]}])

def test_fingerprint_normalized_upper():
    assert PacmanModel(keys=[{"fingerprint": FPR.lower()}]).keys[0].fingerprint == FPR

@pytest.mark.parametrize("fpr", [FPR[:-1], FPR + "0", "Z" * 40, "B5BD6F8F9023E53B"])
def test_bad_fingerprints_refused(fpr):
    with pytest.raises(ValidationError):
        PacmanModel(keys=[{"fingerprint": fpr}])

def test_duplicate_fingerprints_refused_case_insensitively():
    with pytest.raises(ValidationError):
        PacmanModel(keys=[{"fingerprint": FPR}, {"fingerprint": FPR.lower()}])

@pytest.mark.parametrize("url", ["http://e/k.gpg", "https://u:p@e/k.gpg"])
def test_bad_key_url_refused(url):
    with pytest.raises(ValidationError):
        PacmanModel(keys=[{"fingerprint": FPR, "url": url}])

def test_unknown_field_refused():
    with pytest.raises(ValidationError):
        PacmanModel(repositories=[{"name": "x", "servers": [SRV], "usage": "All"}])
```

- [ ] **Step 2:** `.venv/bin/python -m pytest tests/lib/models/test_pacman_model.py -q` → FAIL (fields missing).
- [ ] **Step 3:** Implement in `pacman_model.py`. Mirror the validator style of `GitPackageSourceModel` (`package_model.py`: `urlsplit`, credentials refused). SigLevel grammar: each whitespace-separated token matches `(Package|Database)?(Never|Optional|Required|TrustedOnly|TrustAll)`; empty string refused.
- [ ] **Step 4:** Run the file → PASS; run `tests/lib/models/` → PASS; `dasik check config/install-megamix.json` still OK.
- [ ] **Step 5:** Commit `feat(pacman): model repositories and keys`.

---

### Task 3: `pacman.conf` sections — parse, compare, render (pure)

**Files:**
- Create: `dasik/lib/actions/pacman_repos_state.py`
- Test: `tests/lib/actions/test_pacman_repos_conf.py`

**Interfaces:**
- Consumes: `OFFICIAL_REPOS` (Task 2); fixture `tests/fixtures/pacman_repos/pacman.conf.stock` (Task 1).
- Produces:
  - `@dataclass(frozen=True) class RepoSection: name: str; sig_level: Optional[str]; servers: Tuple[str, ...]; include: Optional[str]`
  - `def parse_sections(text: str) -> List[RepoSection]` — every uncommented `[name]` except `options`, in file order, with its **contiguous** key lines (stops at the first blank or comment line).
  - `def third_party(sections: List[RepoSection]) -> List[RepoSection]` — drops `OFFICIAL_REPOS`.
  - `def below_core(text: str, name: str) -> bool` — True only when both headers exist and `name` comes after `[core]`.
  - `def render(text: str, declared: List[RepoSection], remove: Iterable[str]) -> str` — removes the sections named in `declared` ∪ `remove` (header + contiguous key lines + at most one following blank line), then inserts `declared` in order immediately above the `[core]` header (each block followed by one blank line); appends at the end when there is no `[core]`. Everything else byte-identical.
  - `def section_of(model) -> RepoSection` — from a `PacmanRepositoryModel` or its dict.

- [ ] **Step 1: Write failing tests:**

```python
from pathlib import Path
from hypothesis import given, strategies as st
from dasik.lib.actions.pacman_repos_state import (RepoSection, parse_sections, third_party,
                                                   below_core, render)

STOCK = Path("tests/fixtures/pacman_repos/pacman.conf.stock").read_text()
AMT = RepoSection("amt911", "Required", ("https://amt911.github.io/arch-packages/$arch",), None)

def test_stock_has_no_third_party():
    assert third_party(parse_sections(STOCK)) == []

def test_render_inserts_above_core_and_parses_back():
    out = render(STOCK, [AMT], remove=[])
    assert third_party(parse_sections(out)) == [AMT]
    assert out.index("[amt911]") < out.index("\n[core]")
    assert not below_core(out, "amt911")

def test_render_is_idempotent():
    once = render(STOCK, [AMT], remove=[])
    assert render(once, [AMT], remove=[]) == once

def test_render_remove_restores_stock_exactly():
    assert render(render(STOCK, [AMT], remove=[]), [], remove=["amt911"]) == STOCK

def test_hand_written_section_below_core_is_detected_and_moved():
    text = STOCK + "\n[amt911]\nSigLevel = Required\nServer = https://amt911.github.io/arch-packages/$arch\n"
    assert below_core(text, "amt911")
    out = render(text, [AMT], remove=[])
    assert not below_core(out, "amt911")
    assert out.count("[amt911]") == 1

def test_body_stops_at_comment_so_neighbouring_comments_survive():
    text = "[options]\n\n[mine]\nServer = file:///r\n#[core-testing]\n#Include = /etc/pacman.d/mirrorlist\n\n[core]\nInclude = /etc/pacman.d/mirrorlist\n"
    out = render(text, [], remove=["mine"])
    assert "#[core-testing]\n#Include = /etc/pacman.d/mirrorlist" in out
    assert "[mine]" not in out

def test_multiple_servers_and_include_parse():
    text = "[a]\nServer = https://x/$arch\nServer = https://y/$arch\n\n[b]\nInclude = /etc/pacman.d/b\n\n[core]\nInclude = /etc/pacman.d/mirrorlist\n"
    a, b = third_party(parse_sections(text))
    assert a.servers == ("https://x/$arch", "https://y/$arch") and a.sig_level is None
    assert b.include == "/etc/pacman.d/b" and b.servers == ()

def test_commented_header_is_not_a_section():
    assert third_party(parse_sections("#[amt911]\n#Server = https://x\n\n[core]\n")) == []

def test_unrelated_third_party_is_untouched_by_render():
    text = render(STOCK, [RepoSection("other", None, ("https://o/$arch",), None)], remove=[])
    out = render(text, [AMT], remove=[])
    assert [s.name for s in third_party(parse_sections(out))] == ["other", "amt911"]

_names = st.from_regex(r"[a-z][a-z0-9-]{0,8}", fullmatch=True).filter(
    lambda n: n not in {"core", "extra", "multilib", "options", "testing", "community"})

@given(st.lists(_names, unique=True, max_size=4))
def test_property_render_parse_roundtrip(names):
    declared = [RepoSection(n, "Required", (f"https://{n}.example/$arch",), None) for n in names]
    out = render(STOCK, declared, remove=[])
    assert third_party(parse_sections(out)) == declared
    assert render(out, declared, remove=[]) == out
    assert render(out, [], remove=names) == STOCK
```

- [ ] **Step 2:** Run → FAIL (module missing).
- [ ] **Step 3:** Implement. Pure: no IO, no `Command`.
- [ ] **Step 4:** Run → PASS. Confirm `test_render_remove_restores_stock_exactly` goes RED if the "at most one following blank line" removal is dropped (edit, run, restore).
- [ ] **Step 5:** Commit `feat(pacman): parse and render third-party repo sections`.

---

### Task 4: Keyring output — parse (pure)

**Files:**
- Modify: `dasik/lib/actions/pacman_repos_state.py`
- Test: `tests/lib/actions/test_pacman_repos_keyring.py`

**Interfaces:**
- Consumes: fixtures `show-keys-amt911.txt`, `list-keys-*.txt`, `list-sigs-lsigned.txt`, `list-secret-keys.txt`, `archlinux-trusted.head`; FACT-PR-2, FACT-PR-5.
- Produces:
  - `def primary_fingerprints(colons: str) -> Set[str]` — the `fpr` record that directly follows each `pub` record (uppercase); subkey `fpr` records (after `sub`) never count.
  - `def trusted_fingerprints(colons: str) -> Set[str]` — primary fingerprints the keyring trusts, by the rule FACT-PR-2 measured (validity field of `pub`, or a local signature by the master key — whichever the probe showed changes from absent/added to lsigned).
  - `def packaged_trusted(texts: Iterable[str]) -> Set[str]` — fingerprints listed in `*-trusted` files (format per FACT-PR-5), uppercase.

- [ ] **Step 1: Write failing tests** against the fixtures, expected values by hand:
  - `primary_fingerprints(show-keys-amt911) == {"6C6568CE34894645A23ABC44B5BD6F8F9023E53B"}` and `"5018DF894BFD550663C32381A917D03BA23DF1C8" not in` it.
  - `trusted_fingerprints(list-keys-added) == set()` (added, not lsigned).
  - `"6C6568CE34894645A23ABC44B5BD6F8F9023E53B" in trusted_fingerprints(list-keys-lsigned)`.
  - `trusted_fingerprints("") == set()`; garbage lines ignored.
  - `packaged_trusted([archlinux-trusted.head])` contains the first fingerprint of that fixture, written by hand into the test.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run → PASS. Prove `trusted_fingerprints` can fail: make it return all primary fingerprints, watch the "added, not lsigned" test go red, restore.
- [ ] **Step 5:** Commit `feat(pacman): read trusted keys from gpg colon output`.

---

### Task 5: `PacmanRepositoriesAction` — actual, plan, managed_keys

**Files:**
- Create: `dasik/lib/actions/pacman_repositories_action.py`
- Test: `tests/lib/actions/test_pacman_repositories_plan.py`

**Interfaces:**
- Consumes: Tasks 2–4; FACT-PR-1 (keyring listing command).
- Produces:
  - `class PacmanRepositoriesAction(AbstractAction)`, `_DOMAIN = "pacman_repositories"`, `empty_config() -> {}`, `name = "Pacman Repositories"`, `is_optional = True`.
  - Constructor reads `config` as the `pacman` block (dict or model); `self._repos: List[RepoSection]`, `self._keys: Dict[str, Optional[str]]` (fingerprint → url).
  - `def _conf_text(self) -> Optional[str]` (target `/etc/pacman.conf`), `def _db_synced(self, name) -> bool`, `def _trusted_keys(self) -> Set[str]` (FACT-PR-1 command via `Command.execute(..., target=t)`, minus `packaged_trusted` of `/usr/share/pacman/keyrings/*-trusted` under the target).
  - `def actual(self) -> set` → `{f"key:{f}" …} | {f"repo:{s.name}" for s in third_party(parse_sections(conf))}`; empty set when there is no target or no `pacman.conf`.
  - `def plan(self, managed) -> List[Change]` → shape of `McpServersAction.plan`: `compute_changes(_DOMAIN, desired=…, managed=managed, actual=…, op_install=Op.CREATE, op_remove=Op.DELETE)`, then `Op.MODIFY` for each declared `repo:` item present in actual whose state differs, with `reason` exactly one of `"section drift"`, `"below [core]"`, `"database not synced"` (checked in that order; first that applies).
  - `def managed_keys(self) -> dict` → `{_DOMAIN: sorted(desired)}`.

- [ ] **Step 1: Write failing tests** — the detectability matrix, on a `tmp_path` target with a real `etc/pacman.conf` (built with `render` from the stock fixture), `var/lib/pacman/sync/<n>.db` files, and `Command.execute` patched to return the Task-1 fixtures:
  - declared key+repo, machine has neither → `[CREATE key:FPR, CREATE repo:amt911]` (order as `compute_changes` sorts).
  - machine has both, DB present → `[]`.
  - section present but `Server` differs → `[MODIFY repo:amt911 (section drift)]`.
  - section below `[core]` → `MODIFY … (below [core])`.
  - section right, DB missing → `MODIFY … (database not synced)`.
  - key added but not lsigned (list-keys-added fixture) → `CREATE key:FPR`.
  - block empty, managed `["key:FPR","repo:amt911"]`, machine has both → `[DELETE key:FPR, DELETE repo:amt911]`.
  - block empty, managed `[]`, machine has both → `[]` (drift is never touched).
  - an `archlinux-trusted` fingerprint that is lsigned never appears in `actual()`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run → PASS; run `tests/lib/actions/` → PASS.
- [ ] **Step 5:** Commit `feat(pacman): plan third-party repositories and keys`.

---

### Task 6: `PacmanRepositoriesAction.apply`

**Files:**
- Modify: `dasik/lib/actions/pacman_repositories_action.py`
- Test: `tests/lib/actions/test_pacman_repositories_apply.py`

**Interfaces:**
- Consumes: Task 5; FACT-PR-3 (temp dir), FACT-PR-4.
- Produces: `def apply(self, changes) -> None` with the fixed order below; `class PacmanKeyMismatchError(Exception)` added to `dasik/lib/exceptions/exceptions.py`.

Order, independent of `changes` order:
1. For each `CREATE key:F`: with url → `curl -fsSL <url> -o /var/tmp/dasik-key-<F>.gpg` (check=True); `gpg --show-keys --with-colons <file>` (check=True); `primary_fingerprints(stdout) != {F}` → raise `PacmanKeyMismatchError` naming both sets; else `pacman-key --add <file>`, `pacman-key --lsign-key F` (check=True). Without url → `pacman-key --recv-keys F`, `pacman-key --lsign-key F`. The temp file is removed in `finally` (host path via `target.path`).
2. If any `repo:` change: one read of `/etc/pacman.conf`, `render(text, declared=self._repos, remove=[names of DELETE repo items])`, one write.
3. For each `CREATE`/`MODIFY repo:N`: write `/var/tmp/dasik-pacman-<N>.conf` = the `[options]` block of the (new) `pacman.conf` + that one section; `pacman -Sy --config /var/tmp/dasik-pacman-<N>.conf` (check=True, stream=True); remove the file in `finally`.
4. For each `DELETE key:F`: `pacman-key --delete F` (check=True).
`apply([])` runs nothing.

- [ ] **Step 1: Write failing tests** (`Command.execute` patched, `tmp_path` target):
  - CREATE key with url: argv sequence is exactly curl → gpg --show-keys → pacman-key --add → pacman-key --lsign-key, all with `target=` the action's target.
  - mismatch: `gpg --show-keys` returns a fixture whose primary set is `{OTHER}` → raises `PacmanKeyMismatchError`; no `pacman-key` call happened; temp file absent afterwards.
  - fixture with TWO primary keys including the declared one → still raises.
  - CREATE key without url → `--recv-keys` then `--lsign-key`, no curl.
  - CREATE repo → `pacman.conf` on disk parses back with the section above core; a `pacman -Sy --config /var/tmp/dasik-pacman-amt911.conf` call; that temp conf (captured when the mock is called) contains `[options]` and `[amt911]` and NOT `[core]`; temp conf gone afterwards.
  - keys are applied before the conf write, and the conf write before `-Sy` (record call order + conf mtime/contents at each mock call).
  - DELETE repo → section gone, stock restored byte-for-byte, no `-Sy`.
  - DELETE key → `pacman-key --delete F`.
  - `apply([])` → zero calls, `pacman.conf` untouched (mtime).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS; prove the mismatch test can fail by skipping the comparison, restore.
- [ ] **Step 5:** Commit `feat(pacman): apply repositories, trust keys, sync one repo`.

---

### Task 7: Registration, sync capture, the two matrices

**Files:**
- Modify: `dasik/lib/actions/actions_handler_v2.py` (register right after `PacmanAction`, `config_key='pacman'`, `is_optional=True`, with a comment on why it precedes Snapper/Packages)
- Modify: `dasik/lib/actions/pacman_action.py` (`_import_fragment` adds `repositories` and `keys`)
- Modify: `dasik/lib/actions/pacman_repositories_action.py` (`import_state` returns `{}` with the docstring the spec § sync asks for)
- Test: `tests/lib/test_feature_detectability.py`, `tests/lib/test_feature_sync_capture.py` (extend), `tests/lib/actions/test_pacman_repositories_sync.py` (new)

**Interfaces:**
- Consumes: Tasks 3–6.
- Produces: `PacmanAction._import_fragment` → `{"pacman": {"options": …, "multilib": …, "repositories": [ {name, servers|include, sig_level?} … ], "keys": [ {fingerprint, url?} … ]}}`. `url` copied from the seed's `pacman.keys` when the fingerprint matches, omitted otherwise. `sig_level` omitted when the section has none. Both lists are always present, and EMPTY when the machine has none, so a declared list is cleared (sync reports reality).

- [ ] **Step 1: Write failing tests:**
  - sync, machine with `[amt911]` + trusted key, seed declaring both with url → captured block equals the seed's `repositories`/`keys` exactly; `JsonModel.model_validate` accepts the captured config; `plan` of the captured config on the same machine → no `pacman_repositories` changes.
  - sync from `{}` on that machine → captures the repo and `{"fingerprint": FPR}` (no url); captured config validates.
  - machine without them, seed declaring them → captured `repositories == []` and `keys == []`.
  - capture keeps `options`/`multilib` from the machine (the fragment-collision regression): seed `Color: false`, machine `Color` active → captured `Color: true` AND repositories present.
  - `setup_actions()` registry: `PacmanRepositoriesAction` index == `PacmanAction` index + 1, and < `PackagesAction` index.
  - detectability matrix rows (spec § Ítems) added to `test_feature_detectability.py`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run `pytest -q` (whole suite) → PASS.
- [ ] **Step 5:** Commit `feat(pacman): register the domain and capture it in sync`.

---

### Task 8: Gates, mutation scope, docs, sample

**Files:**
- Modify: `pyproject.toml` (`[tool.mutmut].only_mutate` += `dasik/lib/actions/pacman_repos_state.py`)
- Modify: `docs/config-reference.md` (§ `pacman`: both lists, fields, the above-`[core]` policy, single-repo sync, key verification, sync capture incl. url-from-seed), `docs/wiki/Packages.md` (a "Your own repository" recipe with `[amt911]`)
- Modify: `docs/mutation-testing.md` (scope list)

- [ ] **Step 1:** No VM running. `rm -rf mutants && scripts/mutation.sh` → zero real survivors; any survivor in `pacman_repos_state.py` gets a killing test (not an exclusion) unless it is provably equivalent (document its diff signature per `docs/mutation-testing.md`).
- [ ] **Step 2:** `pytest --cov=dasik` ≥ 80 %, `mypy dasik` clean, `bandit -q -r dasik` clean.
- [ ] **Step 3:** Docs written; `dasik check` on every `config/*.json` still OK.
- [ ] **Step 4:** Commit `docs(pacman): repositories and keys; mutation scope`.

---

### Task 9: E2E in a guest — every verb, both targets

**Files:**
- Create: `config/vm-pacman-repo.json` (shape of `config/vm-mcp.json`: autologin drop-in, python/pydantic/colorama, systemd-networkd, `console=ttyS0`; `pacman.repositories` + `pacman.keys` for `[amt911]`; `packages` includes `config-saver`)
- Create: `scripts/vmtest/guest-pacman-repo.sh` (markers `PACREPO-…-RC=`, `PACREPO-DONE rc=N`, `rc()` counter, `PYTHONPATH=/root/repo`)

- [ ] **Step 1:** Fresh workdir. `install-driven config/vm-pacman-repo.json` with `DASIK_VM_RAM=4096 DASIK_VM_DISK=12G DASIK_VM_INSTALL_TIMEOUT=2400`. Assert in the install log: `DASIK-VM-DONE rc=0`, `config-saver` installed FROM the repo (`pacman -Si config-saver` repository `amt911` in the guest script below).
- [ ] **Step 2:** Guest script, on `--target /`:
  1. `pacman -Qi config-saver` ok and `pacman -Sl amt911` lists it; key trusted (FACT-PR-1 command); section above `[core]`.
  2. `check`, `plan` silent for `pacman_repositories`.
  3. `sync` copy → `check` → `plan` silent; `sync` from `{}` (`echo '{}' > /tmp/empty.json`) captures `amt911` and the fingerprint.
  4. `generations` lists; `state.json` contains `pacman_repositories`.
  5. Remove the section by hand + `pacman-key --delete` → `plan` shows `create key:` and `create repo:` → `apply` → `plan` silent.
  6. Move the section below `[core]` by hand → `plan` shows `below [core]` → `apply` → silent.
  7. Record `stat -c %Y` of `core.db`/`extra.db`; delete `amt911.db` → `plan` shows `database not synced` → `apply` → `amt911.db` back AND core/extra mtimes unchanged.
  8. Config with a wrong fingerprint for the same url → `apply` exits non-zero, output names both fingerprints, `pacman-key --list-keys WRONG` fails (never added).
  9. Block removed (`del cfg["pacman"]["repositories"], cfg["pacman"]["keys"]`) → `plan` shows `delete key:` and `delete repo:` → `apply` → section and key gone; `plan` silent; `pacman.conf` otherwise unchanged (`diff` against a copy taken before step 5 minus the section).
  10. `rollback` → section + key back → `plan` silent.
- [ ] **Step 3:** Drive with `DASIK_VM_DRIVE_TIMEOUT=1800` → `PACREPO-DONE rc=0`.
- [ ] **Step 4:** Prove it can fail: revert the fingerprint comparison (Task 6) and the single-repo conf (use a plain `pacman -Sy`) in the worktree, re-drive a fresh copy of the installed qcow2, watch steps 7 and 8 go non-zero, restore, re-drive green.
- [ ] **Step 5:** Commit `test(vm): pacman repositories through every verb`.

---

### Task 10: Hand-off

- [ ] **Step 1:** `superpowers:requesting-code-review` on the branch diff (a read-only review agent, Sonnet); fix confirmed findings under TDD.
- [ ] **Step 2:** `superpowers:verification-before-completion`: re-run the gates; list which verbs ran for real in the guest (all of `check plan apply sync generations rollback`, on both the chroot install and `--target /`).
- [ ] **Step 3:** Report to the user: branch name, commits, gate numbers, guest verdicts. Pushing, the PR (with **How to test manually**) and the agentic verdict comment wait for the user's go-ahead (default mode forbids pushing).
