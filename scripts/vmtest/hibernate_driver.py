#!/usr/bin/env python3
"""Prove hibernate -> resume on an installed, encrypted dasik guest.

Two QEMU boots on the SAME image:

  boot 1  unlock LUKS -> assert the cmdline/swap/CanHibernate preconditions,
          stamp a marker in /run (tmpfs: restored by a resume, GONE after a cold
          boot) plus the boot_id, then `systemctl hibernate` (guest powers off).
  boot 2  unlock LUKS -> if the /run marker and the boot_id are still there, the
          kernel restored the hibernation image instead of booting fresh.

Usage: hibernate_driver.py <image.qcow2> <passphrase> <repo-root> [ram] [cpus]
"""
import os
import select
import shutil
import socket
import subprocess
import sys
import time

MARKER = "/run/dasik-hibernation-marker"
OVMF_CODE = ["/usr/share/edk2/x64/OVMF_CODE.4m.fd", "/usr/share/OVMF/OVMF_CODE.fd",
             "/usr/share/ovmf/x64/OVMF_CODE.fd", "/usr/share/edk2-ovmf/x64/OVMF_CODE.fd"]
OVMF_VARS = ["/usr/share/edk2/x64/OVMF_VARS.4m.fd", "/usr/share/OVMF/OVMF_VARS.fd",
             "/usr/share/ovmf/x64/OVMF_VARS.fd", "/usr/share/edk2-ovmf/x64/OVMF_VARS.fd"]


def _first(paths):
    for p in paths:
        if os.path.isfile(p):
            return p
    raise SystemExit("OVMF firmware not found")


def _value(transcript, key):
    """Value of the last `echo KEY=<value>` OUTPUT line in *transcript*.

    The guest shell echoes the command back before running it, so the line that
    merely CONTAINS `KEY=` may be the command itself; the real output line is
    the one that does not also carry the surrounding `echo`/`cat` text.
    """
    found = ""
    for line in transcript.splitlines():
        line = line.strip()
        if key in line and "echo " not in line and "cat " not in line:
            found = line.split(key, 1)[1].split()[0] if line.split(key, 1)[1].split() else ""
    return found


