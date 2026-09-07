# ISS PXE Provisioner — Docker VM lab

**Mac-ready isolated VM setup:** see [lab/README.md](lab/README.md).
The optional `compose.lab.yaml` runs QEMU and PXE entirely inside Docker, with
the lab portal on localhost 18091 and VM console on 18092.

Configure a machine on the web, arm its MAC, then network-boot it. No installer
questions or target-side commands are required. The provisioner installs Ubuntu
Desktop, applies the login account and final network settings, and optionally
installs an imported ISS3 `.deb`.

This is a development prototype, not a production deployment service. It does
not change the existing ISS3 source code. BU is metadata only; BU upload services
are not implemented yet.

## What is included

- Docker Compose web/API service on TCP 8090, SQLite persistent state in `data/`.
- Opt-in `pxe` profile: dnsmasq DHCP/TFTP plus BIOS/UEFI iPXE boot files.
- Web configuration: Ubuntu version, MAC, hostname, username/password, target disk,
  DHCP/static IPv4, optional ISS3 package and BU.
- MAC-bound, one-shot installation authorization. Unregistered/unarmed machines
  exit iPXE without receiving an installation command.
- Password hashing, token-protected deployment seeds, package checksum validation,
  and explicit target MAC confirmation before authorizing disk erasure.

## Installer strategy and supported scope

