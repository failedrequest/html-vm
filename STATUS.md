# html-vm — Project Status

**Last updated:** 2025-07-19
**Platform:** FreeBSD 15.1 · Python 3.12 · Flask 3.1 · vm-bhyve  
**Entry point:** `sudo .venv/bin/python run.py` (gevent + geventwebsocket, port 8088)

---

## What this is

A Flask web admin for [vm-bhyve](https://github.com/churchers/vm-bhyve) (bhyve hypervisor manager).  
Runs as root (or via `sudo -n`) and talks to the `vm` CLI at `/usr/local/sbin/vm`.  
Authentication is OS-level PAM — any local account can log in.

---

## Files

| File | Purpose |
|---|---|
| [`app.py`](app.py) | Flask app: all routes, CSRF, login/logout |
| [`vm.py`](vm.py) | `vm` CLI wrapper + pure-function parsers, conf rewriters |
| [`auth.py`](auth.py) | PAM authentication (`python-pam`) against local OS accounts |
| [`run.py`](run.py) | Production entry point — gevent + geventwebsocket WSGI server |
| [`requirements.txt`](requirements.txt) | `Flask>=3.0`, `python-pam>=1.0` |
| [`static/style.css`](static/style.css) | Self-contained CSS — no external framework or CDN |
| [`templates/base.html`](templates/base.html) | Nav bar + flash messages |
| [`templates/dashboard.html`](templates/dashboard.html) | VM list tiles + host resource bars + auto-refresh JS |
| [`templates/vm_detail.html`](templates/vm_detail.html) | VM detail: disks, networks, edit/add/remove forms, lifecycle, console link |
| [`templates/vm_create.html`](templates/vm_create.html) | Create VM form with OS profile quick-fill JS |
| [`templates/console.html`](templates/console.html) | gotty iframe serial console |
| [`templates/isos.html`](templates/isos.html) | ISO list + XHR upload with progress bar |
| [`templates/networks.html`](templates/networks.html) | Switches + VXLAN/GRE/IPsec tunnel management |
| [`templates/login.html`](templates/login.html) | PAM login form |
| [`templates/error.html`](templates/error.html) | 403 / 404 / 413 error page |
| [`tests/test_vm.py`](tests/test_vm.py) | vm.py unit tests |
| [`tests/test_app.py`](tests/test_app.py) | Flask route tests |
| [`tests/fixtures/vm_info_all.txt`](tests/fixtures/vm_info_all.txt) | `vm info` sample: testvm1 (stopped), testvm2 (running) |
| [`tests/fixtures/vm_switch_list.txt`](tests/fixtures/vm_switch_list.txt) | `vm switch list` sample |
| [`tests/fixtures/vm_list_v.txt`](tests/fixtures/vm_list_v.txt) | `vm list -v` sample |
| [`pyproject.toml`](pyproject.toml) | Poetry project manifest |
| [`etc/rc.d/html_vm`](etc/rc.d/html_vm) | FreeBSD rc.d service script |
| [`.secret_key`](.secret_key) | Persistent SECRET_KEY (mode 600) |

---

## Routes

| Method | Path | Description |
|---|---|---|
| GET | `/login` | Login page |
| POST | `/login` | Authenticate via PAM |
| POST | `/logout` | Clear session |
| GET | `/` | Dashboard — VM list + host resource bars |
| GET | `/api/vms` | JSON VM list + aggregate stats |
| GET | `/api/host` | JSON host CPU/RAM/datastore info |
| GET | `/vm/<name>` | VM detail page |
| POST | `/vm/<name>/<action>` | start / stop / reboot / delete / update |
| GET | `/vm/create` | Create VM form |
| POST | `/vm/create` | Run `vm create` + post-configure conf |
| GET | `/vm/<name>/console` | Console page (gotty iframe) |
| POST | `/vm/<name>/console/start` | Start gotty console process |
| POST | `/vm/<name>/console/stop` | Stop gotty console process |
| POST | `/vm/<name>/console/kill` | Kill stuck VM (bhyvectl --destroy) |
| GET | `/api/consoles` | JSON active console registry |
| POST | `/vm/<name>/disk/add` | Add disk (`vm add -d disk`) |
| POST | `/vm/<name>/disk/<int:idx>/remove` | Remove disk slot + renumber conf |
| POST | `/vm/<name>/disk/<int:idx>/update` | Update disk type/path/dev |
| POST | `/vm/<name>/network/add` | Add NIC (`vm add -d network`) |
| POST | `/vm/<name>/network/<int:idx>/remove` | Remove NIC slot + renumber conf |
| POST | `/vm/<name>/network/<int:idx>/update` | Update NIC switch/type/mac |
| GET | `/isos` | ISO list + upload form |
| POST | `/isos/upload` | Upload an `.iso` / `.img` file |
| POST | `/isos/<filename>/delete` | Delete an ISO |
| GET | `/networks` | Switches + tunnel list |
| POST | `/networks` | Create switch |
| POST | `/network/<name>/delete` | Destroy switch |
| POST | `/network/vxlan/create` | Create vm-bhyve VXLAN switch |
| POST | `/network/vxlan/tunnel/create` | Create unicast VXLAN tunnel |
| POST | `/network/vxlan/tunnel/<name>/delete` | Destroy unicast VXLAN tunnel |
| POST | `/network/gre/create` | Create GRE tunnel |
| POST | `/network/gre/<name>/delete` | Destroy GRE tunnel |
| POST | `/network/ipsec/create` | Create IPsec tunnel |
| POST | `/network/ipsec/<name>/delete` | Destroy IPsec tunnel |
| POST | `/network/bridge/<bridge>/addm` | Add member interface to bridge |
| POST | `/network/bridge/<bridge>/deletem` | Remove member interface from bridge |
| GET | `/api/tunnels` | JSON tunnel interface list |

---

## Architecture — serial console

`ConsoleRegistry` singleton manages gotty processes per VM.

```
Browser
    ↕  iframe
gotty -w --reconnect -p 190xx  cu -l /dev/nmdm-<name>.1B
    ↕  nmdm character device
bhyve guest serial port
```

`vm.console_registry.start(name, nmdm)` → picks port 19100–19199, spawns gotty.  
`vm.console_registry.stop(name)` → kills gotty.  
`vm.console_registry.kill_stuck(name)` → `bhyvectl --vm=<name> --destroy`.

---

## vm-bhyve conf file format

Disks use numbered keys: `disk0_name`, `disk0_type`, `disk0_dev`, `disk1_name`, ...  
Networks use: `network0_type`, `network0_switch`, `network0_mac`, `network1_type`, ...

- `disk_dev` values: `file`, `zvol`, `sparse-zvol`, `custom` (absolute ISO path)
- `disk_type` values: `virtio-blk`, `ahci-hd`, `nvme`, `ahci-cd`
- `network_type` values: `virtio-net`, `e1000`, `vmxnet3`

Conf edits are done by rewriting the `.conf` file directly using `_conf_set` / `_conf_del` / `_conf_value` helpers in `vm.py`.

---

## Test status

```
222 tests — OK (all pass)
pyflakes — 0 warnings (pre-existing unused-import notices in test files only)
```

### `tests/test_vm.py` — vm.py unit tests (93 tests)

| Class | Tests | Coverage |
|---|---|---|
| `ParseVmInfoTests` | 6 | `parse_vm_info` |
| `HelpersTests` | 6 | `bytes_value`, `fmt_bytes`, `valid_*` |
| `ConfHelpersTests` | 4 | `_conf_set`, `_conf_del`, `_conf_value` |
| `SwitchesTests` | 1 | `parse_switches` |
| `AggregateTests` | 2 | `vm_aggregate_stats` |
| `OsProfilesTests` | 2 | OS profiles keys + loaders |
| `ParseVmListVTests` | 7 | `parse_vm_list_v` |
| `SocatBridgeTests` / `ConsoleRegistryTests` | 15 | console registry, gotty lifecycle |
| `TunnelTests` | 15 | VXLAN/GRE/IPsec create/destroy, `list_tunnel_interfaces` |
| `DiskNetConfTests` | 35 | `vm_conf_disks/networks`, `add/remove/update_disk/network_conf` |

### `tests/test_app.py` — Flask route tests (64 tests)

| Class | Tests | Coverage |
|---|---|---|
| `AuthTests` | 7 | login/logout, next= redirect |
| `CsrfTests` | 3 | missing/wrong/correct |
| `DashboardTests` | 4 | `/`, `/api/vms`, `/api/host` |
| `VmDetailTests` | 7 | detail, lifecycle, update |
| `VmCreateTests` | 3 | GET, POST success/fail |
| `VmConsoleTests` | 8 | running, stopped, console start/stop/kill |
| `IsoTests` | 6 | list, upload, delete |
| `NetworkTests` | 5 | switches CRUD |
| `TunnelTests` | 14 | VXLAN/GRE/IPsec/bridge routes |
| `ErrorHandlerTests` | 2 | 403, 404 |
| `FlashResultTests` | 3 | success/fail/bytes |
| `SwitchNamesTests` | 3 | default always present |
| `WsAuthedTests` | 3 | cookie auth |
| `VmDiskNetRouteTests` | (new) | disk/network add/remove/update routes |

Run all tests:

```sh
.venv/bin/python -m unittest tests/test_vm.py tests/test_app.py -v
```

---

## What works

- [x] PAM login / logout / CSRF protection
- [x] 8-hour session lifetime, SECRET_KEY persists to `.secret_key`
- [x] Dashboard: VM tiles, CPU/memory/disk/net, auto-refresh (10 s)
- [x] Host resource bars: CPU count, RAM total, datastore used/total
- [x] VM detail: full parsed `vm info` — disks, networks, conf tables
- [x] VM lifecycle: start / stop / reboot / delete
- [x] VM config edit: cpu, memory, loader, utctime
- [x] Create VM: `vm create` + full post-conf (OS profile, disk type, NIC type, switch, ISO)
- [x] ISO management: list, upload with XHR progress bar, delete
- [x] Network switches: list, create, delete
- [x] VXLAN (vm-bhyve native + unicast tunnel), GRE, IPsec tunnels
- [x] Bridge addm/deletem
- [x] Serial console via gotty (`ConsoleRegistry` singleton, iframe)
- [x] `bhyvectl --destroy` for stuck VMs
- [x] Error pages: 403 (CSRF), 404, 413 (upload too large)
- [x] `pyflakes` passes with 0 warnings
- [x] `etc/rc.d/html_vm` — FreeBSD rc.d service script
- [x] noVNC / VNC permanently removed
- [x] **vm.py disk/network conf functions** — `vm_conf_disks`, `vm_conf_networks`, `add/remove/update_disk_conf`, `add/remove/update_network_conf`
- [x] **Disk/network edit UI** — 6 routes in `app.py`, edit/add/remove sections in `vm_detail.html`, 48 new tests (205 total)
- [x] **Rename VM** — `vm rename` via `/vm/<name>/rename` POST; form in `vm_detail.html`
- [x] **Stop all / Start all** — `vm stopall/startall` via `/vms/stopall` and `/vms/startall`; buttons in `dashboard.html`
- [x] **Download ISO from URL** — `vm iso <url>` via `/isos/fetch` POST; form in `isos.html`

---

## Platform notes

- FreeBSD 15.1 only — no Linux assumptions
- `grub` loader not installed — only `bhyveload`, `uefi`, `uefi-csm` valid
- `disk_dev` must be `"custom"` for absolute ISO paths
- nmdm device: `console-ports` section of `vm info`, e.g. `/dev/nmdm-<name>.1B`
- `bootloader` state treated as `running=True`
- gevent + geventwebsocket installed via `pkg`, symlinked into `.venv` — do not `pip install`
- geventwebsocket 0.10.1 has two Python 3.12 patches in handler.py — do not reinstall without re-applying
- `gotty` at `/usr/local/bin/gotty`

---

## How to run

```sh
sudo .venv/bin/python run.py
```

Server: gevent WSGI on `0.0.0.0:8088`.  
Open: `http://<host>:8088/`
