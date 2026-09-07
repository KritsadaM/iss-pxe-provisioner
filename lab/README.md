# Ready-to-run Mac / Docker VM lab

This lab runs a real x86_64 QEMU guest, DHCP/TFTP, NAT and a noVNC console in an
isolated Docker network namespace. It requires no UTM network configuration and
does not broadcast DHCP onto the Mac's physical LAN. QEMU runs natively on the
Docker host architecture and emulates an amd64 guest using TCG.

The existing web preview on **18090** is separate. Use these lab endpoints:

- Provisioning console: <http://127.0.0.1:18091>
- VM screen: <http://127.0.0.1:18092/vnc.html> → Connect
- Both endpoints bind only to localhost. Do not expose noVNC to untrusted networks.

## Start the services

Run from the `pxe-provisioner` directory:

```sh
docker compose -f compose.lab.yaml up --build -d
docker compose -f compose.lab.yaml ps
```

Docker needs at least **12 GB total** and at least **10 GB available** before
starting the 8 GB Desktop VM. 16 GB Docker memory is recommended with other
services running. The start command checks available memory and will refuse
instead of stopping unrelated services. Cross-architecture installation can be
slow, especially installation of Desktop packages.

## Prepared target settings

Open port 18091, unlock using `PXE_ADMIN_TOKEN` in the project's `.env`, then set:

| Field | Value |
| --- | --- |
| MAC | `52:54:00:77:00:10` |
| Hostname | Your choice, e.g. `iss-desktop` |
| Release | `24.04` (after ISO import) |
| Target disk | `/dev/vda` — dedicated `lab-data/desktop.qcow2`, never a host disk |
| Username / password | Your chosen OS login credentials |
| Network | DHCP, or static `192.168.77.20/24` |
| Static gateway | `192.168.77.2` |
| DNS | `1.1.1.1` |
| ISS3 | OS only until a compatible ISS3 `.deb` is imported |
| Erase confirmation | `52:54:00:77:00:10` |

Click **Arm deployment**, then boot the VM:

```sh
docker compose -f compose.lab.yaml exec lab python lab/vm.py start
```

Watch port 18092. PXE will load the installer without interaction. The target disk
is a sparse 50 GB QCOW2 file. **Arming authorizes erasing that guest disk**; do not
rearm an installed VM unless you intend to reinstall it. The OS password is not
automatically chosen, and no install is armed by setup alone.

## Assets

Setup downloads Ubuntu 24.04.4 live-server amd64 from the official Ubuntu HTTPS
origin and checks the official SHA256SUMS. This is checksum verification over
HTTPS, not detached GPG-signature verification. The installed target becomes
Desktop through `ubuntu-desktop`; network access to Ubuntu repositories is needed.

To repeat download safely (resumes a `.partial` download):

```sh
python3 lab/download.py
```

The local script needs PyYAML, or use the existing development Python environment.
Import using the SHA printed by the download command:

```sh
docker compose --profile tools run --rm assets iso /assets/ubuntu-24.04.4-live-server-amd64.iso --release 24.04 --sha256 SHA_FROM_VERIFIED_DOWNLOAD
```

The lab mounts the same imported ISO/package directories read-only but uses its
own deployment database. A valid ISS3 package has not been invented or substituted:
import a real `iss3` amd64 `.deb` following the main README, then refresh the portal.

## VM controls and troubleshooting

```sh
docker compose -f compose.lab.yaml exec lab python lab/vm.py status
docker compose -f compose.lab.yaml exec lab python lab/vm.py screenshot
docker compose -f compose.lab.yaml logs --tail=100 lab web
docker compose -f compose.lab.yaml exec lab python lab/vm.py poweroff
```

`poweroff` requests a graceful ACPI shutdown. If a firmware-only test is stuck,
`force-stop` stops only this lab VM immediately (do not use during a disk write).
Neither command deletes its disk. `docker compose -f compose.lab.yaml down` also
stops the lab; use graceful guest shutdown first if it contains data to preserve.
Disk, screenshot and serial log persist in `lab-data/`; the database is under
`lab-data/control/`. Do not delete this directory unless you intend to discard
the guest and deployment history.

The `smoke` action starts a separate 512 MB firmware-only VM with MAC
`52:54:00:77:00:99` and its own 1 GB disk. **Never arm that MAC**: it exists only
to verify DHCP → iPXE → the unarmed boot response. It is not a Desktop install test.

Starting or restarting the Docker services does **not** boot the guest automatically.
After the guest is installed, `start` again boots it; PXE exits and firmware falls
back to disk, provided the previous deployment is not armed for reinstallation.

References: [QEMU invocation](https://www.qemu.org/docs/master/system/invocation.html),
[Ubuntu release](https://releases.ubuntu.com/24.04.4/).
