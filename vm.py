"""vm-bhyve integration layer.

Runs the ``vm`` CLI (as root, or via ``sudo -n`` when unprivileged) and parses
its text output into plain dicts.  The output parsers are pure functions and
are unit-tested against saved fixtures in ``tests/fixtures`` so no running
hypervisor is required to test them.
"""
import os
import re
import shutil
import socket as _socket
import subprocess
import threading

VM_BIN = "/usr/local/sbin/vm"

# Default datastore path – overridden by VM_DATASTORE env var or read from
# the system.conf when a real vm is running.
VM_DATASTORE = os.environ.get("VM_DATASTORE", "/var/vm")
ISO_DIR = os.path.join(VM_DATASTORE, ".iso")

#: vm-bhyve guest names: lower case, start/end alphanumeric.
VM_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9._-]{0,62}[a-z0-9])?$")
SWITCH_NAME_RE = VM_NAME_RE

BYTES_MAP = {
    "bytes-size": ("size_bytes", "size_human"),
    "bytes-used": ("used_bytes", "used_human"),
    "bytes-in": ("net_in_bytes", "net_in_human"),
    "bytes-out": ("net_out_bytes", "net_out_human"),
}

# ── OS profiles ────────────────────────────────────────────────────────────
# Each profile is a dict of defaults the UI pre-fills and writes into the conf.
OS_PROFILES = {
    "freebsd": {
        "label": "FreeBSD",
        "loader": "bhyveload",
        "bootrom": "",
        "disk_type": "virtio-blk",
        "nic_type": "virtio-net",
        "console": "serial",
        "utctime": "yes",
    },
    "linux": {
        "label": "Linux (UEFI)",
        "loader": "uefi",
        "bootrom": "",
        "disk_type": "virtio-blk",
        "nic_type": "virtio-net",
        "console": "serial",
        "utctime": "yes",
    },
    "windows": {
        "label": "Windows",
        "loader": "uefi",
        "bootrom": "",
        "disk_type": "ahci-hd",
        "nic_type": "e1000",
        "console": "serial",
        "utctime": "no",
    },
    "openbsd": {
        "label": "OpenBSD",
        "loader": "uefi",
        "bootrom": "",
        "disk_type": "virtio-blk",
        "nic_type": "virtio-net",
        "console": "serial",
        "utctime": "yes",
    },
    "custom": {
        "label": "Custom",
        "loader": "bhyveload",
        "bootrom": "",
        "disk_type": "virtio-blk",
        "nic_type": "virtio-net",
        "console": "serial",
        "utctime": "yes",
    },
}

LOADERS = ["bhyveload", "uefi", "uefi-csm"]
DISK_TYPES = ["virtio-blk", "ahci-hd", "nvme"]
NIC_TYPES = ["virtio-net", "e1000", "vmxnet3"]


# ── Privilege helpers ───────────────────────────────────────────────────────

def _prefix():
    """Command prefix so ``vm`` runs with enough privilege (root)."""
    if os.geteuid() == 0:
        return []
    return ["sudo", "-n"]


def _exec(cmd, timeout=30):
    """Run an arbitrary command (argv list, no shell); return (output, rc)."""
    if shutil.which(cmd[0]) is None:
        return "error: '{0}' not installed".format(cmd[0]), 127
    try:
        p = subprocess.run(
            cmd, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=timeout,
        )
        return (p.stdout or "") + (p.stderr or ""), p.returncode
    except subprocess.TimeoutExpired:
        return "error: command timed out after {0}s".format(timeout), 124
    except FileNotFoundError:
        return "error: '{0}' not found".format(cmd[0]), 127


def run(args, timeout=60):
    """Run a vm-bhyve ``vm`` subcommand; return (output, rc)."""
    return _exec(_prefix() + [VM_BIN] + list(args), timeout=timeout)


# ── Unit conversion helpers ─────────────────────────────────────────────────

def bytes_value(s):
    """Convert a vm-bhyve size string (e.g. ``1024M``) to bytes (binary)."""
    if not s:
        return None
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*([kmgtp]?)\s*$", str(s), re.IGNORECASE)
    if not m:
        return None
    n = float(m.group(1))
    u = m.group(2).upper()
    return int(n * {
        "": 1, "K": 1 << 10, "M": 1 << 20, "G": 1 << 30,
        "T": 1 << 40, "P": 1 << 50,
    }[u])


def _int(s):
    try:
        return int(str(s).strip())
    except (ValueError, TypeError):
        return None


def _split_bytes(val):
    """'2147483648 (2.000G)' -> (2147483648, '2.000G')."""
    val = (val or "").strip()
    m = re.match(r"^(\d+)\s*\(([^)]*)\)", val)
    if m:
        return int(m.group(1)), m.group(2)
    m = re.match(r"^(\d+)", val)
    if m:
        return int(m.group(1)), val
    return None, val


def fmt_bytes(n):
    """Format a byte count as a human string (e.g. 2.00 GiB)."""
    if n is None:
        return "-"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024:
            return "{0:.2f} {1}".format(n, unit)
        n /= 1024
    return "{0:.2f} PiB".format(n)


# ── VM dict constructor / parsers ───────────────────────────────────────────

def _new_vm(name):
    return {
        "name": name, "state": "unknown", "running": False, "pid": None,
        "datastore": None, "loader": None, "uuid": None, "cpu": None,
        "memory": None, "memory_bytes": None, "memory_resident": None,
        "disks": [], "networks": [],
        "uptime": None, "uptime_seconds": None, "pcpu": None, "rss_human": None,
        "loading": False,
        "nmdm_port": None,   # e.g. /dev/nmdm-<name>.1B from vm info console-ports
    }


def _set_vm_field(vm, key, val):
    val = (val or "").strip()
    if key == "state":
        vm["state"] = val
        low = val.lower()
        # bootloader state = vm process is up, just hasn't handed off yet
        vm["running"] = low.startswith("running") or low.startswith("bootloader")
        vm["loading"] = low.startswith("bootloader")
        m = re.search(r"\((\d+)\)", val)
        if m:
            vm["pid"] = int(m.group(1))
    elif key == "cpu":
        vm["cpu"] = _int(val)
    elif key == "memory":
        vm["memory"] = val
        vm["memory_bytes"] = bytes_value(val)
    elif key == "datastore":
        vm["datastore"] = val
    elif key == "loader":
        vm["loader"] = val
    elif key == "uuid":
        vm["uuid"] = val
    elif key == "memory-resident":
        vm["memory_resident"] = val


def _set_sec_field(sec, key, val):
    val = (val or "").strip()
    if key == "number":
        sec["number"] = _int(val)
        return
    if key in BYTES_MAP:
        bi, hu = BYTES_MAP[key]
        sec[bi] = None
        sec[hu] = None
        n, h = _split_bytes(val)
        if n is not None:
            sec[bi] = n
            sec[hu] = h
        return
    sec[key.replace("-", "_")] = val


def parse_vm_info(text):
    """Parse ``vm info`` (all guests) output into a list of VM dicts."""
    vms = []
    cur = None
    section = None  # ("network", dict) | ("disk", dict) | None
    line_re = re.compile(r"^(\s+)([A-Za-z][A-Za-z0-9_-]*)\s*:\s*(.*)$")
    hdr_re = re.compile(r"^  ([A-Za-z][A-Za-z0-9_.-]*)\s*$")
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if line.strip() == "":
            section = None
            continue
        m = re.match(r"^Virtual Machine:\s*(\S+)\s*$", line)
        if m:
            if cur:
                vms.append(cur)
            cur = _new_vm(m.group(1))
            section = None
            continue
        m = hdr_re.match(line)
        if m and cur is not None:
            hdr = m.group(1)
            if hdr == "network-interface":
                d = {}
                cur["networks"].append(d)
                section = ("network", d)
            elif hdr == "virtual-disk":
                d = {}
                cur["disks"].append(d)
                section = ("disk", d)
            elif hdr == "console-ports":
                section = ("console-ports", cur)
            else:
                section = None
            continue
        m = line_re.match(line)
        if m and cur is not None:
            indent = len(m.group(1))
            key = m.group(2)
            val = m.group(3)
            if indent <= 2:
                _set_vm_field(cur, key, val)
                section = None
            elif section:
                if section[0] == "console-ports":
                    nmdm = (val or "").strip()
                    if nmdm and cur["nmdm_port"] is None:
                        cur["nmdm_port"] = nmdm
                else:
                    _set_sec_field(section[1], key, val)
    if cur:
        vms.append(cur)
    return vms


