#!/usr/bin/env python3
"""VNC / noVNC diagnostic tool for html-vm.

Checks every layer of the VNC console stack for a named VM and prints a
pass/fail summary with fix hints.

Usage (must be root):
    sudo .venv/bin/python tools/vnc_diag.py <vmname>

Exit code:
    0  all checks pass
    1  one or more checks failed
"""
import os
import re
import select
import socket
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import vm as _vm

# ── ANSI colours ────────────────────────────────────────────────────────────
_GREEN  = "\033[32m"
_RED    = "\033[31m"
_YELLOW = "\033[33m"
_CYAN   = "\033[36m"
_BOLD   = "\033[1m"
_RESET  = "\033[0m"

def _ok(msg):   print("  {0}✔  {1}{2}".format(_GREEN,  msg, _RESET))
def _fail(msg): print("  {0}✗  {1}{2}".format(_RED,    msg, _RESET))
def _warn(msg): print("  {0}⚠  {1}{2}".format(_YELLOW, msg, _RESET))
def _info(msg): print("  {0}·  {1}{2}".format(_CYAN,   msg, _RESET))
def _head(msg): print("\n{0}{1}{2}".format(_BOLD, msg, _RESET))

# ── helpers ──────────────────────────────────────────────────────────────────

def _run(*args, timeout=10):
    try:
        p = subprocess.run(list(args), capture_output=True, text=True, timeout=timeout)
        return (p.stdout + p.stderr).strip(), p.returncode
    except Exception as e:
        return str(e), 1


def _tcp_open(host, port, timeout=3):
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except OSError:
        return False


def _rfb_handshake(host, port, timeout=4):
    """Connect to VNC, read the RFB banner, return (version_string, error)."""
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.settimeout(timeout)
        banner = s.recv(12)
        s.close()
        if banner.startswith(b"RFB "):
            return banner.decode("ascii", "replace").strip(), None
        return None, "No RFB banner (got {0!r})".format(banner[:12])
    except OSError as e:
        return None, str(e)


def _novnc_files_ok(novnc_root):
    required = [
        "core/rfb.js",
        "core/websock.js",
    ]
    missing = []
    for f in required:
        if not os.path.isfile(os.path.join(novnc_root, f)):
            missing.append(f)
    return missing


