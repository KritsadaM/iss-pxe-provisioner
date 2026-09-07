import base64
from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml
from portal import create_app
from provision import RELEASES, seed, validate_device
from manage import checksum, dnsmasq, import_asset


class ProvisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.token = "test-admin-secret-not-for-production"
        self.headers = {"Authorization": "Bearer " + self.token}
        for version in RELEASES:
            folder = self.root / "media" / version
            folder.mkdir(parents=True)
            for filename in ("installer.iso", "vmlinuz", "initrd"):
                (folder / filename).write_bytes(b"test-fixture-not-an-installer")
            (folder / "manifest.json").write_text(json.dumps({"release": version}))
        self.app = create_app(self.root, self.token, "http://192.168.77.2:8090")
        self.client = self.app.test_client()
        self.values = dict(mac="52:54:00:12:34:56", release="24.04", hostname="iss-lab",
                           username="operator", password="test-password-only", disk="/dev/vda",
                           erase_confirmation="52:54:00:12:34:56")

    def arm(self, **changes):
        return self.client.post("/api/deploy", json=dict(self.values, **changes), headers=self.headers)

    def claim(self):
        response = self.client.get("/boot.ipxe?mac=" + self.values["mac"])
        key = re.search(r"/deployment/([^/]+)/", response.text)[1]
        return key, response

    def test_authentication_and_erase_confirmation(self):
        self.assertEqual(self.client.get("/api/devices").status_code, 401)
        self.assertEqual(self.arm(erase_confirmation="wrong").status_code, 400)
        self.assertEqual(self.client.get("/boot.ipxe?mac=" + self.values["mac"]).text, "#!ipxe\nexit\n")
        self.assertEqual(self.arm().status_code, 201)
        data = self.client.get("/api/devices", headers=self.headers).text
        self.assertNotIn("password", data)
        self.assertNotIn("token", data)

    def test_all_releases_seed_and_shell_syntax(self):
        for release in RELEASES:
            with self.subTest(release=release):
                self.assertEqual(self.arm(release=release).status_code, 201)
                key, boot = self.claim()
                self.assertIn("/media/" + release, boot.text)
                response = self.client.get(f"/deployment/{key}/user-data")
                self.assertEqual(response.status_code, 200)
                config = yaml.safe_load(response.text)["autoinstall"]
                self.assertNotIn("interactive-sections", config)
                self.assertIn("ubuntu-desktop", config["packages"])
                self.assertTrue(config["identity"]["password"].startswith("$6$"))
                self.assertNotIn(self.values["password"], response.text)
                self.assertEqual(config["storage"]["layout"]["match"]["path"], "/dev/vda")
                for command in config["early-commands"] + config["late-commands"] + config["error-commands"]:
                    subprocess.run(["bash", "-n"], input=command, text=True, check=True)
                script = config["user-data"]["write_files"][1]["content"]
                subprocess.run(["bash", "-n"], input=script, text=True, check=True)
                self.client.post("/api/cancel", json={"mac": self.values["mac"]}, headers=self.headers)

    def test_concurrent_boot_is_one_shot_and_persistent(self):
        self.arm()
        def boot(_):
            with self.app.test_client() as client:
                return client.get("/boot.ipxe?mac=" + self.values["mac"]).text
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(boot, range(4)))
        self.assertEqual(sum("autoinstall" in response for response in responses), 1)
        restarted = create_app(self.root, self.token).test_client()
        self.assertEqual(restarted.get("/boot.ipxe?mac=" + self.values["mac"]).text, "#!ipxe\nexit\n")
        self.assertEqual(self.arm().status_code, 400)

    def test_status_lifecycle_and_revoked_seed(self):
        self.arm()
        key, _ = self.claim()
        url = f"/deployment/{key}/status"
        self.assertEqual(self.client.post(url, json={"state": "completed"}).status_code, 409)
        for state in ("installing", "first_boot", "completed", "completed"):
            self.assertEqual(self.client.post(url, json={"state": state}).status_code, 200)
        self.assertEqual(self.client.get(f"/deployment/{key}/user-data").status_code, 404)
        self.assertEqual(self.client.get("/boot.ipxe?mac=" + self.values["mac"]).text, "#!ipxe\nexit\n")

    def test_cancel_rotates_token(self):
        self.arm()
        key, _ = self.claim()
        self.client.post("/api/cancel", json={"mac": self.values["mac"]}, headers=self.headers)
        self.assertEqual(self.client.get(f"/deployment/{key}/user-data").status_code, 404)
        self.assertEqual(self.client.post(f"/deployment/{key}/status", json={"state": "failed"}).status_code, 404)
        self.assertEqual(self.arm().status_code, 201)
        new_key, _ = self.claim()
        self.assertNotEqual(key, new_key)

    def test_static_network_and_invalid_inputs(self):
        config = validate_device(dict(self.values, mode="static", address="192.168.77.20/24",
                                      gateway="192.168.77.1", dns="1.1.1.1,8.8.8.8"))
        config["token"] = "test"
        rendered = yaml.safe_load(seed(config, "http://192.168.77.2:8090"))["autoinstall"]
        network = yaml.safe_load(base64.b64decode(rendered["late-commands"][1].split()[1]))
        self.assertEqual(network["network"]["renderer"], "NetworkManager")
        self.assertEqual(network["network"]["ethernets"]["pxe"]["addresses"], ["192.168.77.20/24"])
        for changes in ({"disk": "/dev/vda1"}, {"mac": "ff:ff:ff:ff:ff:ff"},
                        {"username": "root"}, {"password": "short"}, {"release": "26.04"}):
            with self.assertRaises(ValueError):
                validate_device(dict(self.values, **changes))

    def test_package_compatibility_and_checksum(self):
        digest = "a" * 64
        folder = self.root / "packages" / digest
        folder.mkdir(parents=True)
        (folder / "package.deb").write_bytes(b"test-only")
        (folder / "manifest.json").write_text(json.dumps({"sha256": digest, "releases": ["24.04"]}))
        self.assertEqual(self.arm(package=digest, release="20.04").status_code, 400)
        self.assertEqual(self.arm(package=digest).status_code, 201)
        key, _ = self.claim()
        config = yaml.safe_load(self.client.get(f"/deployment/{key}/user-data").text)["autoinstall"]
        script = config["user-data"]["write_files"][1]["content"]
        self.assertIn(digest, script)
        self.assertIn("sha256sum -c -", script)
        subprocess.run(["bash", "-n"], input=script, text=True, check=True)
        with self.client.get(f"/deployment/{key}/package.deb") as response:
            self.assertEqual(response.data, b"test-only")

    def test_placeholder_secret_rejected(self):
        with self.assertRaises(RuntimeError):
            create_app(self.root, "replace-this-with-a-random-token-before-start")

    def test_real_deb_import_is_atomic_and_immutable(self):
        tree = self.root / "deb-tree"
        (tree / "DEBIAN").mkdir(parents=True)
        (tree / "DEBIAN/control").write_text(
            "Package: iss3\nVersion: 0.0.1\nArchitecture: amd64\n"
            "Maintainer: Test <test@example.invalid>\nDescription: Import test fixture only\n")
        source = self.root / "fixture.deb"
        subprocess.run(["dpkg-deb", "--build", str(tree), str(source)], check=True, capture_output=True)
        args = Namespace(kind="deb", file=str(source), sha256=checksum(source), compatible=["24.04"])
        with patch.dict(os.environ, {"PXE_DATA": str(self.root)}):
            import_asset(args)
            destination = self.root / "packages" / args.sha256
            self.assertEqual(checksum(destination / "package.deb"), args.sha256)
            with self.assertRaises(ValueError):
                import_asset(args)
            args.sha256 = "0" * 64
            with self.assertRaises(ValueError):
                import_asset(args)
            self.assertEqual(len(list((self.root / "packages").iterdir())), 1)

    def test_dnsmasq_config(self):
        env = dict(PXE_INTERFACE="eth0", PXE_SERVER_CIDR="192.168.77.2/24",
                   PXE_DHCP_START="192.168.77.100", PXE_DHCP_END="192.168.77.200",
                   PXE_GATEWAY="192.168.77.1", PXE_DNS="1.1.1.1", PXE_BASE_URL="http://192.168.77.2:8090")
        with patch.dict(os.environ, env):
            config = dnsmasq()
            path = self.root / "dnsmasq.conf"
            path.write_text(config)
            subprocess.run(["dnsmasq", "--test", "-C", str(path)], check=True)
            for name in ("ipxe.efi", "undionly.kpxe"):
                self.assertTrue((Path("/usr/lib/ipxe") / name).is_file())
            with patch.dict(os.environ, {"PXE_DHCP_START": "192.168.77.1"}):
                with self.assertRaises(ValueError):
                    dnsmasq()


if __name__ == "__main__":
    unittest.main()