# ── Uptime / ps helpers ─────────────────────────────────────────────────────

def _uptime_seconds(etime):
    if not etime:
        return None
    etime = etime.strip()
    days = 0
    if "-" in etime:
        d, etime = etime.split("-", 1)
        days = _int(d) or 0
    parts = etime.split(":")
    try:
        parts = [int(p) for p in parts]
    except ValueError:
        return None
    if len(parts) == 1:
        return _int(parts[0]) or 0
    if len(parts) == 2:
        h, m = parts
        s = 0
    elif len(parts) == 3:
        h, m, s = parts
    else:
        return None
    return days * 86400 + h * 3600 + m * 60 + s


def _fmt_uptime(secs):
    if not secs:
        return "-"
    days = secs // 86400
    secs %= 86400
    h = secs // 3600
    secs %= 3600
    m = secs // 60
    s = secs % 60
    if days > 0:
        return "{0}d {1:02d}:{2:02d}:{3:02d}".format(days, h, m, s)
    if h > 0:
        return "{0:02d}:{1:02d}:{2:02d}".format(h, m, s)
    if m > 0:
        return "{0}m {1}s".format(m, s)
    return "{0}s".format(s)


def _fmt_rss(kb):
    if kb is None:
        return None
    f = float(kb)
    for unit in ("K", "M", "G"):
        if f < 1024:
            return "{0:.0f}{1}".format(f, unit)
        f /= 1024
    return "{0:.0f}T".format(f)


def _ps_stats(pid):
    """Return (uptime_str, uptime_seconds, pcpu, rss_human) for a pid."""
    out, rc = _exec(["ps", "-o", "etime=", "-o", "pcpu=", "-o", "rss=",
                     "-p", str(pid)], timeout=5)
    if rc != 0:
        return None, None, None, None
    parts = out.split()
    if len(parts) < 2:
        return None, None, None, None
    etime = parts[0]
    pcpu = parts[1] if len(parts) > 1 else None
    rss = _int(parts[2]) if len(parts) > 2 else None
    secs = _uptime_seconds(etime)
    return _fmt_uptime(secs), secs, pcpu, _fmt_rss(rss)


# ── Config file helpers ─────────────────────────────────────────────────────

def _conf_value(lines, key):
    """Return the unquoted value of ``key=`` from a list of conf lines."""
    for ln in lines:
        m = re.match(r'^{0}\s*=\s*"?([^"#\n]*)"?'.format(re.escape(key)), ln)
        if m:
            return m.group(1).strip()
    return None


def _conf_set(lines, key, value):
    """Return new lines list with key=value set (append if absent)."""
    new_ln = '{0}="{1}"'.format(key, value)
    out = []
    found = False
    for ln in lines:
        if re.match(r'^{0}\s*='.format(re.escape(key)), ln):
            out.append(new_ln)
            found = True
        else:
            out.append(ln)
    if not found:
        out.append(new_ln)
    return out


def _conf_del(lines, key):
    """Return new lines list with all ``key=`` entries removed."""
    return [ln for ln in lines if not re.match(r'^{0}\s*='.format(re.escape(key)), ln)]


def _atomic_write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(data)
    os.replace(tmp, path)


def conf_path(name, vms=None):
    """Locate a VM's config file via its disk ``system-path``."""
    if vms is None:
        vms, _ = list_vms()
    for vm in vms:
        if vm["name"] == name:
            for d in vm["disks"]:
                sp = d.get("system_path")
                if sp:
                    return os.path.join(os.path.dirname(sp), name + ".conf")
    # Fall back: datastore layout
    candidate = os.path.join(VM_DATASTORE, name, name + ".conf")
    if os.path.isfile(candidate):
        return candidate
    return None


def read_conf(name, vms=None):
    """Return a dict of all key=value pairs from a VM's .conf file."""
    path = conf_path(name, vms)
    if not path or not os.path.isfile(path):
        return {}
    result = {}
    with open(path) as f:
        for ln in f:
            ln = ln.strip()
            m = re.match(r'^([a-zA-Z0-9_]+)\s*=\s*"?([^"#\n]*)"?', ln)
            if m:
                result[m.group(1)] = m.group(2).strip()
    return result


# ── VM list / detail ────────────────────────────────────────────────────────

def parse_vm_list_v(text):
    """Parse ``vm list -v`` tabular output into a list of summary dicts.

    Each row becomes::

        {"name": str, "datastore": str, "loader": str, "cpu": int,
         "memory": str, "vnc": str, "auto": str, "pcpu": str,
         "rsz": str, "uptime": str, "state": str, "running": bool}

    The header line is identified by the ``NAME`` column header and is
    skipped; blank lines and comment lines are also skipped.
    """
    rows = []
    header = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        # Detect header: first line whose first token is "NAME"
        if header is None:
            if line.split()[0].upper() == "NAME":
                header = line
            continue
        # Split on whitespace; vm list -v has fixed columns
        parts = line.split()
        if len(parts) < 2:
            continue
        # Column order: NAME DATASTORE LOADER CPU MEMORY VNC AUTO %CPU RSZ UPTIME STATE
        name     = parts[0]
        ds       = parts[1] if len(parts) > 1 else ""
        loader   = parts[2] if len(parts) > 2 else ""
        cpu      = _int(parts[3]) if len(parts) > 3 else None
        memory   = parts[4]  if len(parts) > 4 else ""
        vnc      = parts[5]  if len(parts) > 5 else ""
        auto     = parts[6]  if len(parts) > 6 else ""
        pcpu     = parts[7]  if len(parts) > 7 else ""
        rsz      = parts[8]  if len(parts) > 8 else ""
        uptime   = parts[9]  if len(parts) > 9 else ""
        state    = parts[10] if len(parts) > 10 else ""
        running  = state.lower() in ("running", "bootloader")
        rows.append({
            "name": name, "datastore": ds, "loader": loader,
            "cpu": cpu, "memory": memory, "vnc": vnc, "auto": auto,
            "pcpu": pcpu, "rsz": rsz, "uptime": uptime,
            "state": state, "running": running,
        })
    return rows


def list_vms():
    """Return (vms, error). ``vms`` is a list of vm dicts with runtime stats."""
    out, rc = run(["info"])
    vms = parse_vm_info(out)
    for v in vms:
        if v["pid"]:
            up, secs, pcpu, rss = _ps_stats(v["pid"])
            v["uptime"] = up
            v["uptime_seconds"] = secs
            v["pcpu"] = pcpu
            v["rss_human"] = rss
    if rc != 0:
        return vms, out.strip()
    return vms, None


def vm_detail(name, vms=None):
    if vms is None:
        vms, _ = list_vms()
    for v in vms:
        if v["name"] == name:
            return v
    return None


# ── Serial console via gotty ────────────────────────────────────────────────

GOTTY_BIN          = "/usr/local/bin/gotty"
CU_BIN             = "/usr/bin/cu"
_GOTTY_PORT_START  = 19100
_GOTTY_PORT_END    = 19199


def _free_gotty_port():
    """Return the lowest free TCP port in the gotty range."""
    for port in range(_GOTTY_PORT_START, _GOTTY_PORT_END + 1):
        try:
            s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            s.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", port))
            s.close()
            return port
        except OSError:
            continue
    return None


