# html-vm Project Status

## What This Is
Flask web admin UI for vm-bhyve on FreeBSD 15.1. Runs on port 8088.
Install target: `/usr/local/html-vm`. Run: `sudo .venv/bin/python run.py`

## Stack
- Python 3.12 + Flask (gevent WSGI server)
- PAM login, CSRF protection
- vm-bhyve CLI (`vm`) for all hypervisor operations
- gotty + cu for serial console (WebSocket terminal in browser)
- noVNC + websockify for VNC graphics console (in progress)

## Key Paths
| Path | Purpose |
|------|---------|
| `app.py` | All Flask routes |
| `vm.py` | All vm-bhyve CLI wrappers, registries |
| `auth.py` | PAM authentication |
| `run.py` | gevent entry point |
| `templates/` | Jinja2 templates |
| `tests/test_vm.py` | vm.py unit tests (no hypervisor needed) |
| `tests/test_app.py` | Flask route tests (all I/O mocked) |
| `tests/fixtures/` | Static fixture files for tests |
| `static/style.css` | Global stylesheet |

## External Tools / Dependencies
| Binary | Location | Purpose |
|--------|----------|---------|
| `vm` | `/usr/local/sbin/vm` | vm-bhyve CLI |
| `gotty` | `/usr/local/bin/gotty` | WebSocket serial terminal |
| `cu` | system | Serial port client |
| `websockify` | `/usr/local/bin/websockify` | TCP→WebSocket proxy for VNC |
| `noVNC` | `/usr/local/libexec/novnc/` | HTML5 VNC client (v1.6.0) |

## Python Packages
- `flask-sock==0.7.0` (in requirements.txt)
- `geventwebsocket` — installed via `pkg install py312-gevent-websocket`, symlinked into `.venv`
- Never `pip install geventwebsocket` — use pkg only

## Port Ranges
| Range | Used For |
|-------|---------|
| 8088 | html-vm Flask app |
| 19100–19199 | gotty serial console processes |
| 19200–19299 | websockify VNC proxy processes |

## Architecture — VNC Console
```
Browser (iframe → noVNC)
    ↕ WebSocket
websockify :192xx   (one process per running VM with graphics)
    ↕ raw TCP/RFB
bhyve VNC :59xx     (graphics_port in VM conf)
```

## Completed Features
- [x] PAM login / logout with CSRF
- [x] Dashboard: VM list, health, auto-refresh
- [x] VM detail page: state, CPU, memory, disks, NICs
- [x] VM lifecycle: start, stop, restart, force-kill
- [x] Disk management: add, remove
- [x] NIC management: add, remove
- [x] Create VM wizard
- [x] ISO management: upload, delete, list
- [x] Serial console via gotty (`ConsoleRegistry` singleton)
- [x] VM logs viewer
- [x] Snapshots: create, delete, rollback (live VMs with `-f`)
- [x] Clone VM
- [x] VM image export/import
- [x] VM migrate
- [x] VXLAN / GRE / IPsec tunnel management
- [x] Switch config: per-switch physical iface, VLAN, NAT, address, private mode
- [x] Networks page: clickable switches, inline bridge add
- [x] ZFS storage page
- [x] Maintenance mode + reboot
- [x] 260 tests passing, pyflakes clean

## In Progress
- [ ] **noVNC VNC graphics console**
  - `VncRegistry` class in `vm.py` (mirrors `ConsoleRegistry`)
  - VNC routes in `app.py` (`/vm/<name>/vnc`, `/vm/<name>/vnc/start`, `/vm/<name>/vnc/stop`)
  - `/novnc/<path>` static serving from `/usr/local/libexec/novnc/`
  - `templates/vnc_console.html` new template
  - VNC button in `vm_detail.html` header (when VM running)
  - `VncRegistryTests` in `tests/test_vm.py`
  - `VncRouteTests` in `tests/test_app.py`

## VM conf keys for graphics
```
graphics="yes"
graphics_port="5901"   # unique per VM — required
graphics_res="1280x720"
xhci_mouse="yes"
```

## Git Tags
- `pre-novnc` — state before VNC implementation started

## How To Run Tests
```sh
.venv/bin/python -m unittest tests/test_vm.py tests/test_app.py
```

## How To Lint
```sh
.venv/bin/pyflakes app.py vm.py auth.py
```

## How To Deploy
```sh
sudo sh install.sh
```
