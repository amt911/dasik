#!/usr/bin/env python3
"""Drive a day-2 convergence check on an already-installed guest over serial.

`qemu.sh day2` boots the installed image (OVMF, the repo on 9p, serial on a unix
socket). vm-day2.json autologins root on ttyS0, so this script waits for that root
shell, mounts the 9p repo, and runs guest-day2.sh — which re-applies configs
against the LIVE host (target /) and prints DAY2-* markers. Streams the console and
exits 0 only on DAY2-DONE rc=0.

Usage: day2_driver.py <socket-path> <timeout-seconds>
"""
import os
import select
import socket
import sys
import time

RUN = ("mkdir -p /root/repo && mount -t 9p -o trans=virtio,ro dasik /root/repo && "
       "bash /root/repo/scripts/vmtest/guest-day2.sh\n")


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: day2_driver.py <socket> <timeout_s>", file=sys.stderr)
        return 2
    sock_path, timeout_s = sys.argv[1], int(sys.argv[2])

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

    # Wait for the autologin root shell (or a login prompt if autologin failed).
    wait_for(["]# ", "login:"], 240)
    mark = len(buf)
    if b"login:" in buf and b"]# " not in bytes(buf)[mark - 200:]:
        sk.sendall(b"root\n")
        pump(3)
    sk.sendall(b"\n")
    pump(2)

    sk.sendall(RUN.encode())

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        pump(5)
        if b"DAY2-DONE" in buf:
            pump(2)
            break

    rc = 1
    for line in bytes(buf).decode("utf-8", "replace").splitlines():
        if "DAY2-DONE rc=" in line:
            try:
                rc = int(line.split("rc=")[1].split()[0])
            except (ValueError, IndexError):
                rc = 1
    return 0 if rc == 0 else (rc or 1)


if __name__ == "__main__":
    sys.exit(main())