class ConsoleRegistry:
    """Thread-safe registry of active gotty console processes.

    Each entry maps ``vm_name`` to::

        {"port": int, "pid": int, "nmdm": str, "proc": Popen}

    The registry is a module-level singleton accessed via
    :data:`console_registry`.
    """

    def __init__(self):
        self._lock  = threading.Lock()
        self._conns = {}   # {vm_name: {"port":, "pid":, "nmdm":, "proc":}}

    # ── public API ────────────────────────────────────────────────────────

    def start(self, name, nmdm, baud=9600, bind_addr="0.0.0.0"):
        """Start a gotty process for *name* → *nmdm*.

        If a session already exists for *name* and the process is still
        alive, returns the existing entry unchanged.
        Returns ``(entry, error_str)``.  *entry* is None on failure.
        """
        with self._lock:
            existing = self._conns.get(name)
            if existing and existing["proc"].poll() is None:
                return existing, None  # already running

            port = _free_gotty_port()
            if port is None:
                return None, "no free port in range {0}–{1}".format(
                    _GOTTY_PORT_START, _GOTTY_PORT_END)

            # --ws-origin allows the gotty WebSocket handshake from any origin
            # (html-vm may be on a different port). --once makes gotty exit
            # when the browser disconnects.  --reconnect lets the iframe
            # reconnect without restarting.
            cmd = [
                GOTTY_BIN,
                "-a", bind_addr,
                "-p", str(port),
                "-w",
                "--reconnect",
                "--reconnect-time", "3",
                "--ws-origin", ".*",
                "--title-format", "console: {0}".format(name),
                "--quiet",
                CU_BIN, "-l", nmdm, "-s", str(baud),
            ]
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError as e:
                return None, "failed to start gotty: {0}".format(e)

            entry = {"port": port, "pid": proc.pid, "nmdm": nmdm, "proc": proc}
            self._conns[name] = entry
            return entry, None

    def stop(self, name):
        """Kill the gotty process for *name*.  Returns (ok, msg)."""
        with self._lock:
            entry = self._conns.pop(name, None)
        if entry is None:
            return False, "no console session for '{0}'".format(name)
        self._kill_proc(entry["proc"])
        return True, "console for '{0}' stopped".format(name)

    def kill_stuck(self, name):
        """SIGKILL the gotty process for *name* and remove the entry.

        Use when ``stop`` doesn't work because the process is hung on the
        nmdm device.  Returns (ok, msg).
        """
        with self._lock:
            entry = self._conns.pop(name, None)
        if entry is None:
            return False, "no console session for '{0}'".format(name)
        proc = entry["proc"]
        try:
            proc.kill()
            proc.wait(timeout=3)
        except Exception:
            pass
        return True, "killed pid {0} for '{1}'".format(entry["pid"], name)

    def get(self, name):
        """Return the entry dict for *name*, or None if not running."""
        with self._lock:
            entry = self._conns.get(name)
            if entry and entry["proc"].poll() is not None:
                # process exited — clean up stale entry
                del self._conns[name]
                return None
            return dict(entry) if entry else None

    def list_all(self):
        """Return a list of status dicts for all tracked VMs.

        Each dict: ``{"name": str, "port": int, "pid": int, "nmdm": str,
        "alive": bool}``.  Stale (exited) entries are pruned.
        """
        result = []
        with self._lock:
            stale = [n for n, e in self._conns.items() if e["proc"].poll() is not None]
            for n in stale:
                del self._conns[n]
            for name, entry in self._conns.items():
                result.append({
                    "name":  name,
                    "port":  entry["port"],
                    "pid":   entry["pid"],
                    "nmdm":  entry["nmdm"],
                    "alive": True,
                })
        return result

    # ── private ───────────────────────────────────────────────────────────

    @staticmethod
    def _kill_proc(proc):
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=2)
            except Exception:
                pass


#: Module-level singleton — import this from app.py
console_registry = ConsoleRegistry()


# ── Validators ──────────────────────────────────────────────────────────────

def valid_name(name):
    return bool(VM_NAME_RE.match(name or ""))


def valid_size(s):
    return bool(re.match(r"^\d+[KMGTP]?$", s or "", re.IGNORECASE))


def valid_memory(s):
    return bool(re.match(r"^\d+[KMGTP]?$", s or "", re.IGNORECASE))


def valid_int(s):
    return bool(re.match(r"^\d+$", s or ""))


def valid_loader(s):
    return s in LOADERS


def valid_disk_type(s):
    return s in DISK_TYPES


def valid_nic_type(s):
    return s in NIC_TYPES


# ── VM conf: disk and network helpers ───────────────────────────────────────

def _load_conf_lines(name):
    """Return (lines, path, error).  *lines* is split on '\\n'."""
    vms, _ = list_vms()
    path = conf_path(name, vms)
    if not path or not os.path.isfile(path):
        return None, None, "could not locate config for {0}".format(name)
    with open(path) as f:
        return f.read().split("\n"), path, None


def vm_conf_disks(name):
    """Return a list of disk dicts read directly from the conf file.

    Each dict: ``{"index": int, "type": str, "name": str, "dev": str}``.
    """
    lines, _, err = _load_conf_lines(name)
    if err:
        return [], err
    disks = []
    idx = 0
    while True:
        dname = _conf_value(lines, "disk{0}_name".format(idx))
        if dname is None:
            break
        disks.append({
            "index": idx,
            "type":  _conf_value(lines, "disk{0}_type".format(idx)) or "",
            "name":  dname,
            "dev":   _conf_value(lines, "disk{0}_dev".format(idx)) or "file",
        })
        idx += 1
    return disks, None


def vm_conf_networks(name):
    """Return a list of network dicts read directly from the conf file.

    Each dict: ``{"index": int, "type": str, "switch": str, "mac": str}``.
    """
    lines, _, err = _load_conf_lines(name)
    if err:
        return [], err
    nets = []
    idx = 0
    while True:
        ntype = _conf_value(lines, "network{0}_type".format(idx))
        if ntype is None:
            break
        nets.append({
            "index":  idx,
            "type":   ntype,
            "switch": _conf_value(lines, "network{0}_switch".format(idx)) or "",
            "mac":    _conf_value(lines, "network{0}_mac".format(idx)) or "",
        })
        idx += 1
    return nets, None


def add_disk_conf(name, disk_type, size, dev="file"):
    """Add a new disk to the VM conf and create the backing file/zvol.

    Delegates to ``vm add -d disk -t <dev> -s <size> <name>`` which
    creates the file and updates the conf atomically.
    Returns (output, rc).
    """
    if not valid_name(name):
        return "error: invalid vm name", 1
    if not valid_disk_type(disk_type):
        return "error: invalid disk emulation type", 1
    if not valid_size(size):
        return "error: invalid size (e.g. 10G)", 1
    if dev not in ("file", "zvol", "sparse-zvol"):
        return "error: dev must be file, zvol, or sparse-zvol", 1
    return run(["add", "-d", "disk", "-t", dev, "-s", size, name])


def remove_disk_conf(name, index):
    """Remove disk *index* from the VM conf.

    Removes only the conf keys — does NOT delete the backing image file.
    Renumbers remaining disks so there are no gaps (e.g. removing disk1
    from [disk0, disk1, disk2] yields [disk0, disk1]).
    Returns (output, rc).
    """
    if not valid_name(name):
        return "error: invalid vm name", 1
    try:
        index = int(index)
    except (ValueError, TypeError):
        return "error: index must be an integer", 1

    lines, path, err = _load_conf_lines(name)
    if err:
        return "error: " + err, 1

    # Collect all disk entries
    all_disks = []
    idx = 0
    while True:
        dname = _conf_value(lines, "disk{0}_name".format(idx))
        if dname is None:
            break
        all_disks.append({
            "name": dname,
            "type": _conf_value(lines, "disk{0}_type".format(idx)) or "",
            "dev":  _conf_value(lines, "disk{0}_dev".format(idx)) or "file",
        })
        idx += 1

    if index < 0 or index >= len(all_disks):
        return "error: disk index {0} does not exist".format(index), 1

    # Remove all diskN_* keys, then rewrite the survivors in order
    for i in range(len(all_disks)):
        lines = _conf_del(lines, "disk{0}_name".format(i))
        lines = _conf_del(lines, "disk{0}_type".format(i))
        lines = _conf_del(lines, "disk{0}_dev".format(i))

    survivors = [d for i, d in enumerate(all_disks) if i != index]
    for new_i, d in enumerate(survivors):
        lines = _conf_set(lines, "disk{0}_name".format(new_i), d["name"])
        lines = _conf_set(lines, "disk{0}_type".format(new_i), d["type"])
        lines = _conf_set(lines, "disk{0}_dev".format(new_i), d["dev"])

    _atomic_write(path, "\n".join(lines))
    return "disk {0} removed (restart VM to apply)".format(index), 0


