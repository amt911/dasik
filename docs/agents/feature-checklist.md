# Feature checklist — detectable, capturable, exercised through every verb

> Moved verbatim out of `AGENTS.md` on 2026-10-04 so that file fits the 32 KiB Codex reads
> by default. Its rules still bind: `AGENTS.md` lists the hard ones inline and says when to
> read this file. Edit the rule here, not a copy of it.

## Every feature must be detectable by `plan`/`apply`

**A declared block that converges but never shows up in `dasik plan` is a bug**,
even when `apply` does the right thing: you cannot tell "already applied" from
"dasik ignores this block", and `apply` then changes the machine in ways the dry
run never announced.

Two things make this easy to get wrong:

- **A feature usually rides another domain.** `sysrq` has no `[sysrq]` line in
  the plan — it appears as `+ [kernel_cmdline] install sysrq_always_enabled=1`,
  the `cpu` block as `amd_pstate=active` on the same domain plus a package, a
  unit and `/etc/default/cpupower`, `reflector` as a `[files]` entry plus
  `reflector.timer`. That is fine. Being invisible *everywhere* is not.
- **Silence is ambiguous.** A quiet plan means "already converged" — which is
  exactly what a feature nobody looks at also produces. On a machine whose boot
  entry already carried `sysrq_always_enabled=1` (the old imperative installer's
  `enable_reisub`) the silence was correct, and indistinguishable from a bug.

So every feature needs BOTH assertions, and the disable direction where it
exists: **missing on the target ⇒ a change is planned; present ⇒ no change;
declared off but owned in the manifest ⇒ REMOVE.** An unowned parameter someone
else set is deliberately left alone. The matrix for issue #173 block A lives in
[`tests/lib/test_feature_detectability.py`](../../tests/lib/test_feature_detectability.py) —
extend it when adding a feature.

## …and capturable by `sync`

The same rule on the way back: **a feature `apply` converges but `sync` cannot
read is a one-way street.** Capture the machine, re-apply the captured config,
and the feature silently disappears — which is exactly how `sysrq`, `cpu` and
`reflector` behaved until they got an `import_state`.

Two failure modes, both silent:

- **Nothing captures it.** `reflector` wrote `/etc/xdg/reflector/reflector.conf`
  and nothing read it back (file discovery only scans `DropFilesAction._SECTIONS`,
  and /etc/xdg is not one of them), so the mirrorlist policy was lost outright.
  A feature delivered purely by an expand toggle has no owner on the way back
  until you give it one — see the CAPTURE-ONLY actions `CpuAction` /
  `ReflectorAction` (`plan()` deliberately empty; they exist so
  `Reconciler.sync`, which only visits v3 actions, reaches them).
- **It captures as noise instead of as itself.** `sysrq_always_enabled=1` and
  `amd_pstate=active` came back as hand-set `kernel_cmdline` entries, so the
  captured config described the same policy without ever growing the block that
  explains it. Parameters a block owns are subtracted by NAME in
  `KernelCmdlineAction.import_state`, whether or not the config declares the
  block.

Assert per feature: **machine has it ⇒ the declaration is captured; machine
lacks it ⇒ nothing is invented (and a declared flag is CLEARED, since sync
reports reality); the captured config validates and re-plans to nothing.** The
last one is the real invariant — `sync` → `plan` must be silent. The matrix
lives in [`tests/lib/test_feature_sync_capture.py`](../../tests/lib/test_feature_sync_capture.py).

Watch out for two legitimate reasons a key is absent from a synced config:
`subtract_contributions` strips whatever the *seed's* toggles already derive
(`systemd-boot-update.service` never gets listed because `bootloader: sd-boot`
re-derives it), and `_cmd_sync` drops newly-added empty values. Assert
reproducibility (`expand_config(captured)`), not literal presence.

## …and exercised through EVERY verb before it is called done

**A feature is not finished until it has been driven through all of
`check`, `plan`, `apply`, `sync`, `generations` and `rollback`.** The unit suite
passing is not the same thing: each verb enters the code by a different door, and
the bugs found this way were invisible to a green suite.

| Verb | What only this verb proves |
| --- | --- |
| `check` | the sample config still validates, and — the one that keeps being missed — **a config `sync` just produced does too**. A capture the tool then refuses is a broken capture. |
| `plan` | the domain is visible at all, in both directions (missing ⇒ planned, present ⇒ silent), and it **converges**: plan → apply → plan must be empty the second time. |
| `apply` | the change is written where it was announced, and re-running writes nothing. Never against real hardware — assert the intent (mocked `Command.execute`, a scratch root). |
| `sync` | the feature reads back as its own block, and nothing is invented on a machine that lacks it. |
| `generations` / `rollback` | the manifest records the domain, and a restored generation re-plans to nothing. `rollback` re-applies: same destructive limits as `apply`. |

The pairs matter more than the verbs alone — run them as round trips:
**`sync` → `check` → `plan` must end in silence**, and **`plan` → `apply` →
`plan` must too.** Real defects that only these round trips catch:

- a domain that plans a change, applies it, and plans the *same* change forever
  (a systemd drop-in that another file outranks — `apply` reported success every
  time);
- a `sync` that reported the config back instead of the machine, so the captured
  file described a setting nobody had applied;
- a `sync` whose output `check` then rejected, because the capture omitted the
  package behind an enabled unit;
- an *undeclared* domain planning a destructive MODIFY — `ln -sf
  /usr/share/zoneinfo/None/None`, or commenting out every locale — reached by
  dropping a block a previous generation owned.

That last one is the general trap: **also exercise the domain with its config
block removed.** The reconciler hands an action its *empty* config when a
previous generation owned the domain, and an empty config is not the same thing
as "the empty value".

`apply` and `rollback` are destructive and must never run for real; drive them
against a scratch root or with `Command.execute` mocked, and say plainly in the
verdict which verbs were exercised for real and which were asserted.
