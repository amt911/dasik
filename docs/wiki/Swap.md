# Swap and swap encryption

dasik can declare a swap partition in one of three ways. The choice is decided
by a single question:

> **Does this machine hibernate?**

| Answer | Declare | Why |
| --- | --- | --- |
| No, and the swap must not leak | `swap_encryption: "random"` | A fresh key every boot. Nothing swapped out survives a power cycle in readable form. |
| Yes | `encrypt: true` + `luks_name` | One persistent key, unlocked in the initramfs, so `resume=` can read the hibernation image. |
| Neither matters | nothing extra | A plain swap partition. Its contents sit on disk in the clear. |

The first two are mutually exclusive by construction, and dasik refuses a config
that asks for both — see [the error below](#declaring-both-is-refused).

---

## Random key: a swap re-encrypted on every boot

```json
{
  "label": "swap",
  "size": "8GiB",
  "filesystem": "swap",
  "partition_type": "linux-swap",
  "swap_encryption": "random"
}
```

That is the whole declaration. `swap_encryption` is orthogonal to `encrypt`:
`encrypt: true` is LUKS with a key you hold, this is plain dm-crypt with a key
nobody holds — drawn from `/dev/urandom` at boot and discarded at shutdown.

### What dasik writes

Three things, and it is worth knowing why the first one exists:

1. **A 1 MiB ext2 filesystem at the front of the partition.**

   ```
   mkfs.ext2 -F -L cryptswap /dev/vda2 1M
   ```

   The swap is re-created by `mkswap` on every boot, which erases any UUID or
   label the partition had. So the partition cannot be addressed by its own
   identity — instead a tiny filesystem is placed in front of it for the sole
   purpose of carrying a persistent `LABEL`. This is the Arch wiki's
   [UUID and LABEL](https://wiki.archlinux.org/title/Dm-crypt/Swap_encryption#UUID_and_LABEL)
   procedure, and it is not cosmetic: a `swap` crypttab entry **reformats
   whatever device it resolves to**, so pointing it at `/dev/sdb2` and letting
   the kernel renumber your disks is how you lose a filesystem.

2. **The crypttab entry** (in `/etc/crypttab`):

   ```
   swap LABEL=cryptswap /dev/urandom swap,offset=2048,cipher=aes-xts-plain64,size=512,sector-size=4096
   ```

   `offset=2048` is 2048 sectors of 512 B — exactly the 1 MiB the ext2 occupies
   — so the encrypted area starts *behind* the label and never overwrites it.

3. **The fstab entry** (in `/etc/fstab`):

   ```
   /dev/mapper/swap none swap defaults 0 0
   ```

   `genfstab` cannot produce this line during the install: `/dev/mapper/swap`
   does not exist yet. It is created at the first boot, by the crypttab entry
   above.

### Names are derived, not configured

From the partition's `label`:

| `label` | mapper device | ext2 label |
| --- | --- | --- |
| `swap` | `/dev/mapper/swap` | `cryptswap` |
| `swap2` | `/dev/mapper/swap2` | `cryptswap2` |

Two random-key swaps on one machine therefore cannot collide, and a captured
config re-derives exactly what was applied.

### Who owns `/etc/crypttab`

Exactly one thing at a time:

* with `"initramfs": "dracut"`, the dracut backend composes the whole file (the
  derived LUKS root entry *and* the swap line);
* with mkinitcpio, the encrypted-swap action merges its line in.

If your config declares its own `/etc/crypttab` under `files`, dasik yields the
file to you completely — and then tells you which line you have to add, because
otherwise the swap is never opened and nothing says so. See
[Validation](Validation.md).

---

## LUKS: the swap that can hibernate

```json
{
  "label": "swap",
  "size": "32GiB",
  "filesystem": "swap",
  "partition_type": "linux-swap",
  "encrypt": true,
  "luks_name": "cryptswap",
  "luks_password": "…"
}
```

A persistent key means the hibernation image written before shutdown can still
be read at resume. dasik derives the rest: the crypttab entry gets
`x-initrd.attach` (the volume must be open *before* the real root, because
resume happens in the initramfs or not at all), and the initramfs gets the
resume module. Declaring `resume=` on the kernel cmdline is what points the
kernel at the image.

Give the swap the same passphrase as the root and systemd's password agent
reuses the one you already typed, so the extra volume costs no extra prompt.

`config/laptop-p14s.json` is a full working example.

---

## Declaring both is refused

A random-key swap next to a `resume=` parameter is not a warning, it is an
error, and `plan`/`apply` stop before touching anything:

```
[error] random_swap_hibernation: a swap declares swap_encryption='random' while
the kernel cmdline asks to resume from it (resume=/dev/mapper/swap): the random
key is discarded at shutdown, so the hibernation image can never be decrypted.
Declare the swap with `encrypt: true` (LUKS, one persistent key) to hibernate,
or drop the resume parameter.
```

It has to be an error rather than a warning because the failure is silent:
hibernating *works*. The machine writes the image, powers off, and comes back
with a fresh boot and no session — every time, with nothing in the logs that
looks like a cause.

For the same reason, a random-key swap never pulls the resume module into the
initramfs: it would only cost boot time hunting for an image that cannot exist.

---

## What `sync` captures

| On the machine | In the captured config |
| --- | --- |
| an ext2 partition named by a `/dev/urandom` + `swap` crypttab entry | that partition, as `filesystem: swap` with `swap_encryption: random` |
| a plain ext2 partition | nothing — ext2 is not a filesystem dasik represents, so the partition is skipped |
| a LUKS swap | `encrypt: true` + `luks_name`, like any other LUKS volume |

The captured label is the **mapper** name, not the ext2 one, so re-applying
derives the same pair back. `sync` → `plan` is silent.

## Related

- [Disks and encryption](Disks.md) — the rest of the partition fields
- [Boot chain](Boot.md) — how `resume=` and the initramfs fit together
- [Validation](Validation.md) — the crypttab coherence checks
