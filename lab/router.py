"""A private TAP network inside this container, never host networking."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

sys.path.insert(0, "/app")
from manage import dnsmasq


def run(*args):
    subprocess.run(args, check=True)


run("ip", "tuntap", "add", "dev", "pxe0", "mode", "tap")
run("ip", "addr", "add", "192.168.77.2/24", "dev", "pxe0")
run("ip", "link", "set", "pxe0", "up")
run("iptables", "-t", "nat", "-A", "POSTROUTING", "-s", "192.168.77.0/24", "-o", "eth0", "-j", "MASQUERADE")
run("iptables", "-A", "FORWARD", "-i", "pxe0", "-o", "eth0", "-j", "ACCEPT")
run("iptables", "-A", "FORWARD", "-i", "eth0", "-o", "pxe0", "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT")
Path("/lab-data").mkdir(exist_ok=True)
Path("/run/dnsmasq.conf").write_text(dnsmasq())
run("dnsmasq", "--test", "-C", "/run/dnsmasq.conf")
children = [subprocess.Popen(["dnsmasq", "--no-daemon", "--log-facility=-", "-C", "/run/dnsmasq.conf"]),
            subprocess.Popen(["websockify", "--web=/usr/share/novnc", "6080", "127.0.0.1:5900"])]


def stop(*_):
    for child in children:
        child.terminate()
    sys.exit(0)


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
Path("/run/pxe-ready").touch()
print("Isolated PXE router ready. VM is OFF; use lab/vm.py start after configuring the portal.", flush=True)
while all(child.poll() is None for child in children):
    time.sleep(1)
stop()
