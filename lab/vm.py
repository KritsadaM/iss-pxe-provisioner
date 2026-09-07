"""Control only the dedicated disposable lab VM; never attaches host disks."""
import argparse
import json
from pathlib import Path
import socket
import subprocess

ROOT = Path("/lab-data")
SOCKET = str(ROOT / "qmp.sock")


def qmp(command, arguments=None):
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(10)
        connection.connect(SOCKET)
        stream = connection.makefile("rwb")
        json.loads(stream.readline())
        for name, args in (("qmp_capabilities", {}), (command, arguments or {})):
            stream.write((json.dumps({"execute": name, "arguments": args}) + "\n").encode())
            stream.flush()
            while True:
                response = json.loads(stream.readline())
                if "error" in response:
                    raise RuntimeError(response["error"])
                if "return" in response:
                    break
        return response["return"]


def start(memory, smoke):
    try:
        qmp("query-status")
    except (OSError, ValueError):
        pass
    else:
        raise SystemExit("VM is already running")
    ROOT.mkdir(exist_ok=True)
    # Firmware-only testing has its own ephemeral disk and is never suitable for installation.
    disk = ROOT / ("smoke.qcow2" if smoke else "desktop.qcow2")
    if not disk.exists():
        subprocess.run(["qemu-img", "create", "-f", "qcow2", str(disk), "1G" if smoke else "50G"], check=True)
    if not smoke:
        total_kb = int(Path("/proc/meminfo").read_text().split("MemTotal:")[1].split()[0])
        available_kb = int(Path("/proc/meminfo").read_text().split("MemAvailable:")[1].split()[0])
        if total_kb < 12 * 1024 * 1024 or available_kb < (memory + 2048) * 1024:
            raise SystemExit("Not enough Docker memory: allocate >=12 GB total and leave VM RAM + 2 GB available. Existing services are not stopped automatically.")
    cmd = ["qemu-system-x86_64", "-name", "iss-pxe-desktop", "-machine", "q35",
           "-accel", "tcg", "-cpu", "max,la57=off,pku=off", "-smp", "2", "-m", str(memory),
           "-drive", f"file={disk},format=qcow2,if=virtio",
           "-netdev", "tap,id=pxe,ifname=pxe0,script=no,downscript=no",
           "-device", "virtio-net-pci,netdev=pxe,mac=" + ("52:54:00:77:00:99" if smoke else "52:54:00:77:00:10"),
           "-boot", "order=nc,menu=off", "-display", "none", "-vnc", "127.0.0.1:0",
           "-serial", "file:" + str(ROOT / "serial.log"),
           "-qmp", "unix:" + SOCKET + ",server=on,wait=off", "-daemonize",
           "-pidfile", str(ROOT / "qemu.pid")]
    subprocess.run(cmd, check=True)
    print("VM started. Console: http://127.0.0.1:18092/vnc.html (Connect).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "smoke", "status", "poweroff", "force-stop", "screenshot"])
    args = parser.parse_args()
    if args.action in ("start", "smoke"):
        start(512 if args.action == "smoke" else 11264, args.action == "smoke")
    elif args.action == "status":
        print(json.dumps(qmp("query-status")))
    elif args.action == "poweroff":
        print(qmp("system_powerdown"))
    elif args.action == "force-stop":
        print(qmp("quit"))
    elif args.action == "screenshot":
        qmp("screendump", {"filename": str(ROOT / "screen.ppm")})
        print(ROOT / "screen.ppm")
