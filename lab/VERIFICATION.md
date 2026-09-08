# Local setup verification — 2026-09-07

- Docker Desktop memory increased from approximately 8 GB to configured 16 GB,
  with user approval. Engine reports 16,747,134,976 bytes usable.
- All 15 containers running before the restart were restored and observed running.
- Native ARM64 lab image built with QEMU x86_64 emulation; the target OS is amd64.
- Real firmware-only QEMU VM (MAC `52:54:00:77:00:99`) received DHCP address
  `192.168.77.149`, gateway/server `192.168.77.2`, and downloaded both stages of
  the HTTP iPXE script successfully. No installation was armed.
- Firmware then tried the empty guest disk and reported no bootable device:
  expected for this unarmed smoke test. Screenshot: `../lab-data/pxe-smoke.png`.
- Smoke VM stopped after verification; no guest OS installation is running.
- Ubuntu 24.04.4 live-server amd64 ISO downloaded from official Ubuntu HTTPS and
  imported successfully after SHA-256 verification:
  `e907d92eeec9df64163a7e454cbc8d7755e8ddc7ed42f99dbc80c40f1a138433`.
- Live authenticated lab catalog lists the 24.04 ISO. Package catalog is empty:
  a real compatible ISS3 `.deb` is still required for ISS3 installation.
- Existing automated provisioning suite passed 10/10 tests in the lab image.

Not yet verified: full unattended Ubuntu Desktop installation, final static
networking on the installed guest, and ISS3 installation/service behavior. The
smoke test used iPXE in QEMU's NIC ROM and HTTP; it did not exercise a separate
firmware-to-TFTP chain or UEFI Secure Boot. Ubuntu 20.04/22.04 ISOs are not imported.


## Netboot debugging — 2026-09-08

Three defects in `manage.py`'s ISO import were found and fixed; each hid the next.

1. `cpio` was not installed in either image, so **every** ISO import aborted with
   `FileNotFoundError: 'cpio'`. Added `cpio` (and later `zstd`) to both Dockerfiles.
2. The casper-premount hook was concatenated **after** the compressed archive. The kernel
   rejects that (`Initramfs unpacking failed: invalid magic at start of compressed archive`),
   so the hook was never installed.
3. initramfs-tools' `run_scripts()` only sources `scripts/casper-premount/ORDER`; a hook that
   ORDER does not list never runs, however correctly it is installed. The import now rebuilds
   the compressed segment with both the hook and a patched ORDER appended, so the later entry
   wins over the ISO's own copy.

Verified consequences:

- casper's `do_urlmount` runs `wget "$URL" -O "$(basename "$URL")"`, writing the ISO into the
  initramfs rootfs — a tmpfs limited to 50% of target RAM. With an 11 GB target that capped the
  download at ~5278 MB, and a 6.65 GB Desktop ISO failed at 83% every time with
  `wget: short write: No space left on device`.
- With the hook actually running, the same Desktop ISO downloaded to **100% (6347M)** and booted
  into its live session. The RAM ceiling is removed.
- The Desktop ISO still cannot install here for a separate reason: its installer runs inside a
  full GNOME live session, which fails on this target with "Oh no! Something has gone wrong."
  No OOM and no killed process appears in the log — GNOME Shell has no GPU and software
  rendering under x86_64 TCG emulation is not sufficient. That limit is graphics, not memory,
  so more RAM on another machine would not by itself resolve it; native x86_64 or a real GPU
  would. The patched Desktop media is kept at `data/_unused/24.04.desktop`.

Subiquity/live-server findings:

- Subiquity logged `Skipping mirror check since network is not available` about 30 s into boot,
  while the guest's network was in fact working (`curl` to archive.ubuntu.com returned 200 from
  the installer environment). It therefore gave the target a cdrom-only apt source,
  `deb [check-date=no] file:///cdrom noble main restricted`, and the `packages: [ubuntu-desktop]`
  step died with `E: Unable to locate package ubuntu-desktop` (apt exit 100, three retries).
- Writing an archive source into the mounted target and running `apt-get update` in the chroot
  made `ubuntu-desktop 1.539` resolve from `noble/main`. `provision.py` now writes that source
  and installs the desktop from `late-commands` instead of `packages:`.
- This emulated target is timing-sensitive: a kernel `double fault` during `copy_process`, and a
  `netplan apply` exit 1, each occurred once and did not reproduce on retry (`netplan apply` by
  hand returned 0). Treat isolated installer failures here as transient and retry before
  investigating.
- Automated suite: 10/10 passing after updating two assertions in `tests/test_provisioner.py`
  that depended on `packages:` and on a fixed `late-commands` index.