def update_disk_conf(name, index, disk_type=None, disk_name=None, dev=None):
    """Update emulation type and/or path for an existing disk slot.

    Useful for swapping a CD image path or changing emulation type.
    Returns (output, rc).
    """
    if not valid_name(name):
        return "error: invalid vm name", 1
    try:
        index = int(index)
    except (ValueError, TypeError):
        return "error: index must be an integer", 1
    if disk_type is not None and not valid_disk_type(disk_type):
        return "error: invalid disk emulation type", 1
    if dev is not None and dev not in ("file", "zvol", "sparse-zvol", "custom"):
        return "error: dev must be file, zvol, sparse-zvol, or custom", 1

    lines, path, err = _load_conf_lines(name)
    if err:
        return "error: " + err, 1

    if _conf_value(lines, "disk{0}_name".format(index)) is None:
        return "error: disk index {0} does not exist".format(index), 1

    if disk_type is not None:
        lines = _conf_set(lines, "disk{0}_type".format(index), disk_type)
    if disk_name is not None:
        lines = _conf_set(lines, "disk{0}_name".format(index), disk_name)
        # absolute paths require dev=custom
        if os.path.isabs(disk_name) and dev is None:
            lines = _conf_set(lines, "disk{0}_dev".format(index), "custom")
    if dev is not None:
        lines = _conf_set(lines, "disk{0}_dev".format(index), dev)

    _atomic_write(path, "\n".join(lines))
    return "disk {0} updated (restart VM to apply)".format(index), 0


def add_network_conf(name, switch, nic_type=None):
    """Add a new network interface to the VM conf.

    Delegates to ``vm add -d network -s <switch> <name>``.
    Returns (output, rc).
    """
    if not valid_name(name):
        return "error: invalid vm name", 1
    if not SWITCH_NAME_RE.match(switch or ""):
        return "error: invalid switch name", 1
    if nic_type is not None and not valid_nic_type(nic_type):
        return "error: invalid nic type", 1
    out, rc = run(["add", "-d", "network", "-s", switch, name])
    if rc != 0:
        return out, rc
    # vm add -d network does not support -t for nic type; patch the conf if needed
    if nic_type:
        lines, path, err = _load_conf_lines(name)
        if not err:
            # find the newly added network slot (highest index)
            idx = 0
            while _conf_value(lines, "network{0}_type".format(idx)) is not None:
                idx += 1
            target = idx - 1
            if target >= 0:
                lines = _conf_set(lines, "network{0}_type".format(target), nic_type)
                _atomic_write(path, "\n".join(lines))
    return out, rc


def remove_network_conf(name, index):
    """Remove network interface *index* from the VM conf.

    Renumbers remaining interfaces to fill the gap.
    Returns (output, rc).
    """
    if not valid_name(name):
        return "error: invalid vm name", 1
    try:
        index = int(index)
    except (ValueError, TypeError):
        return "error: index must be an integer", 1

    lines, path, err = _load_conf_lines(name)
    if err:
        return "error: " + err, 1

    all_nets = []
    idx = 0
    while True:
        ntype = _conf_value(lines, "network{0}_type".format(idx))
        if ntype is None:
            break
        all_nets.append({
            "type":   ntype,
            "switch": _conf_value(lines, "network{0}_switch".format(idx)) or "",
            "mac":    _conf_value(lines, "network{0}_mac".format(idx)) or "",
        })
        idx += 1

    if index < 0 or index >= len(all_nets):
        return "error: network index {0} does not exist".format(index), 1

    for i in range(len(all_nets)):
        lines = _conf_del(lines, "network{0}_type".format(i))
        lines = _conf_del(lines, "network{0}_switch".format(i))
        lines = _conf_del(lines, "network{0}_mac".format(i))

    survivors = [n for i, n in enumerate(all_nets) if i != index]
    for new_i, n in enumerate(survivors):
        lines = _conf_set(lines, "network{0}_type".format(new_i), n["type"])
        lines = _conf_set(lines, "network{0}_switch".format(new_i), n["switch"])
        if n["mac"]:
            lines = _conf_set(lines, "network{0}_mac".format(new_i), n["mac"])

    _atomic_write(path, "\n".join(lines))
    return "network {0} removed (restart VM to apply)".format(index), 0


def update_network_conf(name, index, switch=None, nic_type=None, mac=None):
    """Update switch and/or emulation type for an existing network slot.

    Returns (output, rc).
    """
    if not valid_name(name):
        return "error: invalid vm name", 1
    try:
        index = int(index)
    except (ValueError, TypeError):
        return "error: index must be an integer", 1
    if switch is not None and not SWITCH_NAME_RE.match(switch):
        return "error: invalid switch name", 1
    if nic_type is not None and not valid_nic_type(nic_type):
        return "error: invalid nic type", 1

    lines, path, err = _load_conf_lines(name)
    if err:
        return "error: " + err, 1

    if _conf_value(lines, "network{0}_type".format(index)) is None:
        return "error: network index {0} does not exist".format(index), 1

    if switch is not None:
        lines = _conf_set(lines, "network{0}_switch".format(index), switch)
    if nic_type is not None:
        lines = _conf_set(lines, "network{0}_type".format(index), nic_type)
    if mac is not None:
        if mac == "":
            lines = _conf_del(lines, "network{0}_mac".format(index))
        else:
            lines = _conf_set(lines, "network{0}_mac".format(index), mac)

    _atomic_write(path, "\n".join(lines))
    return "network {0} updated (restart VM to apply)".format(index), 0


# ── VM update / conf rewrite ────────────────────────────────────────────────

def update_vm(name, cpu=None, memory=None, loader=None, utctime=None):
    """Rewrite selected fields in the VM config (applies on restart)."""
    if not valid_name(name):
        return "error: invalid virtual machine name", 1
    vms, _ = list_vms()
    path = conf_path(name, vms)
    if not path or not os.path.isfile(path):
        return "error: could not locate config for {0}".format(name), 1
    if cpu is not None and not valid_int(cpu):
        return "error: invalid cpu count", 1
    if memory is not None and not valid_memory(memory):
        return "error: invalid memory size", 1
    if loader is not None and not valid_loader(loader):
        return "error: invalid loader", 1
    with open(path) as f:
        lines = f.read().split("\n")
    if cpu is not None:
        lines = _conf_set(lines, "cpu", cpu)
    if memory is not None:
        lines = _conf_set(lines, "memory", memory)
    if loader is not None:
        lines = _conf_set(lines, "loader", loader)
    if utctime is not None:
        lines = _conf_set(lines, "utctime", utctime)
    _atomic_write(path, "\n".join(lines))
    return "configuration updated (restart the VM to apply changes)", 0


# ── VM lifecycle ────────────────────────────────────────────────────────────

def start_vm(name):
    if not valid_name(name):
        return "error: invalid name", 1
    return run(["start", name])


def stop_vm(name):
    if not valid_name(name):
        return "error: invalid name", 1
    return run(["stop", name])


def reboot_vm(name):
    if not valid_name(name):
        return "error: invalid name", 1
    return run(["restart", name])


def remove_lock_file(name):
    """Remove the run.lock file for VM *name*.

    vm-bhyve writes VM_DATASTORE/<name>/run.lock while a VM is running.
    If bhyve crashes or is killed externally the lock file is left behind,
    preventing ``vm destroy``.  Returns (message, rc).
    """
    if not valid_name(name):
        return "error: invalid name", 1
    lock = os.path.join(VM_DATASTORE, name, "run.lock")
    if not os.path.exists(lock):
        return "no lock file found at {0}".format(lock), 1
    try:
        os.remove(lock)
        return "removed {0}".format(lock), 0
    except OSError as e:
        return str(e), 1


def delete_vm(name):
    if not valid_name(name):
        return "error: invalid name", 1
    stop_out, stop_rc = run(["stop", name], timeout=30)
    out, rc = run(["destroy", "-f", name])
    # Combine output so the caller can inspect warnings from both steps.
    combined = "\n".join(filter(None, [stop_out.strip(), out.strip()]))
    # Clean up the ZFS dataset if one exists (vm destroy only removes files,
    # not the dataset itself).
    destroy_vm_zfs_dataset(name)
    return combined, rc


# ── VM creation ─────────────────────────────────────────────────────────────