class Guest:
    """One QEMU boot with the serial console on a unix socket."""

    def __init__(self, image, repo, work, ram, cpus, tag):
        self.image, self.repo, self.work, self.tag = image, repo, work, tag
        self.sock_path = os.path.join(work, f"hib-{tag}.sock")
        self.varsf = os.path.join(work, f"OVMF_VARS-{tag}.fd")
        self.ram, self.cpus = ram, cpus
        self.buf = bytearray()
        self.proc = None
        self.sk = None

    def start(self):
        if os.path.exists(self.sock_path):
            os.unlink(self.sock_path)
        shutil.copyfile(_first(OVMF_VARS), self.varsf)
        argv = [
            "qemu-system-x86_64", "-enable-kvm", "-cpu", "host",
            "-m", str(self.ram), "-smp", str(self.cpus),
            "-display", "none", "-monitor", "none",
            "-drive", f"if=pflash,unit=0,format=raw,readonly=on,file={_first(OVMF_CODE)}",
            "-drive", f"if=pflash,unit=1,format=raw,file={self.varsf}",
            "-drive", f"file={self.image},if=virtio,format=qcow2", "-boot", "c",
            "-netdev", "user,id=n0", "-device", "virtio-net,netdev=n0",
            "-serial", f"unix:{self.sock_path},server,nowait", "-no-reboot",
        ]
        self.proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
        for _ in range(120):
            if os.path.exists(self.sock_path):
                try:
                    self.sk = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    self.sk.connect(self.sock_path)
                    break
                except OSError:
                    pass
            time.sleep(0.5)
        if self.sk is None:
            raise SystemExit(f"[{self.tag}] serial socket never appeared")
        self.sk.setblocking(False)

    def pump(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            r, _, _ = select.select([self.sk], [], [], 0.5)
            if not r:
                continue
            try:
                data = self.sk.recv(65536)
            except OSError:
                return
            if not data:
                return
            sys.stdout.buffer.write(data)
            sys.stdout.flush()
            self.buf.extend(data)

    def wait_for(self, needles, timeout, since=0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if any(n.encode() in bytes(self.buf)[since:] for n in needles):
                return True
            self.pump(1.0)
        return any(n.encode() in bytes(self.buf)[since:] for n in needles)

    def send(self, line):
        self.sk.sendall((line + "\n").encode())

    def unlock_and_login(self, passphrase):
        if not self.wait_for(["passphrase", "Passphrase", "Please enter"], 240):
            raise SystemExit(f"[{self.tag}] LUKS prompt never appeared")
        prompts_before = self.text().count("assphrase")
        self.pump(1.0)
        self.send(passphrase)
        self.pump(5.0)
        # A second device asking would show another prompt; systemd's password
        # cache is supposed to reuse the first answer for cryptswap.
        self.wait_for(["]# ", "login:"], 300)
        self.pump(2.0)
        if b"login:" in bytes(self.buf) and b"]# " not in bytes(self.buf)[-400:]:
            self.send("root")
            self.pump(5.0)
        self.send("")
        self.pump(2.0)
        return prompts_before

    def run(self, cmd, settle=6.0):
        self.send(cmd)
        self.pump(settle)

    def text(self):
        return bytes(self.buf).decode("utf-8", "replace")

    def stop(self):
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.kill()
        finally:
            if self.sk:
                self.sk.close()

    def wait_exit(self, timeout):
        end = time.time() + timeout
        while time.time() < end:
            if self.proc.poll() is not None:
                return True
            self.pump(2.0)
        return self.proc.poll() is not None


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    image, passphrase, repo = sys.argv[1], sys.argv[2], sys.argv[3]
    ram = sys.argv[4] if len(sys.argv) > 4 else "4096"
    cpus = sys.argv[5] if len(sys.argv) > 5 else "4"
    work = os.path.dirname(os.path.abspath(image))

    # ---------------- boot 1: assert preconditions, then hibernate ----------
    print("\n=== BOOT 1: preconditions + hibernate ===", flush=True)
    g1 = Guest(image, repo, work, ram, cpus, "b1")
    g1.start()
    try:
        g1.unlock_and_login(passphrase)
        g1.run("echo HIB-PROBE-START")
        g1.run("grep -o 'resume=[^ ]*' /proc/cmdline || echo NO-RESUME")
        g1.run("grep -c 'rd.luks.name' /proc/cmdline")
        g1.run("swapon --show=NAME,TYPE,SIZE --noheadings || echo NO-SWAP")
        g1.run("cat /sys/power/resume")
        g1.run("ls -l /dev/mapper/")
        g1.run("busctl --no-pager call org.freedesktop.login1 /org/freedesktop/login1 "
               "org.freedesktop.login1.Manager CanHibernate 2>&1 | tail -1")
        g1.run("echo BOOT1-BOOTID=$(cat /proc/sys/kernel/random/boot_id)")
        g1.run(f"date -Is > {MARKER}; echo BOOT1-MARKER=$(cat {MARKER})")
        g1.run("echo HIB-PROBE-END")
        probe = g1.text()
        boot1_id = _value(probe, "BOOT1-BOOTID=")

        print("\n--- hibernating ---", flush=True)
        g1.run("systemctl hibernate", settle=10.0)
        exited = g1.wait_exit(180)
        print(f"\n[b1] guest process exited: {exited}", flush=True)
    finally:
        g1.stop()

    if not exited:
        print("\nVERDICT: guest did NOT power off after `systemctl hibernate`.")
        return 1

    # ---------------- boot 2: did it RESUME or cold-boot? -------------------
    print("\n=== BOOT 2: resume check ===", flush=True)
    g2 = Guest(image, repo, work, ram, cpus, "b2")
    g2.start()
    resumed = False
    try:
        # A resumed kernel unlocks LUKS in the initramfs exactly like a cold
        # boot (the image lives in the encrypted swap), so the prompt is normal.
        g2.unlock_and_login(passphrase)
        mark = len(g2.buf)
        g2.run("echo RESUME-CHECK-START")
        g2.run(f"echo BOOT2-MARKER=$(cat {MARKER} 2>/dev/null || echo none)")
        g2.run("echo BOOT2-BOOTID=$(cat /proc/sys/kernel/random/boot_id)")
        g2.run("uptime -p; echo RESUME-CHECK-END")
        after = g2.text()[mark:]
        # boot_id is regenerated by every FRESH boot and preserved by a resume,
        # so an equal id is the proof. The /run marker (tmpfs, restored with the
        # image) corroborates it. Both are read from the command OUTPUT, never
        # from the echoed command line — the shell echoes the text back and a
        # naive substring match would always "find" it.
        boot2_id = _value(after, "BOOT2-BOOTID=")
        boot2_marker = _value(after, "BOOT2-MARKER=")
        resumed = bool(boot1_id) and boot2_id == boot1_id and boot2_marker not in ("", "none")
    finally:
        g2.stop()

    print("\n================ VERDICT ================")
    print("probe (boot 1) tail:")
    for line in probe.splitlines()[-40:]:
        print("  " + line)
    print("resume check (boot 2):")
    for line in after.splitlines()[-25:]:
        print("  " + line)
    print(f"\nRESUMED FROM HIBERNATION: {resumed}")
    return 0 if resumed else 1


if __name__ == "__main__":
    sys.exit(main())
