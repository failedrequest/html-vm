# html-vm

A Flask web admin for [vm-bhyve](https://github.com/churchers/vm-bhyve) on FreeBSD.

Manage bhyve virtual machines from a browser — no command line required.

---

## Features

### Dashboard
- VM table with state, CPU, memory, disk, uptime, network stats
- Host resource bars — vCPUs allocated, memory allocated, datastore used
- Health section — FreeBSD version, vm-bhyve version, system uptime
- Console button (`cons`) per running VM, direct from the table
- Auto-refreshes every 10 s via `/api/vms`

### VM detail
- **Lifecycle** — start, stop, reboot, force power-off, force reset, destroy VMM context
- **Console + Logs** links in the page header (console only shown when running)
- **Lifecycle section** sits directly below the info table, above disks
- Config edit — cpu, memory, loader, utctime
- Rename VM (`vm rename`, VM must be stopped)
- Locked VM — remove stale `run.lock` file via UI button

### Disks & Networks
- Add, remove, update disks (type, path, device) per-VM
- Install media picker auto-selects `ahci-hd` for `.img` memstick files, `ahci-cd` for `.iso` files
- Add, remove, update NICs (switch, NIC type, MAC) per-VM

### VM creation
- OS profile quick-fill (FreeBSD, Linux, Windows, OpenBSD)
- Network switch selector shows only switches that actually exist — no phantom `default`
- Optional ZFS dataset creation; informational note when vm-bhyve uses ZFS datastore

### Serial console
- In-browser terminal via [gotty](https://github.com/sorenisanerd/gotty) and nmdm devices
- Console registry tracks active sessions; kill-stuck support

### ISO management
- List, upload (XHR progress bar), delete
- Download from URL via `vm iso <url>`

### Networking
- **vm-bhyve switches** — create, delete; switch names link to per-switch config page
- **Per-switch config** — add/remove physical interfaces, set VLAN (uplink tag), NAT, gateway address, private mode
- **VLAN model explained** — one switch per VLAN; uplink tagged, VM taps untagged
- **VXLAN** — vm-bhyve native overlay switches + standalone unicast tunnels
- **GRE tunnels** — create/destroy, inline "add to bridge" per row
- **IPsec tunnels** — route-based VTI, create/destroy, inline "add to bridge" per row

### Storage & migration
- ZFS snapshots, rollback, clone, portable image pack/provision
- VM migration via `vm migrate`

### Security & ops
- PAM authentication — any local OS account; 8-hour sessions
- CSRF protection on all POST routes
- Error pages — 403 (CSRF), 404, 413 (upload too large)
- FreeBSD rc.d service script included
- `install.sh` — idempotent installer (pkg + venv + rc.d)

---

## Requirements

- FreeBSD 14 or 15
- [vm-bhyve](https://github.com/churchers/vm-bhyve) installed and initialized (`vm init`)
- Python 3.12
- `gotty` at `/usr/local/bin/gotty` (for serial console — `pkg install gotty`)
- `setkey(8)` at `/sbin/setkey` (base system, for IPsec)

Python packages (installed via `install.sh` or manually):

```
flask-sock==0.7.0   # pip only
```

gevent and geventwebsocket are installed via `pkg install py312-gevent-websocket` and symlinked into `.venv` — do **not** `pip install` them.

---

## Installation

```sh
# 1. Clone
git clone https://github.com/failedrequest/html-vm.git /usr/local/html-vm
cd /usr/local/html-vm

# 2. Run the installer (idempotent — safe to re-run)
sudo sh install.sh
```

The installer:
- Installs pkg dependencies (Python 3.12, gevent-websocket, gotty, etc.)
- Creates `.venv` and pip-installs `flask-sock`
- Symlinks `geventwebsocket` into `.venv`
- Installs and enables the rc.d service

Then start the app:

```sh
sudo service html_vm start
# or directly:
sudo .venv/bin/python run.py
```

Open `http://<host>:8088/` and log in with any local OS account.

---

## rc.d service

```sh
sudo cp etc/rc.d/html_vm /usr/local/etc/rc.d/html_vm
sudo chmod 555 /usr/local/etc/rc.d/html_vm
echo 'html_vm_enable="YES"' | sudo tee -a /etc/rc.conf
sudo service html_vm start
```

---

## Running tests

No hypervisor or root access required — all external I/O is mocked.

```sh
.venv/bin/python -m unittest tests/test_vm.py tests/test_app.py -v
```

Current: **260 tests — all pass**.

---

## Project layout

```
app.py              Flask app: all routes, CSRF, login/logout
vm.py               vm CLI wrapper, parsers, console registry, tunnel helpers
auth.py             PAM authentication
run.py              gevent + geventwebsocket WSGI entry point
install.sh          Idempotent FreeBSD installer
static/style.css    Self-contained CSS — no external framework or CDN
templates/          Jinja2 templates
  dashboard.html      VM table + host resource bars + health
  vm_detail.html      VM detail, lifecycle, disks, networks, config edit
  vm_create.html      Create VM form with OS profile quick-fill
  networks.html       Switches + tunnels (VXLAN/GRE/IPsec)
  network_switch.html Per-switch config (interfaces, VLAN, NAT, address, private)
  vm_log.html         VM log viewer (SSE live stream + static tail)
  console.html        gotty iframe serial console
  isos.html           ISO management
  storage.html        ZFS snapshots, clone, images
  migrate.html        VM migration
tests/              Unit + route tests (260 total, fixtures in tests/fixtures/)
tools/              Diagnostic utilities (vnc_diag.py, ws_rfb_probe.py)
etc/rc.d/html_vm    FreeBSD rc.d service script
```

---

## Platform notes

- **FreeBSD 14 / 15** —  BSD userland only
- Valid loaders: `bhyveload` (FreeBSD), `uefi`, `uefi-csm` — `grub` is not installed
- `vm_dir` is read from `sysrc -n vm_dir` at startup; handles `zfs:pool/dataset` prefix
- ZFS snapshot/clone/migrate require `vm_dir="zfs:pool/dataset"` in `/etc/rc.conf`
- geventwebsocket 0.10.1 has two Python 3.12 patches in `handler.py` — do not reinstall without re-applying
- nmdm device path: `console-ports` section of `vm info`, e.g. `/dev/nmdm-<name>.1B`
- **Memstick `.img` files** must be attached as `ahci-hd`, not `ahci-cd` — the UI auto-selects this
- **Multiple VLANs**: create one switch per VLAN, each with the same trunk uplink and a different VLAN ID

---

## License

BSD 2-Clause License