def create_vm(name, cpu, memory, size, switch=None, loader="bhyveload",
              disk_type="virtio-blk", nic_type="virtio-net",
              iso=None, utctime="yes", zfs_dataset=False):
    """Create a VM and post-configure its conf file with extended options.

    If *zfs_dataset* is True, a ZFS dataset is created for the VM at
    VM_DATASTORE/<name> before calling ``vm create``, enabling snapshots,
    clones, and migration.
    """
    if not valid_name(name):
        return "error: invalid virtual machine name", 1
    if not valid_int(cpu) or not valid_memory(memory) or not valid_size(size):
        return "error: invalid cpu/memory/disk-size values", 1
    if switch and not SWITCH_NAME_RE.match(switch):
        return "error: invalid network switch name", 1
    if not valid_loader(loader):
        return "error: invalid loader", 1
    if not valid_disk_type(disk_type):
        return "error: invalid disk type", 1
    if not valid_nic_type(nic_type):
        return "error: invalid nic type", 1

    if zfs_dataset:
        _, err = create_vm_zfs_dataset(name)
        if err:
            return "error creating ZFS dataset: " + err, 1

    out, rc = run(["create", "-s", size, "-c", cpu, "-m", memory, name])
    if rc != 0:
        return out, rc

    # Post-configure the conf file
    path = conf_path(name)
    if path and os.path.isfile(path):
        with open(path) as f:
            lines = f.read().split("\n")

        lines = _conf_set(lines, "loader", loader)
        lines = _conf_set(lines, "disk0_type", disk_type)
        lines = _conf_set(lines, "network0_type", nic_type)
        if switch:
            lines = _conf_set(lines, "network0_switch", switch)
        lines = _conf_set(lines, "utctime", utctime)
        # ISO / install disk — disk1_dev must be "custom" for absolute paths
        if iso:
            iso_path = os.path.join(ISO_DIR, iso)
            if os.path.isfile(iso_path):
                lines = _conf_set(lines, "disk1_type", "ahci-cd")
                lines = _conf_set(lines, "disk1_name", iso_path)
                lines = _conf_set(lines, "disk1_dev", "custom")

        _atomic_write(path, "\n".join(lines))

    return out, rc


# ── Switch management ───────────────────────────────────────────────────────

def parse_switches(text):
    """Parse ``vm switch list`` into a list of dicts."""
    sw = []
    header_seen = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if not header_seen:
            header_seen = True
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        sw.append({
            "name": parts[0],
            "type": parts[1] if len(parts) > 1 else "",
            "iface": parts[2] if len(parts) > 2 else "",
            "address": parts[3] if len(parts) > 3 else "",
            "private": parts[4] if len(parts) > 4 else "",
            "mtu": parts[5] if len(parts) > 5 else "",
            "vlan": parts[6] if len(parts) > 6 else "",
            "ports": parts[7:] if len(parts) > 7 else [],
        })
    return sw


def list_switches():
    out, rc = run(["switch", "list"])
    if rc != 0:
        return [], out.strip()
    return parse_switches(out), None


def create_switch(name, private=False):
    if not SWITCH_NAME_RE.match(name):
        return "error: invalid switch name", 1
    args = ["switch", "create"]
    if private:
        args.append("-p")
    args.append(name)
    return run(args)


def delete_switch(name):
    if not SWITCH_NAME_RE.match(name):
        return "error: invalid switch name", 1
    return run(["switch", "destroy", name])


def create_vxlan_switch(name, vni, iface):
    """Create a vm-bhyve vxlan switch (type=vxlan).

    vm-bhyve creates the vxlanN interface and a bridge, and manages lifecycle.
    ``vni`` is the 24-bit VXLAN Network Identifier (1–16777215).
    ``iface`` is the physical uplink (e.g. ``em0``).
    Returns (output, rc).
    """
    if not SWITCH_NAME_RE.match(name):
        return "error: invalid switch name", 1
    try:
        vni_int = int(vni)
    except (ValueError, TypeError):
        return "error: vni must be an integer", 1
    if not (1 <= vni_int <= 16777215):
        return "error: vni must be between 1 and 16777215", 1
    if not _valid_iface(iface or ""):
        return "error: invalid interface name", 1
    return run(["switch", "create", "-t", "vxlan", "-i", iface, "-n", str(vni_int), name])


# FreeBSD interface names: letters + digits + hyphens + underscores, start with letter
IFACE_RE = re.compile(r'^[a-zA-Z][a-zA-Z0-9_-]*$')
IP_RE    = re.compile(r'^(\d{1,3}\.){3}\d{1,3}$')
CIDR_RE  = re.compile(r'^(\d{1,3}\.){3}\d{1,3}/(\d{1,2})$')


def _valid_iface(s):
    return bool(IFACE_RE.match(s or ""))


def _valid_ip(s):
    return bool(IP_RE.match(s or ""))


def _valid_cidr(s):
    m = CIDR_RE.match(s or "")
    if not m:
        return False
    return int(m.group(2)) <= 32


def kld_load(module):
    """Load a kernel module; ignore 'already loaded' errors.

    Returns (output, rc) where rc=0 means loaded (or was already loaded).
    """
    out, rc = _exec(["kldload", module], timeout=10)
    if rc != 0 and "already loaded" in out.lower():
        return out, 0
    return out, rc


def list_tunnel_interfaces():
    """Return a dict with keys 'vxlan', 'gre', 'ipsec'.

    Each value is a list of dicts::

        {"name": "gre0", "tunnel_src": "1.2.3.4", "tunnel_dst": "5.6.7.8",
         "inet": "10.0.0.1/30", "status": "up"}

    Parsed from ``ifconfig -a`` output.
    """
    out, rc = _exec(["ifconfig", "-a"], timeout=10)
    result = {"vxlan": [], "gre": [], "ipsec": []}
    if rc != 0:
        return result

    current = None
    iface_hdr = re.compile(r'^([a-zA-Z][a-zA-Z0-9_]*\d*):\s+flags=')
    tunnel_re = re.compile(r'tunnel inet (\S+) --> (\S+)')
    inet_re   = re.compile(r'inet (\S+)')
    vxlanid_re = re.compile(r'vxlanid (\d+)')
    vxlanlocal_re = re.compile(r'vxlanlocal (\S+)')
    vxlanremote_re = re.compile(r'vxlanremote (\S+)')

    for line in out.splitlines():
        m = iface_hdr.match(line)
        if m:
            if current is not None:
                _tunnel_append(result, current)
            name = m.group(1)
            if re.match(r'^(vxlan|gre|ipsec)\d+$', name):
                up = "UP" in line.upper().split("flags=")[-1].split("<")[-1].split(">")[0]
                current = {"name": name, "tunnel_src": None, "tunnel_dst": None,
                           "inet": None, "status": "up" if up else "down",
                           "_type": name.rstrip("0123456789")}
                if "vxlan" in name:
                    current["vxlanid"] = None
                    current["vxlanlocal"] = None
                    current["vxlanremote"] = None
                    current["mode"] = None  # "unicast" or "multicast", set below
            else:
                current = None
            continue

        if current is None:
            continue

        m = tunnel_re.search(line)
        if m:
            current["tunnel_src"] = m.group(1)
            current["tunnel_dst"]  = m.group(2)

        m = inet_re.search(line)
        if m and not line.strip().startswith("inet6"):
            current["inet"] = m.group(1)

        if "_type" in current and current["_type"] == "vxlan":
            m = vxlanid_re.search(line)
            if m:
                current["vxlanid"] = m.group(1)
            m = vxlanlocal_re.search(line)
            if m:
                current["vxlanlocal"] = m.group(1)
            m = vxlanremote_re.search(line)
            if m:
                current["vxlanremote"] = m.group(1)
                # multicast addresses are 224.0.0.0 – 239.255.255.255
                first_octet = int(m.group(1).split(".")[0])
                current["mode"] = "multicast" if first_octet >= 224 else "unicast"

    if current is not None:
        _tunnel_append(result, current)

    return result


def _tunnel_append(result, iface):
    t = iface.pop("_type", None)
    if t in result:
        result[t].append(iface)