def _ws_endpoint_responds(host, port, vm_name, timeout=5):
    """
    Send a raw HTTP WebSocket upgrade to /ws/vnc/<name> and check we get
    a 101 Switching Protocols back (not 400 or 403).
    Returns (status_line, error).
    """
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.settimeout(timeout)
        key = "dGhlIHNhbXBsZSBub25jZQ=="   # well-known test nonce
        req = (
            "GET /ws/vnc/{name} HTTP/1.1\r\n"
            "Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        ).format(name=vm_name, host=host, port=port, key=key)
        s.sendall(req.encode())
        resp = b""
        deadline = time.time() + timeout
        while b"\r\n\r\n" not in resp and time.time() < deadline:
            try:
                chunk = s.recv(4096)
                if not chunk:
                    break
                resp += chunk
            except socket.timeout:
                break
        s.close()
        first_line = resp.decode("ascii", "replace").split("\r\n")[0]
        return first_line, None
    except OSError as e:
        return None, str(e)


# ── checks ───────────────────────────────────────────────────────────────────

def check_vm_state(name):
    _head("1. VM state")
    vms, err = _vm.list_vms()
    if err:
        _warn("list_vms returned error: " + err)
    v = _vm.vm_detail(name, vms)
    if v is None:
        _fail("VM '{0}' not found — is vm-bhyve running and is the name correct?".format(name))
        return None
    state = v.get("state", "?")
    running = v.get("running", False)
    loading = v.get("loading", False)
    _info("state        = {0}".format(state))
    _info("running      = {0}".format(running))
    _info("loading      = {0} (bootloader active, bhyve not yet started)".format(loading))
    _info("loader       = {0}".format(v.get("loader", "?")))
    _info("graphics     = {0}".format(v.get("graphics", "?")))
    _info("graphics_port= {0}".format(v.get("graphics_port", "?")))
    _info("graphics_listen={0}".format(v.get("graphics_listen", "?")))

    if not running:
        _fail("VM is not running — start it first with: sudo vm start {0}".format(name))
        return None
    if loading:
        _fail(
            "VM is in 'bootloader' state — bhyve hasn't started yet.\n"
            "     The VNC framebuffer is only available once bhyve itself is running.\n"
            "     Cause: bhyveload loader does not expose VNC. Use loader=uefi for VNC.\n"
            "     Or wait until boot finishes (state changes to 'Running')."
        )
    loader = v.get("loader", "")
    if loader == "bhyveload" and (v.get("graphics") or "no") not in ("no", ""):
        _warn(
            "loader=bhyveload with graphics=yes — bhyveload does not open VNC.\n"
            "     Fix: change loader to 'uefi' in the VM config and restart."
        )
    elif (v.get("graphics") or "no").lower() in ("", "no", "off", "false", "0"):
        _fail("graphics='no' in VM config — VNC is disabled. Set graphics=yes and restart.")
    else:
        if not loading:
            _ok("VM is running with graphics enabled")
    return v


def check_vnc_port(v):
    _head("2. VNC TCP port")
    host = v.get("graphics_listen") or "127.0.0.1"
    port = v.get("graphics_port") or 5900
    _info("Checking {0}:{1} ...".format(host, port))

    # Also check sockstat
    out, _ = _run("sockstat", "-4", "-6", "-l")
    vnc_lines = [l for l in out.splitlines() if str(port) in l]
    if vnc_lines:
        for l in vnc_lines:
            _info("sockstat: " + l.strip())
    else:
        _info("sockstat: nothing listening on :{0}".format(port))

    if _tcp_open(host, port):
        _ok("TCP port {0}:{1} is open".format(host, port))
        banner, err = _rfb_handshake(host, port)
        if banner:
            _ok("RFB handshake: " + banner)
        else:
            _fail("RFB handshake failed: " + (err or "no banner"))
        return True
    else:
        _fail(
            "TCP port {0}:{1} is closed.\n"
            "     bhyve has not opened the VNC listener yet.\n"
            "     Causes: VM still in bootloader state; wrong port in config;\n"
            "             bhyve crashed immediately after start.".format(host, port)
        )
        # Check if bhyve process is alive
        pid = v.get("pid")
        if pid:
            out2, rc2 = _run("ps", "-p", str(pid), "-o", "pid=,comm=,state=")
            if rc2 == 0:
                _info("bhyve pid {0} exists: {1}".format(pid, out2.strip()))
            else:
                _warn("pid {0} not found in ps — bhyve may have already exited".format(pid))
        return False


def check_conf_file(name):
    _head("3. VM config file")
    path = _vm.conf_path(name)
    if not path:
        _fail("Cannot locate config file for {0}".format(name))
        return
    _info("config path: " + path)
    cfg = _vm.read_conf(name)

    # Check disk1_dev double-path bug
    disk1_dev  = cfg.get("disk1_dev", "")
    disk1_name = cfg.get("disk1_name", "")
    if disk1_dev == "file" and disk1_name.startswith("/"):
        _fail(
            "disk1_dev=\"file\" with absolute disk1_name — vm-bhyve will prepend the\n"
            "     datastore path, creating a doubled path like /var/vm/name//absolute/path.\n"
            "     Fix: change disk1_dev to \"custom\".\n"
            "     Run:  sudo sed -i '' 's/disk1_dev=\"file\"/disk1_dev=\"custom\"/' {0}".format(path)
        )
    elif disk1_name:
        _ok("disk1_dev = \"{0}\" (ok for path: {1})".format(disk1_dev or "(unset)", disk1_name))

    # graphics fields
    gfx = cfg.get("graphics", "no")
    gport = cfg.get("graphics_port", "")
    glisten = cfg.get("graphics_listen", "127.0.0.1")
    _info("graphics       = {0}".format(gfx))
    _info("graphics_port  = {0}".format(gport or "(not set — bhyve picks random)"))
    _info("graphics_listen= {0}".format(glisten))

    if gfx.lower() in ("", "no", "off", "false", "0"):
        _fail("graphics=no — VNC will not start. Set graphics=yes and restart the VM.")
    else:
        _ok("graphics={0}".format(gfx))

    if not gport:
        _warn("graphics_port not set — bhyve picks a random port. Set a fixed port (e.g. 5900).")
    elif not gport.isdigit():
        _fail("graphics_port={0} is not a number".format(gport))
    else:
        _ok("graphics_port={0}".format(gport))

    loader = cfg.get("loader", "")
    if loader == "bhyveload":
        _fail(
            "loader=bhyveload — bhyveload does not support VNC graphics.\n"
            "     Fix: set loader=uefi in the config and restart the VM."
        )
    else:
        _ok("loader={0} (supports VNC)".format(loader))


def check_novnc(novnc_root):
    _head("4. noVNC installation")
    _info("NOVNC_ROOT = " + novnc_root)
    if not os.path.isdir(novnc_root):
        _fail("noVNC directory not found: " + novnc_root)
        _info("Install: sudo pkg install -y novnc  OR  set NOVNC_ROOT env var")
        return
    missing = _novnc_files_ok(novnc_root)
    if missing:
        _fail("Missing noVNC files: " + ", ".join(missing))
    else:
        _ok("core/rfb.js and core/websock.js present")

    # Check novnc route is reachable via HTTP (not WS)
    app_host = os.environ.get("VM_HOST", "127.0.0.1:8088")
    if ":" in app_host:
        h, _, p = app_host.partition(":")
    else:
        h, p = app_host, "8088"
    h = h or "127.0.0.1"
    url_path = "/novnc/core/rfb.js"
    try:
        s = socket.create_connection((h, int(p)), timeout=3)
        req = "GET {path} HTTP/1.0\r\nHost: {host}:{port}\r\n\r\n".format(
            path=url_path, host=h, port=p)
        s.sendall(req.encode())
        s.settimeout(3)
        resp = s.recv(256)
        s.close()
        status = resp.decode("ascii", "replace").split("\r\n")[0]
        if "200" in status:
            _ok("GET {0} → {1}".format(url_path, status))
        else:
            _fail("GET {0} → {1}".format(url_path, status))
    except OSError as e:
        _warn("Could not reach app server at {0}:{1} — is run.py running? ({2})".format(h, p, e))


def check_ws_endpoint(name):
    _head("5. WebSocket /ws/vnc/{0} endpoint".format(name))
    app_host = os.environ.get("VM_HOST", "127.0.0.1:8088")
    if ":" in app_host:
        h, _, p = app_host.partition(":")
    else:
        h, p = app_host, "8088"
    h = h or "127.0.0.1"
    _info("Probing ws://{0}:{1}/ws/vnc/{2}".format(h, p, name))
    status, err = _ws_endpoint_responds(h, int(p), name)
    if err:
        _warn("Could not reach app server: " + err)
        return
    _info("HTTP response: " + (status or "(empty)"))
    if status and "101" in status:
        _ok("WebSocket upgrade accepted (101 Switching Protocols)")
    elif status and "400" in status:
        _fail(
            "Got 400 — server rejected the upgrade.\n"
            "     Likely cause: app is not running under gevent+geventwebsocket\n"
            "     (Werkzeug dev server cannot handle WebSocket upgrades).\n"
            "     Fix: run with  sudo .venv/bin/python run.py"
        )
    elif status and "403" in status:
        _fail(
            "Got 403 — CSRF / auth check failed on the WS route.\n"
            "     The WS route uses _ws_authed() not the CSRF hook — check session cookie handling."
        )
    elif status and "302" in status:
        _fail("Got 302 redirect — session/auth not carried into the WS request.")
    else:
        _warn("Unexpected response: " + (status or "(none)"))


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: sudo .venv/bin/python tools/vnc_diag.py <vmname>")
        sys.exit(1)

    name = sys.argv[1]
    print("{0}═══ VNC diagnostic for VM: {1} ═══{2}".format(_BOLD, name, _RESET))

    failures = []

    # 1. VM state
    v = check_vm_state(name)
    if v is None:
        failures.append("VM not found or not running")
    else:
        # 2. VNC TCP port
        vnc_ok = check_vnc_port(v)
        if not vnc_ok:
            failures.append("VNC TCP port not open")

    # 3. Config file
    check_conf_file(name)

    # 4. noVNC files
    novnc_root = os.environ.get("NOVNC_ROOT", "/usr/local/libexec/novnc")
    check_novnc(novnc_root)

    # 5. WebSocket endpoint
    check_ws_endpoint(name)

    # ── summary ──────────────────────────────────────────────────────────────
    _head("═══ Summary ═══")
    if not failures:
        _ok("All checks passed — VNC stack looks healthy.")
        print()
        return 0
    else:
        for f in failures:
            _fail(f)
        print()
        _info("See hints above for fixes. Common quick fixes:")
        _info("  1. loader must be 'uefi' for VNC:  edit /var/vm/{0}/{0}.conf, set loader=uefi, restart VM".format(name))
        _info("  2. Fix double ISO path:  sed -i '' 's/disk1_dev=\"file\"/disk1_dev=\"custom\"/' /var/vm/{0}/{0}.conf".format(name))
        _info("  3. Run server correctly:  sudo .venv/bin/python run.py")
        print()
        return 1


if __name__ == "__main__":
    sys.exit(main())
