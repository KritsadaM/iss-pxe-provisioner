"""Import verified local installer assets and generate dedicated-LAN DHCP config."""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import secrets
import subprocess
import tempfile

from provision import RELEASES


def checksum(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


HOOK = """#!/bin/sh
# casper's do_urlmount runs `wget "$URL" -O "$(basename "$URL")"`, so a netbooted ISO is written
# into the initramfs rootfs: a tmpfs whose default limit is 50% of RAM. A Desktop ISO does not fit
# that on a modestly sized target, so raise the limit before the download starts.
{ mount -o remount,size=90% none / || mount -o remount,size=90% / ; df -h / ; } > /dev/console 2>&1
exit 0
"""
HOOK_NAME = "05iss_rootfs_size"
DECOMPRESS = {b"\x28\xb5\x2f\xfd": ["zstd", "-d", "-c"], b"\x1f\x8b\x08\x00": ["gzip", "-d", "-c"]}


def cpio_segments(data):
    """Return the offset just past the leading uncompressed newc archives."""
    off = 0
    while data[off:off + 6] == b"070701":
        namesize = int(data[off + 94:off + 102], 16)
        filesize = int(data[off + 54:off + 62], 16)
        name = data[off + 110:off + 110 + namesize - 1]
        end = (off + 110 + namesize + 3) & ~3
        off = (end + filesize + 3) & ~3
        if name == b"TRAILER!!!":
            while off < len(data) and data[off] == 0:
                off += 1
    return off


def patch_initrd(path):
    """Register a casper-premount hook that grows the initramfs rootfs before the ISO download.

    initramfs-tools' run_scripts only sources scripts/casper-premount/ORDER, so an added hook is
    ignored unless ORDER lists it. The kernel accepts uncompressed cpio archives only ahead of a
    compressed one, and a later entry wins, so the compressed segment is rebuilt with both files
    appended rather than concatenating a separate archive onto the image.
    """
    data = path.read_bytes()
    offset = cpio_segments(data)
    for magic, command in DECOMPRESS.items():
        if data[offset:offset + len(magic)] == magic:
            break
    else:
        raise ValueError("Unsupported initrd compression; expected zstd or gzip")
    main = subprocess.run(command, input=data[offset:], capture_output=True, check=True).stdout
    order = subprocess.run(["cpio", "-i", "--to-stdout", "scripts/casper-premount/ORDER"],
                           input=main, capture_output=True).stdout.decode()
    entry = "/scripts/casper-premount/%s \"$@\"\n[ -e /conf/param.conf ] && . /conf/param.conf\n" % HOOK_NAME
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp)
        hook_dir = staging / "scripts" / "casper-premount"
        hook_dir.mkdir(parents=True)
        (hook_dir / HOOK_NAME).write_text(HOOK)
        (hook_dir / HOOK_NAME).chmod(0o755)
        (hook_dir / "ORDER").write_text(entry + order)
        listing = "".join(name + "\n" for name in (
            "scripts", "scripts/casper-premount",
            "scripts/casper-premount/" + HOOK_NAME, "scripts/casper-premount/ORDER"))
        extra = subprocess.run(["cpio", "-o", "-H", "newc"], cwd=staging,
                               input=listing.encode(), capture_output=True, check=True).stdout
    rebuilt = subprocess.run(command[:1] + ["-c"], input=main + extra,
                             capture_output=True, check=True).stdout
    path.chmod(0o644)
    path.write_bytes(data[:offset] + rebuilt)


