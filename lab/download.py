"""Fetch an Ubuntu ISO with resumable curl and checksum from its official HTTPS origin."""
import argparse
from pathlib import Path
import re
import subprocess
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from manage import checksum

parser = argparse.ArgumentParser()
parser.add_argument("--point-release", default="24.04.4")
parser.add_argument("--flavor", choices=["desktop", "live-server"], default="desktop")
parser.add_argument("--mirror", default="https://mirror.kku.ac.th/ubuntu-releases/")
args = parser.parse_args()
if not re.fullmatch(r"(?:20|22|24)\.04\.[0-9]+", args.point_release):
    raise SystemExit("Expected an Ubuntu LTS point release")
canonical_base = "https://releases.ubuntu.com/" + args.point_release + "/"
name = f"ubuntu-{args.point_release}-{args.flavor}-amd64.iso"
checksums = urllib.request.urlopen(canonical_base + "SHA256SUMS", timeout=60).read().decode()
expected = next((line.split()[0] for line in checksums.splitlines() if line.split()[-1].lstrip("*") == name), None)
if not expected:
    raise SystemExit("ISO not present in official SHA256SUMS")
destination = Path("assets")
destination.mkdir(exist_ok=True)
target = destination / name
partial = target.with_suffix(".iso.partial")
download_url = (args.mirror.rstrip("/") + "/" + args.point_release + "/" + name) if args.mirror else (canonical_base + name)
if not target.exists():
    subprocess.run(["curl", "--fail", "--location", "--retry", "5", "--continue-at", "-", "--output", str(partial), download_url], check=True)
    if checksum(partial) != expected:
        raise SystemExit("ISO checksum mismatch; partial file retained for inspection")
    partial.rename(target)
elif checksum(target) != expected:
    raise SystemExit("Existing ISO checksum mismatch; no file overwritten")
(destination / (name + ".sha256")).write_text(expected + "  " + name + "\n")
print("Verified:", target, expected)
