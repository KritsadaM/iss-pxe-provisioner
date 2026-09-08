"""Unattended Desktop configuration for Subiquity live-server installers."""
import base64
import ipaddress
import re
import shlex
import subprocess
import yaml

RELEASES = ("20.04", "22.04", "24.04")
CODENAMES = {"20.04": "focal", "22.04": "jammy", "24.04": "noble"}
DESKTOP_PACKAGES = ("ubuntu-desktop", "curl", "network-manager")


def mac(value):
    value = str(value).lower().replace("-", ":")
    if not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", value):
        raise ValueError("Invalid MAC address")
    if int(value[:2], 16) & 1 or value == "00:00:00:00:00:00":
        raise ValueError("Specify a unicast MAC address")
    return value


def validate_device(data):
    if not isinstance(data, dict):
        raise ValueError("Expected a configuration object")
    result = {k: str(data[k]) for k in ("release", "hostname", "username", "disk")}
    result.update(mac=mac(data["mac"]), mode=data.get("mode", "dhcp"),
                  package=data.get("package", ""), bu=str(data.get("bu", ""))[:64])
    if result["release"] not in RELEASES:
        raise ValueError("Select Ubuntu 20.04, 22.04 or 24.04")
    if not re.fullmatch(r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?", result["hostname"]):
        raise ValueError("Invalid hostname")
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,30}", result["username"]) or result["username"] in ("root", "iss3"):
        raise ValueError("Choose a normal login username")
    password = data["password"]
    if not isinstance(password, str) or len(password) < 8 or any(c in password for c in "\r\n\x00"):
        raise ValueError("Password requires 8 characters, without line breaks")
    if not re.fullmatch(r"/dev/(?:[sv]d[a-z]|nvme[0-9]+n[0-9]+)", result["disk"]):
        raise ValueError("Specify the whole disk, e.g. /dev/vda or /dev/sda")
    if result["mode"] not in ("dhcp", "static"):
        raise ValueError("Choose DHCP or static networking")
    if result["mode"] == "static":
        address = ipaddress.IPv4Interface(data["address"])
        gateway = ipaddress.IPv4Address(data["gateway"])
        if gateway not in address.network or gateway in (address.ip, address.network.network_address, address.network.broadcast_address):
            raise ValueError("Gateway must be another address in the subnet")
        if address.ip in (address.network.network_address, address.network.broadcast_address):
            raise ValueError("Invalid host address")
        dns = [str(ipaddress.IPv4Address(x.strip())) for x in data["dns"].split(",") if x.strip()]
        if not dns:
            raise ValueError("Specify DNS servers")
        result.update(address=str(address), gateway=str(gateway), dns=dns)
    result["password_hash"] = subprocess.run(["openssl", "passwd", "-6", "-stdin"],
        input=password + "\n", text=True, capture_output=True, check=True).stdout.strip()
    return result


def final_network(device):
    nic = {"match": {"macaddress": device["mac"]}, "dhcp4": device["mode"] == "dhcp"}
    if device["mode"] == "static":
        nic.update(addresses=[device["address"]], gateway4=device["gateway"],
                   nameservers={"addresses": device["dns"]})
    return {"network": {"version": 2, "renderer": "NetworkManager", "ethernets": {"pxe": nic}}}


