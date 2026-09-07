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