The selectable releases are **20.04 / 22.04 / 24.04**, **amd64/x86_64**.
Import the corresponding **live-server ISO**, not the Desktop ISO. Subiquity
autoinstall installs `ubuntu-desktop` to produce a Desktop system. This follows
the [Canonical Desktop autoinstall example](https://github.com/canonical/autoinstall-desktop)
(documented there for 22.04). Applying this approach to all three releases still
requires a real installation test for each exact ISO and ISS3 package combination.
This project does not claim those installations have already passed.

The target needs internet access (or equivalent configured Ubuntu mirrors) for
Desktop packages and ISS3 dependencies. This is **not an offline image**. Use an
ISS3 amd64 `.deb` built for the selected OS; registering `--compatible` declares
your compatibility claim, it does not prove binary compatibility.

## 1. Build and preview the web on this Mac

```sh
cd /Users/anonymous/ISS3_playground_codex/pxe-provisioner
docker build --platform linux/amd64 -t iss-pxe:dev .
docker run --rm --platform linux/amd64 -v "$PWD:/app" iss-pxe:dev python manage.py init
docker compose up -d web
```

Open <http://127.0.0.1:8090>. Read `PXE_ADMIN_TOKEN` from the generated `.env`
locally and paste it into the Unlock field. `init` refuses to overwrite `.env`.
The token remains only in browser memory, not local storage. Do not commit `.env`.

The default starts **only the web**, not DHCP. No installable images appear until
you import them. Docker Desktop port publishing does not provide the isolated
layer-2 DHCP/PXE network needed by target VMs; use the Linux-host arrangement below.

If 8090 is occupied, set `PXE_HTTP_PORT=18090` in `.env` for the Mac preview and
open <http://127.0.0.1:18090>. The preview prepared in this workspace uses 18090
to avoid the existing log server. Remove that override on the Linux PXE host,
where the boot configuration expects port 8090.

## 2. VM networking for a real PXE test

Use a **Linux Docker host VM** with two virtual NICs:

| Machine/NIC | Network | Address / purpose |
| --- | --- | --- |
| Linux host NIC 1 (`ens18`, example) | Hypervisor NAT | Internet/uplink |
| Linux host NIC 2 (`ens19`, example) | Isolated PXE switch | Static `192.168.77.2/24`, no gateway on this NIC |
| Target VM NIC | Same isolated PXE switch only | Receives DHCP from this project |

Disable the hypervisor's own DHCP on the isolated switch. **Do not bridge this
switch to a company/home LAN.** Interface names are examples: check `ip -br addr`
and configure the correct names in `.env`. Choose a subnet not used by the uplink.

Provide routing/NAT so the target can reach Ubuntu package repositories. Either
use a separate router `192.168.77.1` with DHCP disabled, or make the Linux Docker
host the router. For the latter, after reviewing the interface names, run on that
Linux VM (not macOS):

```sh
sudo sysctl -w net.ipv4.ip_forward=1
sudo iptables -t nat -A POSTROUTING -s 192.168.77.0/24 -o ens18 -j MASQUERADE
sudo iptables -I DOCKER-USER 1 -i ens19 -o ens18 -s 192.168.77.0/24 -j ACCEPT
sudo iptables -I DOCKER-USER 1 -i ens18 -o ens19 -d 192.168.77.0/24 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
```

These are temporary IPv4/iptables lab settings and assume Docker's iptables
backend. They are not automatically installed or persisted. On nftables-managed
hosts, configure equivalent forwarding/NAT in the host firewall instead.
Undo lab rules by repeating the same iptables commands with `-D` instead of
`-A`/`-I` (omit position `1`), and restore the previous IP forwarding setting.

Copy this directory onto the Linux VM (exclude the Mac's `.env` and `data` for a
fresh lab), build the image, run `manage.py init`, and edit `.env`:

```dotenv
PXE_BASE_URL=http://192.168.77.2:8090
PXE_HTTP_BIND=192.168.77.2
PXE_INTERFACE=ens19
PXE_SERVER_CIDR=192.168.77.2/24
PXE_DHCP_START=192.168.77.100
PXE_DHCP_END=192.168.77.200
PXE_GATEWAY=192.168.77.2
PXE_DNS=1.1.1.1
```

Keep the generated `PXE_ADMIN_TOKEN`. When using the separate `.1` router instead,
set `PXE_GATEWAY=192.168.77.1`. This service does not assign the host NIC's static
IP, enable routing, or change firewall rules for you.

Allow DHCP UDP 67, TFTP UDP 69 and its related transfer traffic, and HTTP TCP 8090
on the isolated interface. Then:

```sh
docker compose config --quiet
docker compose --profile pxe up --build -d
docker compose logs --tail=100 dhcp web
```

The DHCP service uses host networking **on Linux** and is limited to
`PXE_INTERFACE`. Secure Boot must be off for the bundled iPXE firmware.
Use a target with 4 vCPUs, at least 8 GB RAM, and a blank 50 GB disk as a starting
point; ISO downloading consumes RAM. On Apple Silicon, these amd64 targets require
x86_64 emulation, not an ARM Ubuntu VM, and will be substantially slower.

## 3. Import installer and ISS3 assets

Place your matching live-server ISO and optional ISS3 `.deb` under `assets/`.
Obtain the ISO's SHA-256 from a trusted Ubuntu release source and verify its
checksum/signature; do not blindly use a hash of an untrusted download.

```sh
docker compose --profile tools run --rm assets iso /assets/ubuntu-live-server-amd64.iso --release 24.04 --sha256 TRUSTED_ISO_SHA256
docker compose --profile tools run --rm assets deb /assets/iss3_amd64.deb --compatible 24.04 --sha256 TRUSTED_PACKAGE_SHA256
```

Repeat the ISO import with `--release 20.04` or `22.04` and the matching file.
The importer verifies SHA-256, checks ISO release/server/amd64 identification,
extracts `casper/vmlinuz` and `casper/initrd`, and publishes assets atomically.
The Debian package must identify itself as `iss3`, architecture `amd64` or `all`.
Existing imported assets cannot be overwritten, preserving active deployments.

**Import the live-server ISO, not the Desktop ISO.** The importer's `.disk/info`
check accepts either, but a Desktop ISO cannot work here: the seed generated by
`provision.py` is Subiquity autoinstall, and casper's netboot downloads the whole
ISO into a RAM tmpfs limited to 50% of target RAM. A ~6.6 GB Desktop ISO exceeds
that on an 11 GB target and the boot ends with `wget: short write: No space left
on device` followed by `Unable to find a live file system on the network`.
`.disk/info` begins with `Ubuntu-Server` for the correct image.

## 4. Configure once, then boot the VM

1. Open the web on the PXE host; unlock using the admin token.
2. Enter the target VM's fixed MAC, hostname, login username/password, release,
   exact whole disk (`/dev/vda`, `/dev/sda`, or `/dev/nvme0n1`), and final network.
3. Select the ISS3 package, or OS only. Static IPs must be outside the DHCP pool,
   unused, and able to reach this server and the configured gateway.
4. Confirm the target MAC to authorize **erasing the selected disk**, then arm.
5. Start that VM with network/PXE boot. Installation and first boot are unattended.

State progression: `armed → booting → installing → first_boot → completed`.
Completed means the first-boot script succeeded, started the graphical display
manager, and (when selected) checked that `iss3.service` is active. It is not an
ISS3 functional test. Normal OS login still requires the configured credentials;
automatic installer operation does not enable automatic desktop login.

The first iPXE request consumes the authorization. On subsequent PXE boots the
server returns `exit`, allowing firmware to continue to the local disk (configure
disk as the next boot option). If an install fails, stop the target, investigate,
cancel/revoke, and explicitly rearm before retrying. Cancelling only revokes future
boot/seed/package access: **it cannot stop disk operations already in progress**.

## Logs, verification and limitations

```sh
docker compose logs --tail=200 web dhcp
docker run --rm --platform linux/amd64 iss-pxe:dev python -m unittest discover -s tests -v
docker compose --profile pxe down
```

Target logs: `/var/log/installer/`, `/var/log/cloud-init-output.log`, and
`/var/log/iss-provision.log`. Inspect `cloud-init status --long` and
`systemctl status iss3` after installation. A lost network/status callback can
leave a deployment in its previous state; inspect target logs before rearming.

Automated tests cover generated configuration for three releases, shell syntax,
API authentication, MAC/disk validation, one-shot concurrent boots, persistent
state, token revocation, lifecycle transitions, package checksum instructions, and
dnsmasq syntax/boot-file availability. Fixture files are **not bootable ISOs**;
these tests do not replace a full VM installation of each release.

This lab uses HTTP for iPXE, ISO downloads and tokenized seeds. Use only a trusted
isolated LAN: a party observing seed URLs can access an active deployment.
Prefer an SSH tunnel for administrator access, or add a TLS reverse proxy for
the web UI before broader use. HTTP access logs are disabled to reduce token URL
exposure. Target installation logs may contain sensitive provisioning details.

References: [Canonical autoinstall Desktop example](https://github.com/canonical/autoinstall-desktop),
[Subiquity autoinstall](https://canonical-subiquity.readthedocs-hosted.com/en/latest/intro-to-autoinstall.html),
[casper ISO network boot](https://manpages.ubuntu.com/manpages/jammy/man7/casper.7.html),
[dnsmasq manual](https://thekelleys.org.uk/dnsmasq/docs/dnsmasq-man.html).