def create_vxlan_tunnel(name, vni, local_ip, remote_ip, port=4789):
    """Create a standalone unicast VXLAN tunnel interface.

    This is NOT managed by vm-bhyve — use it for point-to-point tunnels to
    remote hosts (firewalls, routers, other hypervisors) that share no
    multicast group.

    ``name``      — interface name, must start with ``vxlan`` (e.g. ``vxlan10``)
    ``vni``       — VXLAN Network Identifier (1–16777215)
    ``local_ip``  — local outer IP address (assigned to a local interface)
    ``remote_ip`` — remote outer IP address
    ``port``      — UDP port (default 4789)

    After creation, optionally:
    - Assign an inner IP:  ``ifconfig vxlanN inet <addr>/<prefix>``
    - Add to a bridge:     ``ifconfig <bridge> addm vxlanN``

    Returns (output, rc).
    """
    if not _valid_iface(name) or not name.startswith("vxlan"):
        return "error: name must be a vxlan interface (e.g. vxlan10)", 1
    try:
        vni_int = int(vni)
    except (ValueError, TypeError):
        return "error: vni must be an integer", 1
    if not (1 <= vni_int <= 16777215):
        return "error: vni must be between 1 and 16777215", 1
    if not _valid_ip(local_ip):
        return "error: invalid local IP", 1
    if not _valid_ip(remote_ip):
        return "error: invalid remote IP", 1
    try:
        port_int = int(port)
    except (ValueError, TypeError):
        return "error: port must be an integer", 1
    if not (1 <= port_int <= 65535):
        return "error: port must be 1–65535", 1

    out, rc = kld_load("if_vxlan")
    if rc != 0:
        return "error: could not load if_vxlan: " + out, rc

    # Create with unicast remote — no vxlandev/vxlangroup needed
    create_args = [
        "ifconfig", name, "create",
        "vxlanid",     str(vni_int),
        "vxlanlocal",  local_ip,
        "vxlanremote", remote_ip,
        "vxlanport",   str(port_int),
        "vxlanlearn",
        "up",
    ]
    out, rc = _exec(create_args, timeout=5)
    if rc != 0:
        return "error: ifconfig {0} create: {1}".format(name, out), rc

    return "VXLAN unicast tunnel {0} (VNI {1}) created".format(name, vni_int), 0


def destroy_vxlan_tunnel(name):
    """Destroy a standalone VXLAN tunnel interface.

    Does NOT touch vm-bhyve-managed VXLAN switches — use ``vm switch destroy``
    for those.  Returns (output, rc).
    """
    if not _valid_iface(name) or not name.startswith("vxlan"):
        return "error: invalid vxlan interface name", 1
    out, rc = _exec(["ifconfig", name, "destroy"], timeout=5)
    if rc != 0:
        return "error destroying {0}: {1}".format(name, out), rc
    return "destroyed", 0


def create_gre_tunnel(name, local_ip, remote_ip, inner_local, inner_remote):
    """Create a GRE tunnel interface.

    ``name``         — interface name, e.g. ``gre0``
    ``local_ip``     — outer source IP (must be assigned to a local interface)
    ``remote_ip``    — outer destination IP
    ``inner_local``  — inner IP with prefix, e.g. ``10.99.0.1/30``
    ``inner_remote`` — inner peer IP, e.g. ``10.99.0.2``

    Returns (output, rc).
    """
    if not _valid_iface(name) or not name.startswith("gre"):
        return "error: name must be a gre interface (e.g. gre0)", 1
    if not _valid_ip(local_ip):
        return "error: invalid local IP", 1
    if not _valid_ip(remote_ip):
        return "error: invalid remote IP", 1
    if not _valid_cidr(inner_local):
        return "error: inner_local must be CIDR (e.g. 10.0.0.1/30)", 1
    if not _valid_ip(inner_remote):
        return "error: invalid inner_remote IP", 1

    out, rc = kld_load("if_gre")
    if rc != 0:
        return "error: could not load if_gre: " + out, rc

    out, rc = _exec(["ifconfig", name, "create"], timeout=5)
    if rc != 0:
        return "error: ifconfig {0} create: {1}".format(name, out), rc

    out, rc = _exec(["ifconfig", name, "tunnel", local_ip, remote_ip], timeout=5)
    if rc != 0:
        _exec(["ifconfig", name, "destroy"], timeout=5)
        return "error: set tunnel endpoints: " + out, rc

    out, rc = _exec(["ifconfig", name, "inet", inner_local, inner_remote, "up"], timeout=5)
    if rc != 0:
        _exec(["ifconfig", name, "destroy"], timeout=5)
        return "error: set inner addresses: " + out, rc

    return "GRE tunnel {0} created".format(name), 0


def destroy_gre_tunnel(name):
    """Destroy a GRE tunnel interface.  Returns (output, rc)."""
    if not _valid_iface(name) or not name.startswith("gre"):
        return "error: invalid gre interface name", 1
    out, rc = _exec(["ifconfig", name, "destroy"], timeout=5)
    if rc != 0:
        return "error destroying {0}: {1}".format(name, out), rc
    return "destroyed", 0


def create_ipsec_tunnel(name, local_ip, remote_ip, inner_local, inner_remote, reqid=None):
    """Create an IPsec virtual tunnel interface (route-based VPN).

    Does NOT install Security Associations — caller must run setkey(8)
    or use an IKE daemon.  Returns (output, rc).
    """
    if not _valid_iface(name) or not name.startswith("ipsec"):
        return "error: name must be an ipsec interface (e.g. ipsec0)", 1
    if not _valid_ip(local_ip):
        return "error: invalid local IP", 1
    if not _valid_ip(remote_ip):
        return "error: invalid remote IP", 1
    if not _valid_cidr(inner_local):
        return "error: inner_local must be CIDR (e.g. 172.16.0.1/30)", 1
    if not _valid_ip(inner_remote):
        return "error: invalid inner_remote IP", 1

    out, rc = kld_load("ipsec")
    if rc != 0:
        return "error: could not load ipsec: " + out, rc

    create_args = ["ifconfig", name, "create"]
    if reqid:
        try:
            reqid_int = int(reqid)
        except (ValueError, TypeError):
            return "error: reqid must be an integer", 1
        create_args += ["reqid", str(reqid_int)]

    out, rc = _exec(create_args, timeout=5)
    if rc != 0:
        return "error: ifconfig {0} create: {1}".format(name, out), rc

    out, rc = _exec(["ifconfig", name, "inet", "tunnel", local_ip, remote_ip], timeout=5)
    if rc != 0:
        _exec(["ifconfig", name, "destroy"], timeout=5)
        return "error: set tunnel endpoints: " + out, rc

    out, rc = _exec(["ifconfig", name, "inet", inner_local, inner_remote, "up"], timeout=5)
    if rc != 0:
        _exec(["ifconfig", name, "destroy"], timeout=5)
        return "error: set inner addresses: " + out, rc

    return "IPsec tunnel {0} created (configure SAs with setkey)".format(name), 0


def destroy_ipsec_tunnel(name):
    """Destroy an IPsec tunnel interface.  Returns (output, rc)."""
    if not _valid_iface(name) or not name.startswith("ipsec"):
        return "error: invalid ipsec interface name", 1
    out, rc = _exec(["ifconfig", name, "destroy"], timeout=5)
    if rc != 0:
        return "error destroying {0}: {1}".format(name, out), rc
    return "destroyed", 0


def bridge_addm(bridge, iface):
    """Add interface ``iface`` as a member of bridge ``bridge``.

    Returns (output, rc).
    """
    if not _valid_iface(bridge):
        return "error: invalid bridge name", 1
    if not _valid_iface(iface):
        return "error: invalid interface name", 1
    out, rc = _exec(["ifconfig", bridge, "addm", iface], timeout=5)
    if rc != 0:
        return "error adding {0} to {1}: {2}".format(iface, bridge, out), rc
    return "added {0} to {1}".format(iface, bridge), 0


def bridge_deletem(bridge, iface):
    """Remove interface ``iface`` from bridge ``bridge``.

    Returns (output, rc).
    """
    if not _valid_iface(bridge):
        return "error: invalid bridge name", 1
    if not _valid_iface(iface):
        return "error: invalid interface name", 1
    out, rc = _exec(["ifconfig", bridge, "deletem", iface], timeout=5)
    if rc != 0:
        return "error removing {0} from {1}: {2}".format(iface, bridge, out), rc
    return "removed {0} from {1}".format(iface, bridge), 0


def list_host_interfaces():
    """Return list of physical/uplink interface names (excludes lo, tap, bridge, tun, vxlan, gre, ipsec)."""
    out, rc = _exec(["ifconfig", "-l"], timeout=5)
    if rc != 0:
        return []
    skip = re.compile(r'^(lo|tap|bridge|tun|vxlan|gre|ipsec|vm-|vbox|pflog|enc)\d*')
    return [i for i in out.split() if not skip.match(i)]



# ── ISO management ──────────────────────────────────────────────────────────

def list_isos():
    """Return sorted list of ISO filenames found in the datastore .iso dir."""
    try:
        files = sorted(
            f for f in os.listdir(ISO_DIR)
            if f.lower().endswith(".iso") or f.lower().endswith(".img")
        )
        return files, None
    except OSError as e:
        return [], str(e)


