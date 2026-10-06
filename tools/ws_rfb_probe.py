#!/usr/bin/env python3
"""
Deep WS+RFB probe: connects as a real noVNC client would, logs every frame.

Usage:
    sudo .venv/bin/python tools/ws_rfb_probe.py test1
"""
import base64
import hashlib
import os
import select
import socket
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import vm as _vm

_G = "\033[32m"; _R = "\033[31m"; _Y = "\033[33m"; _C = "\033[36m"; _Z = "\033[0m"
def ok(m):   print(_G + "  OK  " + _Z + m)
def fail(m): print(_R + "  FAIL " + _Z + m)
def info(m): print(_C + "  ..  " + _Z + m)

# ── minimal WebSocket client ─────────────────────────────────────────────────

def _ws_connect(host, port, path, timeout=5):
    """Return a connected raw socket after WS handshake, or raise."""
    s = socket.create_connection((host, port), timeout=timeout)
    key = base64.b64encode(os.urandom(16)).decode()
    req = (
        "GET {path} HTTP/1.1\r\n"
        "Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    ).format(path=path, host=host, port=port, key=key)
    s.sendall(req.encode())
    resp = b""
    s.settimeout(timeout)
    while b"\r\n\r\n" not in resp:
        resp += s.recv(4096)
    first = resp.decode("ascii", "replace").split("\r\n")[0]
    if "101" not in first:
        raise RuntimeError("WS handshake failed: " + first)
    return s


def _ws_recv_frame(s, timeout=5):
    """Read one WebSocket frame, return (opcode, payload_bytes)."""
    s.settimeout(timeout)
    def recv_exact(n):
        buf = b""
        while len(buf) < n:
            chunk = s.recv(n - len(buf))
            if not chunk:
                raise EOFError("socket closed")
            buf += chunk
        return buf
    b0, b1 = recv_exact(2)
    # fin = (b0 & 0x80) != 0
    opcode = b0 & 0x0f
    masked = (b1 & 0x80) != 0
    length = b1 & 0x7f
    if length == 126:
        length = struct.unpack(">H", recv_exact(2))[0]
    elif length == 127:
        length = struct.unpack(">Q", recv_exact(8))[0]
    mask = recv_exact(4) if masked else b""
    payload = recv_exact(length)
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return opcode, payload


def _ws_send_binary(s, data):
    """Send a binary WebSocket frame (client-side: masked)."""
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    length = len(data)
    if length < 126:
        header = struct.pack("BB", 0x82, 0x80 | length) + mask
    elif length < 65536:
        header = struct.pack("BBH", 0x82, 0xFE, length) + mask
    else:
        header = struct.pack("BBQ", 0x82, 0xFF, length) + mask
    s.sendall(header + masked)


# ── RFB handshake (client side) ──────────────────────────────────────────────

def probe_rfb_over_ws(s):
    """
    Walk through RFB 3.8 handshake as a client:
      server → ProtocolVersion
      client → ProtocolVersion
      server → SecurityTypes
      client → SecurityType (1=None)
      server → SecurityResult
    Log each step. Return True if handshake reaches SecurityResult=OK.
    """
    # Step 1: server sends protocol version (12 bytes)
    try:
        op, payload = _ws_recv_frame(s, timeout=5)
        info("RFB ProtocolVersion frame: opcode={} len={} data={!r}".format(
            op, len(payload), payload))
        if not payload.startswith(b"RFB "):
            fail("Expected RFB banner, got: {!r}".format(payload[:20]))
            return False
        ok("Server version: " + payload.decode("ascii","replace").strip())
    except Exception as e:
        fail("No RFB version banner from server: {}".format(e))
        return False

    # Step 2: client sends version back (use 3.8)
    _ws_send_binary(s, b"RFB 003.008\n")
    info("Sent: RFB 003.008")

    # Step 3: server sends security types
    try:
        op, payload = _ws_recv_frame(s, timeout=5)
        info("SecurityTypes frame: opcode={} len={} data={!r}".format(
            op, len(payload), payload[:20]))
        if len(payload) < 1:
            fail("Empty SecurityTypes response")
            return False
        n = payload[0]
        if n == 0:
            # error
            reason_len = struct.unpack(">I", payload[1:5])[0]
            reason = payload[5:5+reason_len].decode("utf-8","replace")
            fail("Server refused connection: " + reason)
            return False
        types = list(payload[1:1+n])
        ok("SecurityTypes: n={} types={}".format(n, types))
    except Exception as e:
        fail("Failed reading SecurityTypes: {}".format(e))
        return False

    # Step 4: client chooses security type 1 (None) if available, else 2 (VNCAuth)
    if 1 in types:
        chosen = 1
        info("Choosing SecurityType 1 (None)")
    elif 2 in types:
        chosen = 2
        info("Choosing SecurityType 2 (VNCAuth)")
    else:
        fail("No supported security type in {}".format(types))
        return False
    _ws_send_binary(s, bytes([chosen]))

    # Step 5: security result (for type 1 in RFB 3.8, server still sends result)
    # For VNCAuth we'd need to do the challenge — just check the result byte
    try:
        op, payload = _ws_recv_frame(s, timeout=5)
        info("SecurityResult frame: opcode={} len={} data={!r}".format(
            op, len(payload), payload[:8]))
        if len(payload) >= 4:
            result = struct.unpack(">I", payload[:4])[0]
            if result == 0:
                ok("SecurityResult: OK (0) — RFB handshake succeeded!")
                return True
            else:
                reason = payload[8:].decode("utf-8","replace") if len(payload) > 8 else "?"
                fail("SecurityResult: FAILED ({}) reason={}".format(result, reason))
                return False
        else:
            info("Short SecurityResult payload ({} bytes) — may be VNCAuth challenge".format(len(payload)))
            return None  # inconclusive
    except Exception as e:
        fail("Failed reading SecurityResult: {}".format(e))
        return False


# ── direct TCP RFB probe ─────────────────────────────────────────────────────

def probe_rfb_direct(host, port):
    """Same handshake directly on TCP (bypassing our WS proxy)."""
    info("Direct TCP RFB probe to {}:{}".format(host, port))
    try:
        s = socket.create_connection((host, port), timeout=5)
        banner = s.recv(12)
        if not banner.startswith(b"RFB "):
            fail("Direct: no RFB banner: {!r}".format(banner))
            s.close(); return False
        ok("Direct TCP banner: " + banner.decode("ascii","replace").strip())
        s.close()
        return True
    except Exception as e:
        fail("Direct TCP failed: {}".format(e))
        return False


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: sudo .venv/bin/python tools/ws_rfb_probe.py <vmname>")
        sys.exit(1)
    name = sys.argv[1]
    app_host = os.environ.get("VM_HOST", "127.0.0.1:8088")
    h, _, p = app_host.partition(":")
    h = h or "127.0.0.1"; p = int(p) if p else 8088

    print("\n\033[1m═══ WS+RFB probe for VM: {} ═══\033[0m\n".format(name))

    # 1. Get VM's VNC port from config
    v = _vm.vm_detail(name)
    if v is None:
        fail("VM not found"); sys.exit(1)
    vnc_host = v.get("graphics_listen") or "127.0.0.1"
    vnc_port = v.get("graphics_port") or 5900
    info("VNC target: {}:{}".format(vnc_host, vnc_port))
    info("WS server:  {}:{}".format(h, p))

    # 2. Direct TCP RFB (sanity check bhyve end)
    print("\n\033[1m[A] Direct TCP RFB (bhyve)\033[0m")
    probe_rfb_direct(vnc_host, vnc_port)

    # 3. WebSocket RFB (through our proxy)
    print("\n\033[1m[B] WS proxy RFB (our /ws/vnc/{} endpoint)\033[0m".format(name))
    try:
        ws = _ws_connect(h, p, "/ws/vnc/{}".format(name), timeout=5)
        ok("WebSocket connected to ws://{}:{}/ws/vnc/{}".format(h, p, name))
        result = probe_rfb_over_ws(ws)
        ws.close()
    except Exception as e:
        fail("Could not connect WebSocket: {}".format(e))
        sys.exit(1)

    print()
    if result is True:
        ok("End-to-end RFB over WebSocket works — the proxy is functional.")
        info("If noVNC still shows Disconnected, the issue is in the browser JS.")
        info("Open browser DevTools → Console and Network tabs, reload the console page,")
        info("and look for: JS errors, failed module imports, or WS close codes.")
    elif result is False:
        fail("RFB handshake failed through WebSocket proxy.")
        info("The Python proxy is not correctly forwarding RFB bytes.")
    else:
        info("Handshake inconclusive (VNCAuth challenge) — bhyve requires a VNC password.")
        info("Set graphics_passwd= in the VM config or leave it blank.")

if __name__ == "__main__":
    main()