def seed(device, base, package=None):
    endpoint = base + "/deployment/" + device["token"]
    q = shlex.quote
    def report(state):
        return "curl -fsS --retry 5 --connect-timeout 10 --max-time 30 -X POST -H 'Content-Type: application/json' -d " + q('{"state":"' + state + '"}') + " " + q(endpoint + "/status")
    script = ["#!/bin/bash", "set -euo pipefail", "exec >>/var/log/iss-provision.log 2>&1",
              "trap " + q(report("failed") + " || true") + " ERR",
              "systemctl set-default graphical.target", "systemctl start display-manager.service"]
    if package:
        script += ["curl -f --retry 5 --connect-timeout 10 --max-time 1800 " + q(endpoint + "/package.deb") + " -o /var/tmp/iss3-provision.deb",
                   "echo " + q(package["sha256"] + "  /var/tmp/iss3-provision.deb") + " | sha256sum -c -",
                   "DEBIAN_FRONTEND=noninteractive apt-get install -y /var/tmp/iss3-provision.deb",
                   "systemctl is-active --quiet iss3.service", "rm /var/tmp/iss3-provision.deb"]
    script += [report("completed"), "touch /var/lib/iss-provision-completed"]
    encoded = base64.b64encode(yaml.safe_dump(final_network(device)).encode()).decode()
    # Subiquity only writes an archive mirror into the target when its early network probe
    # succeeds; on a slow target that probe loses the race and the target is left with just
    # "deb file:///cdrom", where ubuntu-desktop does not exist. Supply the archive explicitly.
    suite = CODENAMES[device["release"]]
    sources = base64.b64encode("".join(
        "deb http://archive.ubuntu.com/ubuntu %s main restricted universe multiverse\n" % name
        for name in (suite, suite + "-updates", suite + "-security")).encode()).decode()
    # The lab LAN is IPv4-only, so every AAAA candidate fails with "Network is unreachable"
    # before apt reaches an A record, and a single timed-out mirror connection aborts the whole
    # desktop install because apt does not retry by default.
    apt_conf = base64.b64encode(b'Acquire::ForceIPv4 "true";\n'
                                b'Acquire::Retries "10";\n'
                                b'Acquire::http::Timeout "120";\n').decode()
    config = {"version": 1, "refresh-installer": {"update": False},
              "locale": "en_US.UTF-8", "keyboard": {"layout": "us"},
              "identity": {"hostname": device["hostname"], "username": device["username"], "password": device["password_hash"]},
              "storage": {"layout": {"name": "direct", "match": {"path": device["disk"]}}},
              "network": {"version": 2, "ethernets": {"pxe": {"match": {"macaddress": device["mac"]}, "dhcp4": True}}},
              "early-commands": [report("installing")], "error-commands": [report("failed") + " || true"],
              "late-commands": [
                  "echo " + q(sources) + " | base64 -d > /target/etc/apt/sources.list",
                  "echo " + q(apt_conf) + " | base64 -d > /target/etc/apt/apt.conf.d/99-iss-provision",
                  "curtin in-target --target=/target -- apt-get update",
                  "curtin in-target --target=/target -- env DEBIAN_FRONTEND=noninteractive apt-get install -y " + " ".join(DESKTOP_PACKAGES),
                  "rm -f /target/etc/netplan/00-installer-config.yaml /target/etc/netplan/50-cloud-init.yaml",
                  "echo " + q(encoded) + " | base64 -d > /target/etc/netplan/01-iss-provision.yaml",
                  "chmod 600 /target/etc/netplan/01-iss-provision.yaml",
                  "mkdir -p /target/etc/cloud/cloud.cfg.d",
                  "echo 'network: {config: disabled}' > /target/etc/cloud/cloud.cfg.d/99-disable-network-config.cfg",
                  report("first_boot")],
              "user-data": {"write_files": [
                  {"path": "/etc/cloud/cloud.cfg.d/99-disable-network-config.cfg", "content": "network: {config: disabled}\n"},
                  {"path": "/usr/local/sbin/iss-provision", "permissions": "0700", "content": "\n".join(script) + "\n"}],
                  "runcmd": [["bash", "/usr/local/sbin/iss-provision"]]}}
    return "#cloud-config\n" + yaml.safe_dump({"autoinstall": config}, sort_keys=False)


def ipxe(device, base):
    if not device:
        return "#!ipxe\nexit\n"
    root = base + "/media/" + device["release"]
    source = base + "/deployment/" + device["token"] + "/"
    return ("#!ipxe\n" +
        "kernel %s/vmlinuz initrd=initrd ip=dhcp url=%s/installer.iso cloud-config-url=/dev/null autoinstall ds=nocloud-net;s=%s initramfs.runsize=90%% no5lvl nopku console=tty0 console=ttyS0,115200n8 ---\n" % (root, root, source) +
        "initrd --name initrd %s/initrd\nboot\n" % root)