def delete_iso(filename):
    """Delete an ISO file. Returns (message, rc)."""
    if not re.match(r'^[\w\-. ]+\.(iso|img)$', filename, re.IGNORECASE):
        return "error: invalid filename", 1
    path = os.path.join(ISO_DIR, filename)
    if not os.path.isfile(path):
        return "error: file not found", 1
    try:
        os.remove(path)
        return "deleted", 0
    except OSError as e:
        return str(e), 1


# ── Host resource information ───────────────────────────────────────────────

def host_info():
    """Return a dict of host hardware and storage capacity."""
    info = {
        "cpu_count": None,
        "mem_total_bytes": None,
        "mem_total_human": None,
        "datastore_path": VM_DATASTORE,
        "datastore_total_bytes": None,
        "datastore_avail_bytes": None,
        "datastore_used_bytes": None,
        "datastore_total_human": None,
        "datastore_avail_human": None,
        "datastore_used_human": None,
    }

    # CPU count
    out, rc = _exec(["sysctl", "-n", "hw.ncpu"], timeout=5)
    if rc == 0:
        info["cpu_count"] = _int(out.strip())

    # Physical RAM
    out, rc = _exec(["sysctl", "-n", "hw.physmem"], timeout=5)
    if rc == 0:
        b = _int(out.strip())
        info["mem_total_bytes"] = b
        info["mem_total_human"] = fmt_bytes(b)

    # Datastore disk usage via statfs
    try:
        st = os.statvfs(VM_DATASTORE)
        total = st.f_blocks * st.f_frsize
        avail = st.f_bavail * st.f_frsize
        used = (st.f_blocks - st.f_bfree) * st.f_frsize
        info["datastore_total_bytes"] = total
        info["datastore_avail_bytes"] = avail
        info["datastore_used_bytes"] = used
        info["datastore_total_human"] = fmt_bytes(total)
        info["datastore_avail_human"] = fmt_bytes(avail)
        info["datastore_used_human"] = fmt_bytes(used)
    except OSError:
        pass

    return info


def vm_aggregate_stats(vms):
    """Return totals across all VMs: disk used, cpu allocated, net in/out."""
    total_disk = 0
    total_cpu = 0
    total_mem = 0
    total_net_in = 0
    total_net_out = 0
    for v in vms:
        total_cpu += (v.get("cpu") or 0)
        total_mem += (v.get("memory_bytes") or 0)
        for d in v.get("disks", []):
            total_disk += (d.get("used_bytes") or 0)
        for n in v.get("networks", []):
            total_net_in += (n.get("net_in_bytes") or 0)
            total_net_out += (n.get("net_out_bytes") or 0)
    return {
        "disk_used_bytes": total_disk,
        "disk_used_human": fmt_bytes(total_disk),
        "cpu_allocated": total_cpu,
        "mem_allocated_bytes": total_mem,
        "mem_allocated_human": fmt_bytes(total_mem),
        "net_in_bytes": total_net_in,
        "net_in_human": fmt_bytes(total_net_in),
        "net_out_bytes": total_net_out,
        "net_out_human": fmt_bytes(total_net_out),
    }

# ── Force stop / reset via bhyvectl ────────────────────────────────────────

BHYVECTL_BIN = "/usr/sbin/bhyvectl"


def force_poweroff_vm(name):
    """Hard power-off via bhyvectl --force-poweroff (no guest notification)."""
    if not valid_name(name):
        return "error: invalid name", 1
    return _exec([BHYVECTL_BIN, "--vm={0}".format(name), "--force-poweroff"])


def force_reset_vm(name):
    """Hard reset via bhyvectl --force-reset."""
    if not valid_name(name):
        return "error: invalid name", 1
    return _exec([BHYVECTL_BIN, "--vm={0}".format(name), "--force-reset"])


def destroy_vmm(name):
    """Destroy the VMM context (bhyvectl --destroy) for a stuck/orphaned VM."""
    if not valid_name(name):
        return "error: invalid name", 1
    return _exec([BHYVECTL_BIN, "--vm={0}".format(name), "--destroy"])


# ── ZFS dataset management ─────────────────────────────────────────────────

def _zfs_bin():
    return "/sbin/zfs"


