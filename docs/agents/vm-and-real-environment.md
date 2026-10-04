# Every change gets a VM — and what no unit test can prove

> Moved verbatim out of `AGENTS.md` on 2026-10-04 so that file fits the 32 KiB Codex reads
> by default. Its rules still bind: `AGENTS.md` lists the hard ones inline and says when to
> read this file. Edit the rule here, not a copy of it.

## Every change gets a VM

**Any change under `dasik/` is driven in a QEMU guest before it is called done.**
Not "when it looks risky", not "when it touches disks" — every change. The
harness is `scripts/vmtest/qemu.sh` (see [docs/vm-testing.md](../vm-testing.md));
the usual pair is

```bash
export DASIK_VM_ISO=/path/to/archlinux-x86_64.iso
export DASIK_VM_WORKDIR=/var/tmp/dasik-vmtest DASIK_VM_RAM=4096
scripts/vmtest/qemu.sh install-driven config/vm-<feature>.json
scripts/vmtest/qemu.sh drive $DASIK_VM_WORKDIR/vda.qcow2 guest-<feature>.sh <MARKER>-DONE
```

A change that has no config exercising it gets one (`config/vm-*.json`), and a
guest script that asserts the behaviour end to end (`scripts/vmtest/guest-*.sh`,
echoing `<MARKER>-…=rc` lines so the log is greppable). Start the VM from a
**fresh** qcow2 whenever an earlier run mutated it, or the next run is testing a
machine the previous test broke.

**Why this is not negotiable.** The bugs this repo has actually shipped were all
invisible to a green suite and to a careful reading, and every one of them was
caught by a guest:

| Bug | The suite said |
| --- | --- |
| `sync` dispossessed a declared pacman group, so removing it from the config removed nothing | 2860 passing |
| a systemd drop-in another file outranked: planned, applied, planned again, forever | passing |
| a `sync` whose output `dasik check` then rejected | passing |
| `_process_disk` skipping the format on a fresh disk → empty fstab, aborted install | passing |
| the initramfs written under a filename the bootloader does not look for | passing |

The unit suite proves the decision; the guest proves the machine. Say plainly in
the verdict which verbs ran for real in the guest and which were asserted with
mocks. `apply` inside the guest is fine — it only ever touches the guest's own
`/dev/vda`; **never** run it against the host.

## The vocabulary for those checks — and what no unit test can prove

*Every change gets a VM* (above) says **what** to do. This says **what the checks are called**, so
you can ask for one by name, and **the rules for writing one that is worth trusting**.

The reason the guest exists is that `~430` passing tests cannot see the machine. `Command.execute` is
monkeypatched, so `pacman`'s real output format, `sgdisk`'s real partition table, `arch-chroot`'s
real mount semantics and `hwclock`'s real effect never happen. The suite proves the *decision*;
nothing in it proves the *system*. Every bug in the table above lived in that gap.

| Name | What it means here |
| --- | --- |
| **E2E / in-guest acceptance test** | Drives the real `dasik` inside a QEMU guest against that guest's own `/dev/vda` and asserts on observable results — exit codes in the `<MARKER>-…=rc` lines, the fstab that was written, the packages actually installed, the bootloader entry that exists — never on internals. That is `scripts/vmtest/qemu.sh drive` plus a `guest-*.sh`. |
| **Contract test** | Checks that assumptions about **the system tooling you shell out to** actually hold — precisely what the mocks encode instead of test. Does `pacman -Qgq` still print that shape? Does `pacman -S --needed` exit 0 when nothing is needed? Does `sgdisk` renumber partitions the way the code assumes? Does `arch-chroot` inherit the mounts you think? A mock is a written-down guess about another program's behaviour; the contract test is the measurement, and it can only be taken in the guest. |
| **Mutation testing** (in-guest: by hand) | Revert the fix, re-run the guest script, confirm it goes red, restore. `mutmut`/`cosmic-ray` automate this for the pure decision logic; against a machine you do it manually. **A check that has never failed has not been tested.** |
| **State-invariant test** | Asserts a relationship **between two stores** that no unit test owns — and this is the family of bug this repo keeps shipping. A manifest/generation must agree with the system it describes; a `sync` must produce output that `dasik check` accepts; a declared group removed from the config must actually be dispossessed; an initramfs must be written under the filename the bootloader looks for. Each side is individually correct; the pair is what breaks. |
| **Test pollution / isolation leak** | A test writing to real state. Here it is not flakiness, it is a destroyed machine: `apply` outside a guest touches the host's disks. Also more quietly — reusing a qcow2 an earlier run mutated means the next run is testing a machine the previous test broke. Start from a **fresh** qcow2. |

### Rules that came out of real bugs, not theory

- **Prove every new check can fail before you trust it green.** Revert the fix, re-run the guest,
  watch the `<MARKER>-…=rc` line go non-zero, restore. This applies to unit tests written after the
  fact *and* to guest scripts. A green you have never seen turn red is not evidence — and the whole
  reason the guest exists is that the suite was green for every bug in the table above.
- **Never assert on a count you cannot predict.** "More than 5 packages installed", "the fstab has 4
  lines", "the drop-in directory has 2 files" — every one of those passes against a genuinely broken
  build as soon as the config or the mirror changes, because the magnitude depends on the input, not
  on the bug. Assert the **invariant**: the second `apply` is a no-op (idempotency is what
  `is_needed()` exists for); `sync` output survives `check`; the fstab names the UUID that
  `blkid` reports; the initramfs path is the one the bootloader entry references.
- **A manifest, generation or state marker must die with the data it describes.** A generation kept
  after the system it describes was re-partitioned makes the next `plan` reason about a machine that
  no longer exists — no crash, no log, and the diff looks plausible.
- **Never let a check touch the host.** `apply` runs **only** inside the guest, against the guest's
  own `/dev/vda`. Anything machine-global a test touches gets restored in a teardown that runs even
  when the test fails.
- **Say plainly which verbs ran for real.** In the verdict, separate what executed in the guest from
  what was asserted with mocks. A guest run that only exercised `plan` is not evidence about `apply`.
