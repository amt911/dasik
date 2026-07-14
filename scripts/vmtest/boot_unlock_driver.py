#!/usr/bin/env python3
"""Drive a LUKS boot-unlock over serial for an encrypted installed guest.

`qemu.sh boot-unlock` boots an encrypted image (OVMF, serial on a unix socket).
The initramfs' sd-encrypt hook asks for the LUKS passphrase on the console
(ttyS0, because vm-luks.json sets console=ttyS0). This driver waits for that
prompt, types the passphrase, and then waits for a boot/login marker — proving
the encrypted root unlocks with the declared passphrase and the system boots.

Streams the console; exits 0 only if a login/boot marker is seen AFTER the
passphrase was sent. If the prompt never appears, or boot never completes, it
exits non-zero.

Usage: boot_unlock_driver.py <socket-path> <passphrase> <timeout-seconds>
"""
import os
import select
import socket
import sys
import time

PROMPT_NEEDLES = ["passphrase", "Passphrase", "Please enter", "Enter passphrase", "unlocking"]
BOOT_NEEDLES = ["login:", "reached target", "Reached target", "Welcome to", "systemd[1]"]


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: boot_unlock_driver.py <socket> <passphrase> <timeout_s>", file=sys.stderr)
        return 2
    sock_path, passphrase, timeout_s = sys.argv[1], sys.argv[2], int(sys.argv[3])

    sk = None
    for _ in range(120):
        if os.path.exists(sock_path):
            try:
                sk = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sk.connect(sock_path)
                break
            except OSError:
                time.sleep(0.5)
        else:
            time.sleep(0.5)
    if sk is None:
        print("boot_unlock_driver: serial socket never appeared", file=sys.stderr)
        return 2
    sk.setblocking(False)

    buf = bytearray()

    def pump(seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            r, _, _ = select.select([sk], [], [], 0.5)
            if not r:
                continue
            try:
                data = sk.recv(65536)
            except OSError:
                return
            if not data:
                return
            sys.stdout.buffer.write(data)
            sys.stdout.flush()
            buf.extend(data)

    def seen(needles) -> bool:
        return any(n.encode() in buf for n in needles)

    # 1. Wait for the passphrase prompt from the initramfs.
    deadline = time.time() + timeout_s
    while time.time() < deadline and not seen(PROMPT_NEEDLES):
        pump(1.0)
    if not seen(PROMPT_NEEDLES):
        print("\nboot_unlock_driver: passphrase prompt never appeared", file=sys.stderr)
        return 3

    # 2. Type the passphrase. Give the ask-password agent a moment, then send it.
    #    Send a couple of times spaced out in case the first lands before the
    #    agent is reading; a superfluous line is harmless at a login prompt.
    pump(1.0)
    sk.sendall((passphrase + "\n").encode())
    boot_start = len(buf)

    # 3. Wait for a boot/login marker that appears AFTER the passphrase was sent.
    while time.time() < deadline:
        pump(2.0)
        if any(n.encode() in bytes(buf)[boot_start:] for n in BOOT_NEEDLES):
            pump(1.0)
            print("\nboot_unlock_driver: boot marker seen after unlock — PASS")
            return 0

    print("\nboot_unlock_driver: no boot marker after unlock within timeout", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