def vm_dir_uses_zfs():
    """Return True if vm-bhyve is configured with a ZFS datastore.

    Reads /etc/rc.conf looking for vm_dir="zfs:...".  When True, vm-bhyve
    creates a per-VM ZFS dataset automatically on ``vm create`` — no manual
    dataset creation is needed or wanted.
    """
    try:
        with open("/etc/rc.conf") as f:
            for line in f:
                line = line.strip()
                if line.startswith("vm_dir="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    return val.startswith("zfs:")
    except OSError:
        pass
    return False


def _zfs_parent_for_vmdir():
    """Return the ZFS dataset that should be the parent for per-VM datasets.

    Enumerates all ZFS datasets and finds the one whose mountpoint is the
    deepest (longest) match for VM_DATASTORE.  This correctly handles the
    case where /var is pool0/var but ``zfs list /var/vm`` resolves to the
    root BE (because the root BE is mounted at / and covers all paths not
    explicitly mounted by a child dataset).

    Returns (parent_dataset, vm_container_dataset) or (None, None).

    Example: VM_DATASTORE=/var/vm, pool0/var mounted at /var
      -> parent_dataset      = "pool0/var"
      -> vm_container_dataset = "pool0/var/vm"
    """
    out, rc = _exec([_zfs_bin(), "list", "-H", "-o", "name,mountpoint"],
                    timeout=15)
    if rc != 0:
        return None, None

    target = VM_DATASTORE.rstrip("/")
    best_ds = None
    best_mount = ""

    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        ds_name, ds_mount = parts[0].strip(), parts[1].strip()
        if ds_mount in ("none", "-", "legacy"):
            continue
        ds_mount = ds_mount.rstrip("/")
        # Dataset mountpoint must be a prefix of VM_DATASTORE
        if target == ds_mount or target.startswith(ds_mount + "/"):
            # Pick the deepest (longest mountpoint) match
            if len(ds_mount) > len(best_mount):
                best_ds = ds_name
                best_mount = ds_mount

    if not best_ds:
        return None, None

    if best_mount == target:
        # VM_DATASTORE itself is already a dedicated ZFS dataset
        return best_ds, best_ds

    # Derive the container dataset path by appending the suffix
    suffix = os.path.relpath(target, best_mount).replace(os.sep, "/")
    vm_container = best_ds.rstrip("/") + "/" + suffix
    return best_ds, vm_container


def zfs_vm_dataset_exists(name):
    """Return True if a dedicated ZFS dataset for VM *name* already exists."""
    return _vm_zfs_dataset(name) is not None


def create_vm_zfs_dataset(name):
    """Create a ZFS dataset for VM *name* at VM_DATASTORE/<name>.

    Resolves the ZFS pool that owns VM_DATASTORE, then creates a per-VM
    dataset at <pool>/vm/<name> (or <container>/<name> when VM_DATASTORE
    already has a dedicated dataset) with mountpoint=VM_DATASTORE/<name>.

    If VM_DATASTORE itself is just a plain directory inside a larger dataset
    (the common case when vm_dir="/var/vm" and no dedicated vm dataset exists),
    this first creates the container dataset with the correct mountpoint, then
    the per-VM dataset underneath it.

    Returns (dataset_name, error_string).  error_string is None on success.
    """
    if not valid_name(name):
        return None, "invalid vm name"

    parent_ds, container_ds = _zfs_parent_for_vmdir()
    if not container_ds:
        return None, "could not determine ZFS pool for {0}".format(VM_DATASTORE)

    vm_ds = container_ds.rstrip("/") + "/" + name
    vm_path = os.path.join(VM_DATASTORE, name)

    # Create the container dataset (e.g. pool0/var/vm) if it doesn't exist yet.
    chk, rc = _exec([_zfs_bin(), "list", "-H", "-o", "name", container_ds], timeout=10)
    if rc != 0:
        # The container path may already exist as a plain directory.
        # Create the dataset and set mountpoint explicitly so ZFS takes it over.
        out, rc = _exec([_zfs_bin(), "create",
                         "-o", "mountpoint=" + VM_DATASTORE,
                         container_ds], timeout=30)
        if rc != 0:
            # If the failure is "dataset already exists" we can proceed.
            if "already exists" not in out:
                return None, "zfs create {0} failed: {1}".format(container_ds, out.strip())

    # Check the per-VM dataset doesn't already exist.
    chk, rc = _exec([_zfs_bin(), "list", "-H", "-o", "name", vm_ds], timeout=10)
    if rc == 0:
        return vm_ds, None  # already exists, fine

    # Create the per-VM dataset with an explicit mountpoint.
    # Use -p to create intermediate datasets if needed (idempotent).
    out, rc = _exec([_zfs_bin(), "create",
                     "-p",
                     "-o", "mountpoint=" + vm_path,
                     vm_ds], timeout=30)
    if rc != 0:
        return None, "zfs create {0} failed: {1}".format(vm_ds, out.strip())

    return vm_ds, None


def destroy_vm_zfs_dataset(name):
    """Recursively destroy the ZFS dataset for VM *name* if one exists.

    Called after vm destroy to clean up the dataset.  Safe to call when
    no dataset exists (returns success).
    Returns (output, rc).
    """
    if not valid_name(name):
        return "error: invalid name", 1
    dataset = _vm_zfs_dataset(name)
    if not dataset:
        return "no ZFS dataset for {0}".format(name), 0
    return _exec([_zfs_bin(), "destroy", "-r", dataset], timeout=60)


# ── ZFS snapshot / clone / rollback / image ────────────────────────────────


def _vm_zfs_dataset(name):
    """Return the ZFS dataset name for VM *name*, or None if not on ZFS.

    Runs ``zfs list -H -o name <path>`` and verifies the returned dataset
    actually corresponds to the VM directory — not a parent dataset that
    merely *contains* the directory.  When vm_dir is a plain path (not a
    dedicated ZFS dataset) the resolved dataset will be the root/boot
    dataset, which is wrong and must be rejected.
    """
    path = os.path.join(VM_DATASTORE, name)
    out, rc = _exec([_zfs_bin(), "list", "-H", "-o", "name", path], timeout=10)
    if rc != 0:
        return None
    dataset = out.strip()
    if not dataset:
        return None
    # The dataset must end with the VM name as a path component, not just
    # be any ancestor dataset.  e.g. "pool/vm/potato" is fine but
    # "pool/ROOT/15.1-RELEASE" is not the VM dataset.
    if not (dataset.endswith("/" + name) or dataset == name):
        return None
    return dataset


def vm_snapshots(name):
    """Return list of snapshot dicts for VM *name* using ``zfs list``.

    Returns (snapshots, error).  Each dict: {name, short, creation, used}.
    Returns ([], error) when vm_dir is not a ZFS datastore or the VM has
    no dedicated dataset.
    """
    if not valid_name(name):
        return [], "invalid vm name"
    dataset = _vm_zfs_dataset(name)
    if not dataset:
        return [], (
            "No dedicated ZFS dataset found for VM '{0}'. "
            "ZFS snapshots require vm_dir to be configured as a ZFS "
            "datastore (e.g. vm_dir=\"zfs:pool/vm\" in /etc/rc.conf).".format(name)
        )
    out, rc = _exec([_zfs_bin(), "list", "-r", "-t", "snapshot",
                     "-o", "name,used,creation", "-H",
                     "-s", "creation",
                     dataset],
                    timeout=15)
    if rc != 0:
        if "dataset does not exist" in out or "no such file" in out.lower():
            return [], "no ZFS snapshots for {0}".format(name)
        return [], out.strip()
    snaps = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        full_name = parts[0].strip()
        at = full_name.find("@")
        # Only include snapshots that belong to this VM's dataset
        if not full_name.startswith(dataset + "@") and not full_name.startswith(dataset + "/"):
            continue
        short = full_name[at + 1:] if at != -1 else full_name
        snaps.append({
            "name":     full_name,
            "short":    short,
            "used":     parts[1].strip(),
            "creation": parts[2].strip(),
        })
    return snaps, None


def vm_snapshot(name, snap_name=None):
    """Take a snapshot of VM *name*. Returns (output, rc)."""
    if not valid_name(name):
        return "error: invalid name", 1
    target = name if not snap_name else "{0}@{1}".format(name, snap_name)
    return run(["snapshot", target])


def vm_rollback(name, snap_name, force=False):
    """Roll back VM *name* to snapshot *snap_name*. Returns (output, rc)."""
    if not valid_name(name):
        return "error: invalid name", 1
    if not snap_name or "@" in snap_name:
        # caller passed full name@snap or just snap label
        target = snap_name if "@" in (snap_name or "") else "{0}@{1}".format(name, snap_name)
    else:
        target = "{0}@{1}".format(name, snap_name)
    args = ["rollback"]
    if force:
        args.append("-r")
    args.append(target)
    return run(args)


def vm_clone(name, new_name, snap_name=None):
    """Clone VM *name* to *new_name*. Returns (output, rc)."""
    if not valid_name(name) or not valid_name(new_name):
        return "error: invalid name", 1
    src = "{0}@{1}".format(name, snap_name) if snap_name else name
    return run(["clone", src, new_name])


def vm_delete_snapshot(name, snap_name):
    """Destroy a single snapshot via zfs destroy. Returns (output, rc)."""
    if not valid_name(name):
        return "error: invalid name", 1
    full = "{0}@{1}".format(os.path.join(VM_DATASTORE, name), snap_name)
    return _exec([_zfs_bin(), "destroy", full], timeout=30)


# ── VM image pack / unpack ─────────────────────────────────────────────────

def vm_image_list():
    """Return (images, error) from ``vm image list``."""
    out, rc = run(["image", "list"])
    images = []
    header_seen = False
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        if not header_seen:
            header_seen = True
            continue
        parts = line.split(None, 4)
        if len(parts) >= 2:
            images.append({
                "uuid":        parts[0],
                "name":        parts[1] if len(parts) > 1 else "",
                "date":        parts[2] if len(parts) > 2 else "",
                "size":        parts[3] if len(parts) > 3 else "",
                "description": parts[4] if len(parts) > 4 else "",
            })
    err = out.strip() if rc != 0 else None
    return images, err


def vm_image_create(name, description=""):
    """Package VM *name* into a portable image. Returns (output, rc)."""
    if not valid_name(name):
        return "error: invalid name", 1
    args = ["image", "create"]
    if description:
        args += ["-d", description]
    args.append(name)
    return run(args, timeout=300)


def vm_image_provision(uuid, new_name):
    """Provision a new VM from image UUID. Returns (output, rc)."""
    if not valid_name(new_name):
        return "error: invalid new name", 1
    return run(["image", "provision", uuid, new_name], timeout=300)


def vm_image_destroy(uuid):
    """Delete an image by UUID. Returns (output, rc)."""
    return run(["image", "destroy", uuid])


# ── Rename / bulk lifecycle ────────────────────────────────────────────────


def vm_rename(name, new_name):
    """Rename VM *name* to *new_name* via ``vm rename``.

    VM must be stopped.  Returns (output, rc).
    """
    if not valid_name(name) or not valid_name(new_name):
        return "error: invalid name", 1
    return run(["rename", name, new_name])


def vm_stopall(force=False):
    """Stop all running VMs via ``vm stopall``.  Returns (output, rc)."""
    args = ["stopall"]
    if force:
        args.append("-f")
    return run(args, timeout=120)


def vm_startall():
    """Start all autostart VMs via ``vm startall``.  Returns (output, rc)."""
    return run(["startall"], timeout=120)


# ── ISO download ────────────────────────────────────────────────────────────


def iso_fetch(url):
    """Download an ISO/image from *url* into the datastore ISO directory.

    Runs ``vm iso <url>``.  Returns (output, rc).
    This is a potentially long-running operation; call in a background thread
    for production use.
    """
    if not url or not url.startswith(("http://", "https://", "ftp://")):
        return "error: URL must start with http://, https://, or ftp://", 1
    return run(["iso", url], timeout=3600)


# ── Migration ──────────────────────────────────────────────────────────────

def vm_migrate(name, host, remote_name=None, start_remote=False, triple=False,
               destroy_local=False):
    """Migrate VM *name* to *host* via ``vm migrate``. Returns (output, rc).

    Runs in a subprocess that may take several minutes.  The caller should
    run this in a background thread for long transfers.
    """
    if not valid_name(name):
        return "error: invalid name", 1
    args = ["migrate"]
    if start_remote:
        args.append("-s")
    if triple:
        args.append("-t")
    if destroy_local:
        args.append("-x")
    if remote_name:
        args += ["-r", remote_name]
    args += [name, host]
    return run(args, timeout=3600)

