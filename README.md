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

### VNC graphics console
- Full HTML5 VNC display embedded directly in the browser — no extra client required
- Powered by [noVNC](https://novnc.com/) and [websockify](https://github.com/novnc/websockify)
- **Auto-reconnect** — if the VM reboots inside the console, the connection restores automatically (3 s retry loop) without any page reload
- **Scale to fit** — canvas always fills the console panel regardless of the VM's resolution
- **One-click enable for Windows VMs** — if `graphics` is not yet configured, the VNC page shows an *Enable VNC graphics* button that writes all required conf keys (`graphics`, `graphics_port`, `graphics_res`, `xhci_mouse`) directly into the VM conf and picks the next free VNC port automatically (≥ 5900, no conflicts with other VMs)
- Websockify proxy runs per-VM on ports 19200–19299; bhyve VNC listener stays on its configured TCP port (59xx)
- Console page accessible even when VM is stopped — shows a clear "start the VM first" message so you can still enable/configure VNC before booting
- **Clean process teardown** — Stop VNC kills the entire websockify process group (SIGTERM → SIGKILL), leaving no orphan processes behind

### Install from ISO
- **Install from ISO** section on every VM detail page — pick an ISO from the dropdown, optional *Force stop first* (`-f`) checkbox, runs `vm install [-f] <name> <iso>`
- VM boots directly into the installer; open the VNC console to interact with it
- Falls back gracefully with a link to the ISO upload page when no ISOs are available

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

### Screenshots

**Dashboard**
<img width="1490" height="811" alt="dashboard" src="https://github.com/user-attachments/assets/85dd9398-1790-40e5-8a7b-a11b22f3ab71" />
**Network**
<img width="1490" height="2838" alt="network" src="https://github.com/user-attachments/assets/94bac269-ed6e-474c-9c36-e41aad977a81" />

**VM Console**
<img width="1490" height="865" alt="vm-console" src="https://github.com/user-attachments/assets/1aaea6a5-96e6-4a0b-8b72-03e7928df8db" />

**VNC Console**
<img width="1860" height="967" alt="Screenshot 2026-11-01 at 16-22-30 VNC — win0 · html-vm" src="https://github.com/user-attachments/assets/9a2cca73-17ce-4e4a-b175-e1ee057abf43" />

---

## Requirements

- FreeBSD 14 or 15
- [vm-bhyve](https://github.com/churchers/vm-bhyve) installed and initialized (`vm init`)
- Python 3.12
- `gotty` at `/usr/local/bin/gotty` (serial console — `pkg install gotty`)
- `websockify` at `/usr/local/bin/websockify` (VNC console — `pkg install py312-websockify`)
- noVNC at `/usr/local/libexec/novnc/` (VNC console — `pkg install novnc`)
- `setkey(8)` at `/sbin/setkey` (base system, for IPsec)

All of the above are installed automatically by `install.sh`.

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
- Installs pkg dependencies (Python 3.12, gevent-websocket, gotty, websockify, novnc, etc.)
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

Current: **291 tests — all pass**.

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
  vnc_console.html    noVNC graphics console (embedded RFB, auto-reconnect)
  isos.html           ISO management
  storage.html        ZFS snapshots, clone, images
  migrate.html        VM migration
tests/              Unit + route tests (260 total, fixtures in tests/fixtures/)
tools/              Diagnostic utilities (vnc_diag.py, ws_rfb_probe.py)
etc/rc.d/html_vm    FreeBSD rc.d service script
```

---

## Platform notes

- **FreeBSD 14 / 15** — BSD userland only
- Valid loaders: `bhyveload` (FreeBSD), `uefi`, `uefi-csm` — `grub` is not installed
- `vm_dir` is read from `sysrc -n vm_dir` at startup; handles `zfs:pool/dataset` prefix
- ZFS snapshot/clone/migrate require `vm_dir="zfs:pool/dataset"` in `/etc/rc.conf`
- geventwebsocket 0.10.1 has two Python 3.12 patches in `handler.py` — do not reinstall without re-applying
- nmdm device path: `console-ports` section of `vm info`, e.g. `/dev/nmdm-<name>.1B`
- **Memstick `.img` files** must be attached as `ahci-hd`, not `ahci-cd` — the UI auto-selects this
- **Multiple VLANs**: create one switch per VLAN, each with the same trunk uplink and a different VLAN ID

## Windows VMs

Windows guests require UEFI and a VNC graphics output — they have no serial console.

### Setting up a new Windows VM

1. **Create the VM** — use the *Windows* OS profile in the Create VM form. This selects `uefi` loader, `virtio-blk` disk, and `virtio-net` NIC automatically.
2. **Enable VNC** — navigate to the VM detail page → *VNC console* → click **⚙ Enable VNC graphics**. This writes the following into the VM conf and picks a free port:
   ```
   graphics="yes"
   graphics_port="59xx"   # auto-assigned, unique per VM
   graphics_res="1280x720"
   xhci_mouse="yes"
   ```
3. **Attach a Windows ISO** — add the ISO as an `ahci-cd` device in the Disks section.
4. **Start the VM** — click Start on the VM detail page.
5. **Open VNC** — return to *VNC console* → **▶ Start VNC console**. The Windows installer appears in the browser.
6. **After install** — remove the ISO disk, reboot the VM. The VNC console auto-reconnects after the reboot without any page reload.

### Notes
- `xhci_mouse="yes"` gives accurate mouse tracking in the noVNC canvas without needing mouse capture.
- VNC port range used by html-vm: 5900+ (one port per VM, auto-assigned with no conflicts).
- Websockify proxy range: 19200–19299 (one port per active VNC session).
- The VNC console page is reachable even when the VM is stopped so you can configure graphics before the first boot.

---

## License

BSD 2-Clause License
