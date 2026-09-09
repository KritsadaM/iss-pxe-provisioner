"""Send a line to the lab guest's serial console and print what it replies.

The console is a QEMU socket chardev, so this is the guest's own tty: use it to inspect a
deployment that finished without a usable graphical session. Only the disposable lab VM is
reachable here; no host device is attached.
"""
import argparse
import socket
import time
from pathlib import Path

SOCKET = str(Path("/lab-data") / "console.sock")


def converse(lines, settle=2.0, timeout=25.0):
    with socket.socket(socket.AF_UNIX) as connection:
        connection.connect(SOCKET)
        connection.settimeout(settle)
        deadline = time.time() + timeout
        received = b""
        for line in lines:
            connection.sendall(line.encode() + b"\r")
            time.sleep(settle)
            while time.time() < deadline:
                try:
                    chunk = connection.recv(65536)
                except socket.timeout:
                    break
                if not chunk:
                    break
                received += chunk
        return received.decode("utf-8", "replace")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("line", nargs="*", default=[""])
    parser.add_argument("--settle", type=float, default=2.0)
    parser.add_argument("--timeout", type=float, default=25.0)
    args = parser.parse_args()
    print(converse(args.line or [""], args.settle, args.timeout))
