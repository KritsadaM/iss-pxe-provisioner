"""Web control plane and one-shot MAC-bound PXE boot service."""
import json
from contextlib import contextmanager
import os
from pathlib import Path
import secrets
import sqlite3
import time
from urllib.parse import urlsplit

from flask import Flask, abort, jsonify, render_template, request, send_from_directory
from provision import RELEASES, ipxe, mac, seed, validate_device


def create_app(data=None, token=None, base=None):
    app = Flask(__name__)
    directory = Path(data or os.environ.get("PXE_DATA", "/data"))
    directory.mkdir(parents=True, exist_ok=True)
    token = token or os.environ.get("PXE_ADMIN_TOKEN")
    if not token or len(token) < 24 or token.startswith("replace-this"):
        raise RuntimeError("Set PXE_ADMIN_TOKEN to a random string of at least 24 characters")
    base = (base or os.environ.get("PXE_BASE_URL", "http://192.168.77.2:8090")).rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.query or parsed.fragment or parsed.path:
        raise RuntimeError("PXE_BASE_URL must be an absolute HTTP(S) origin")
    app.config["MAX_CONTENT_LENGTH"] = 16384
    dbpath = directory / "deployments.sqlite"

    @contextmanager
    def db():
        connection = sqlite3.connect(dbpath, timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    with db() as con:
        con.execute("CREATE TABLE IF NOT EXISTS devices (mac TEXT PRIMARY KEY, token TEXT UNIQUE, state TEXT, config TEXT, updated REAL)")
        con.execute("CREATE TABLE IF NOT EXISTS events (mac TEXT, state TEXT, at REAL)")
    dbpath.chmod(0o600)

    def catalog(folder):
        output = {}
        for path in (directory / folder).glob("*/manifest.json"):
            record = json.loads(path.read_text())
            files = ("installer.iso", "vmlinuz", "initrd") if folder == "media" else ("package.deb",)
            if all((path.parent / name).is_file() for name in files):
                output[path.parent.name] = record
        return output

    def deployment(key):
        with db() as con:
            row = con.execute("SELECT * FROM devices WHERE token=?", (key,)).fetchone()
        if not row or row["state"] not in ("booting", "installing", "first_boot"):
            abort(404)
        config = json.loads(row["config"])
        config.update(token=key)
        return config

    @app.before_request
    def authenticate():
        if request.path.startswith("/api/"):
            value = request.headers.get("Authorization", "")
            supplied = value[7:] if value.startswith("Bearer ") else ""
            if not secrets.compare_digest(supplied, token):
                abort(401)

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'"
        return response

    @app.errorhandler(ValueError)
    @app.errorhandler(KeyError)
    def invalid(error):
        return jsonify(error=str(error)), 400

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    @app.get("/")
    def index():
        return render_template("portal.html", releases=RELEASES)

    @app.get("/api/catalog")
    def assets():
        return jsonify(images=catalog("media"), packages=catalog("packages"), base_url=base)

    @app.get("/api/devices")
    def devices():
        with db() as con:
            rows = con.execute("SELECT * FROM devices ORDER BY updated DESC").fetchall()
        results = []
        for row in rows:
            config = json.loads(row["config"])
            config.pop("password_hash", None)
            results.append(dict(config, state=row["state"], updated=row["updated"]))
        return jsonify(results)

    @app.post("/api/deploy")
    def deploy():
        values = request.get_json()
        device = validate_device(values)
        if values.get("erase_confirmation") != device["mac"]:
            raise ValueError("Confirm disk erasure by entering the exact target MAC")
        if device["release"] not in catalog("media"):
            raise ValueError("Import and verify the matching live-server ISO first")
        if device["package"]:
            package = catalog("packages").get(device["package"])
            if not package or device["release"] not in package["releases"]:
                raise ValueError("Select an ISS3 package registered for this Ubuntu version")
            device["package_sha256"] = package["sha256"]
        now = time.time()
        with db() as con:
            con.execute("BEGIN IMMEDIATE")
            existing = con.execute("SELECT state FROM devices WHERE mac=?", (device["mac"],)).fetchone()
            if existing and existing["state"] in ("booting", "installing", "first_boot"):
                raise ValueError("Deployment is active; cancel it before rearming")
            con.execute("INSERT OR REPLACE INTO devices VALUES (?,?,?,?,?)",
                        (device["mac"], secrets.token_urlsafe(32), "armed", json.dumps(device), now))
            con.execute("INSERT INTO events VALUES (?,?,?)", (device["mac"], "armed", now))
        return jsonify(state="armed", mac=device["mac"]), 201

    @app.post("/api/cancel")
    def cancel():
        address = mac(request.get_json()["mac"])
        with db() as con:
            con.execute("UPDATE devices SET state='cancelled', token=?, updated=? WHERE mac=?",
                        (secrets.token_urlsafe(32), time.time(), address))
            con.execute("INSERT INTO events VALUES (?,?,?)", (address, "cancelled", time.time()))
        return jsonify(state="cancelled", note="Revokes boot/seed access; it cannot stop commands already running on the target")

    @app.get("/boot.ipxe")
    def boot():
        # First chain provides a MAC; firmware DHCP option 175 breaks the iPXE loop.
        if not request.args.get("mac"):
            return app.response_class("#!ipxe\nchain " + base + "/boot.ipxe?mac=${net0/mac}\n", mimetype="text/plain")
        address = mac(request.args["mac"])
        device = None
        with db() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM devices WHERE mac=? AND state='armed'", (address,)).fetchone()
            if row:
                device = json.loads(row["config"])
                if device["release"] not in catalog("media"):
                    abort(503)
                device["token"] = row["token"]
                con.execute("UPDATE devices SET state='booting', updated=? WHERE mac=?", (time.time(), address))
                con.execute("INSERT INTO events VALUES (?,?,?)", (address, "booting", time.time()))
        return app.response_class(ipxe(device, base), mimetype="text/plain")

    @app.get("/deployment/<key>/user-data")
    def userdata(key):
        device = deployment(key)
        package = {"sha256": device["package_sha256"]} if device["package"] else None
        return app.response_class(seed(device, base, package), mimetype="text/yaml")

    @app.get("/deployment/<key>/meta-data")
    def metadata(key):
        device = deployment(key)
        return "instance-id: " + key + "\nlocal-hostname: " + device["hostname"] + "\n"

    @app.get("/deployment/<key>/package.deb")
    def package_file(key):
        device = deployment(key)
        if not device["package"]:
            abort(404)
        return send_from_directory(directory / "packages" / device["package"], "package.deb", conditional=True)

    @app.post("/deployment/<key>/status")
    def status(key):
        target = request.get_json().get("state")
        transitions = {"booting": ("installing", "failed"), "installing": ("first_boot", "failed"),
                       "first_boot": ("completed", "failed")}
        with db() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT mac,state FROM devices WHERE token=?", (key,)).fetchone()
            if not row:
                abort(404)
            if target == row["state"]:
                return jsonify(state=target)
            if target not in transitions.get(row["state"], ()):
                abort(409)
            con.execute("UPDATE devices SET state=?,updated=? WHERE token=?", (target, time.time(), key))
            con.execute("INSERT INTO events VALUES (?,?,?)", (row["mac"], target, time.time()))
        return jsonify(state=target)

    @app.get("/media/<version>/<name>")
    def media(version, name):
        if version not in RELEASES or name not in ("installer.iso", "vmlinuz", "initrd"):
            abort(404)
        return send_from_directory(directory / "media" / version, name, conditional=True)

    return app
