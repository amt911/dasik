#!/usr/bin/env python3
"""Drive a day-2 convergence check on an already-installed guest over serial.

`qemu.sh day2` boots the installed image (OVMF, the repo on 9p, serial on a unix
socket). vm-day2.json autologins root on ttyS0, so this script waits for that root
shell, mounts the 9p repo, and runs a guest script — which re-applies configs
against the LIVE host (target /) and prints DONE-marker lines. Streams the console
and exits 0 only on "<done_marker> rc=0".

The guest script and done marker are parameters, so the same driver drives other
booted-host checks (e.g. `qemu.sh lifecycle` → guest-lifecycle.sh / LIFE-DONE).

Usage: day2_driver.py <socket-path> <timeout-seconds> [guest-script] [done-marker]
Defaults: guest-script=guest-day2.sh, done-marker=DAY2-DONE
"""
import os
import select
import socket
import sys
import time


def main() -> int:
    if not (3 <= len(sys.argv) <= 5):
        print("usage: day2_driver.py <socket> <timeout_s> [guest-script] [done-marker]",
              file=sys.stderr)
        return 2
    sock_path, timeout_s = sys.argv[1], int(sys.argv[2])
    guest_script = sys.argv[3] if len(sys.argv) >= 4 else "guest-day2.sh"
    done_marker = sys.argv[4] if len(sys.argv) >= 5 else "DAY2-DONE"

    run = (f"mkdir -p /root/repo && mount -t 9p -o trans=virtio,ro dasik /root/repo && "
           f"bash /root/repo/scripts/vmtest/{guest_script}\n")

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
        print("day2_driver: serial socket never appeared", file=sys.stderr)
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

    def wait_for(needles, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if any(n.encode() in buf for n in needles):
                return True
            pump(1.0)
        return any(n.encode() in buf for n in needles)

    # Encrypted image: the initramfs asks for the LUKS passphrase on the console
    # before the root shell exists. Unlock first if DASIK_VM_LUKS_PASSWORD is set.
    luks_pass = os.environ.get("DASIK_VM_LUKS_PASSWORD")
    if luks_pass:
        if wait_for(["passphrase", "Passphrase", "Please enter"], 180):
            pump(1.0)
            sk.sendall((luks_pass + "\n").encode())
            pump(2.0)

    # Wait for the autologin root shell (or a login prompt if autologin failed).
    wait_for(["]# ", "login:"], 240)
    mark = len(buf)
    if b"login:" in buf and b"]# " not in bytes(buf)[mark - 200:]:
        sk.sendall(b"root\n")
        pump(3)
    sk.sendall(b"\n")
    pump(2)

    sk.sendall(run.encode())

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        pump(5)
        if done_marker.encode() in buf:
            pump(2)
            break

    rc = 1
    for line in bytes(buf).decode("utf-8", "replace").splitlines():
        if f"{done_marker} rc=" in line:
            try:
                rc = int(line.split("rc=")[1].split()[0])
            except (ValueError, IndexError):
                rc = 1
    return 0 if rc == 0 else (rc or 1)


if __name__ == "__main__":
    sys.exit(main())