def import_asset(args):
    source = Path(args.file)
    digest = checksum(source)
    if digest != args.sha256.lower():
        raise ValueError("SHA-256 mismatch; use a checksum obtained from a trusted source")
    root = Path(os.environ.get("PXE_DATA", "/data"))
    parent = root / ("media" if args.kind == "iso" else "packages")
    parent.mkdir(parents=True, exist_ok=True)
    destination = parent / (args.release if args.kind == "iso" else digest)
    if destination.exists():
        raise ValueError("Asset already exists; existing deployments must keep immutable assets")
    with tempfile.TemporaryDirectory(dir=parent) as tmp:
        staging = Path(tmp)
        if args.kind == "iso":
            shutil.copyfile(source, staging / "installer.iso")
            for name in ("vmlinuz", "initrd"):
                subprocess.run(["bsdtar", "-xf", str(source), "-C", tmp, "casper/" + name], check=True)
                (staging / "casper" / name).rename(staging / name)
            patch_initrd(staging / "initrd")
            subprocess.run(["bsdtar", "-xf", str(source), "-C", tmp, ".disk/info"], check=True)
            info = (staging / ".disk/info").read_text()
            if args.release not in info or "amd64" not in info.lower():
                raise ValueError("Expected matching Ubuntu live-server or desktop amd64 ISO")
            manifest = {"release": args.release, "sha256": digest, "source": info.strip()}
        else:
            package = subprocess.check_output(["dpkg-deb", "-f", str(source), "Package"], text=True).strip()
            arch = subprocess.check_output(["dpkg-deb", "-f", str(source), "Architecture"], text=True).strip()
            if package != "iss3" or arch not in ("amd64", "all"):
                raise ValueError("Expected ISS3 amd64 Debian package")
            shutil.copyfile(source, staging / "package.deb")
            version = subprocess.check_output(["dpkg-deb", "-f", str(source), "Version"], text=True).strip()
            manifest = {"version": version, "sha256": digest, "releases": args.compatible}
        (staging / "manifest.json").write_text(json.dumps(manifest))
        # Atomic publish; no partially imported image appears in the web catalog.
        staging.rename(destination)
    print(destination)


def dnsmasq():
    interface = os.environ["PXE_INTERFACE"]
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,14}", interface):
        raise ValueError("Invalid PXE_INTERFACE")
    server = ipaddress.IPv4Interface(os.environ["PXE_SERVER_CIDR"])
    start = ipaddress.IPv4Address(os.environ["PXE_DHCP_START"])
    end = ipaddress.IPv4Address(os.environ["PXE_DHCP_END"])
    gateway = ipaddress.IPv4Address(os.environ["PXE_GATEWAY"])
    dns = str(ipaddress.IPv4Address(os.environ["PXE_DNS"]))
    if not (start in server.network and end in server.network and gateway in server.network):
        raise ValueError("DHCP addresses and gateway must be in the PXE subnet")
    if not int(server.network.network_address) < int(start) <= int(end) < int(server.network.broadcast_address):
        raise ValueError("Invalid DHCP range")
    if start <= server.ip <= end or start <= gateway <= end:
        raise ValueError("DHCP pool must exclude server and gateway")
    base = os.environ["PXE_BASE_URL"]
    if base != "http://%s:8090" % server.ip:
        raise ValueError("PXE_BASE_URL must match http://<PXE_SERVER_IP>:8090")
    return "\n".join(["port=0", "bind-interfaces", "interface=" + interface,
        "dhcp-authoritative", "dhcp-range=%s,%s,%s,12h" % (start, end, server.netmask),
        "dhcp-option=3," + str(gateway), "dhcp-option=6," + dns,
        "enable-tftp", "tftp-root=/usr/lib/ipxe", "log-dhcp",
        "dhcp-match=set:ipxe,175", "dhcp-match=set:efi64,option:client-arch,7",
        "dhcp-match=set:efi64,option:client-arch,9",
        "dhcp-boot=tag:!ipxe,tag:efi64,ipxe.efi",
        "dhcp-boot=tag:!ipxe,tag:!efi64,undionly.kpxe",
        "dhcp-boot=tag:ipxe," + base + "/boot.ipxe", ""])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="kind", required=True)
    for kind in ("iso", "deb"):
        p = sub.add_parser(kind)
        p.add_argument("file")
        p.add_argument("--sha256", required=True)
        if kind == "iso":
            p.add_argument("--release", choices=RELEASES, required=True)
        else:
            p.add_argument("--compatible", choices=RELEASES, nargs="+", required=True)
    sub.add_parser("dnsmasq")
    sub.add_parser("init")
    args = parser.parse_args()
    if args.kind == "init":
        # Exclusive creation: never replace an existing environment or secret.
        with open(".env", "x", opener=lambda path, flags: os.open(path, flags, 0o600)) as stream:
            stream.write(Path(".env.example").read_text().replace(
                "replace-this-with-a-random-token-before-start", secrets.token_urlsafe(32)))
        print("Created .env; configure your isolated VM LAN before enabling the pxe profile")
    elif args.kind == "dnsmasq":
        print(dnsmasq())
    else:
        import_asset(args)
