#!/usr/bin/env python3
"""Drive an archiso guest install over an interactive serial socket.

archiso's ``script=`` auto-install hook does not autologin on ttyS0 with recent
ISOs (2025.12+), so the guest boots to a plain ``archiso login:`` prompt and the
installer is never fetched. ``qemu.sh install-driven`` boots the guest with its
serial exposed on a unix socket and hands it to this script, which logs in as
root and runs the same guest installer (served over HTTP by the host). It streams
the guest console to stdout and exits 0 only on ``DASIK-VM-DONE rc=0``.

qemu.sh owns the QEMU/swtpm/HTTP process lifecycle; this script only speaks to the
already-listening serial socket.

Usage: serial_driver.py <socket-path> <http-port> <timeout-seconds>
"""
import os
import select
import socket
import sys
import time


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: serial_driver.py <socket> <http_port> <timeout_s>", file=sys.stderr)
        return 2
    sock_path, port, timeout_s = sys.argv[1], sys.argv[2], int(sys.argv[3])

    # QEMU creates the socket as a listening server; connect as a client (retry
    # while the guest firmware/kernel come up).
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
        print("serial_driver: serial socket never appeared", file=sys.stderr)
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

    def wait_for(pat: str, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if pat.encode() in buf:
                return True
            pump(1.0)
        return pat.encode() in buf

    # --- log in ----------------------------------------------------------
    wait_for("archiso login:", 240)
    after_prompt = bytes(buf).rsplit(b"archiso login:", 1)[-1]
    if b"archiso login:" in buf and b"@archiso" not in after_prompt:
        sk.sendall(b"root\n")
        pump(3)
    sk.sendall(b"\n")
    pump(2)

    # --- run the installer (wait for network, fetch, run) ----------------
    cmd = (f"until curl -sf http://10.0.2.2:{port}/install.sh -o /tmp/i.sh; do "
           f"echo DRIVE-WAIT-NET; sleep 3; done; bash /tmp/i.sh\n")
    sk.sendall(cmd.encode())

    # --- stream until the guest reports DONE (it then powers off) --------
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        pump(5)
        if b"DASIK-VM-DONE" in buf:
            pump(2)
            break

    rc = 1
    for line in bytes(buf).decode("utf-8", "replace").splitlines():
        if "DASIK-VM-DONE rc=" in line:
            try:
                rc = int(line.split("rc=")[1].split()[0])
            except (ValueError, IndexError):
                rc = 1
    return 0 if rc == 0 else (rc or 1)


if __name__ == "__main__":
    sys.exit(main())
