# html-vm

A Flask web admin for [vm-bhyve](https://github.com/churchers/vm-bhyve) on FreeBSD.

Manage bhyve virtual machines from a browser — no command line required.

---

## Features

- **Dashboard** — VM tiles with CPU, memory, disk, network stats and host resource bars. Auto-refreshes every 10 s.
- **VM lifecycle** — start, stop, reboot, force power-off, force reset, destroy VMM context
- **Bulk ops** — start all autostart VMs, stop all running VMs
- **VM detail** — full parsed `vm info`, config edit (cpu, memory, loader, utctime)
- **Rename VM** — rename a stopped VM via `vm rename`
- **Disks** — add, remove, update (type, path/size, device) per-VM
- **Networks** — add, remove, update (switch, NIC type, MAC) per-VM
- **Create VM** — OS profile quick-fill (FreeBSD, Linux, Windows, OpenBSD), disk type, NIC type, ISO attach, optional ZFS dataset
- **Serial console** — in-browser terminal via [gotty](https://github.com/sorenisanerd/gotty) and nmdm devices
- **ISO images** — list, upload (XHR progress bar), delete, download from URL via `vm iso <url>`
- **Storage** — ZFS snapshots, rollback, clone, portable image pack/provision
- **Networks page** — vm-bhyve switches (create/delete), VXLAN (native + unicast tunnel), GRE tunnels, IPsec tunnels, bridge member management
- **VM migration** — `vm migrate` to a remote host
- **PAM authentication** — any local OS account can log in; 8-hour sessions
- **CSRF protection** on all POST routes
- **Error pages** — 403 (CSRF), 404, 413 (upload too large)
- **FreeBSD rc.d service** script included

---

## Requirements

- FreeBSD 15
- [vm-bhyve](https://github.com/churchers/vm-bhyve) installed and initialized
- Python 3.12
- `gotty` at `/usr/local/bin/gotty` (for serial console)
- `setkey(8)` at `/sbin/setkey` (base system, for IPsec)

Python packages (see [`requirements.txt`](requirements.txt)):

```
Flask>=3.0
python-pam>=1.0
```

gevent and geventwebsocket are installed via `pkg` and symlinked into `.venv` — do **not** `pip install` them.

---

## Installation

```sh
# 1. Clone
git clone https://github.com/failedrequest/html-vm.git
cd html-vm

# 2. Create venv and install packages
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3. Generate a secret key (persisted to .secret_key, mode 600)
#    The app creates this automatically on first run.

# 4. Run (must be root or have passwordless sudo for vm / bhyvectl / ifconfig)
sudo .venv/bin/python run.py
```

Open `http://<host>:8088/` and log in with any local OS account.

---

## rc.d service

```sh
# Copy the service script
sudo cp etc/rc.d/html_vm /usr/local/etc/rc.d/html_vm
sudo chmod 555 /usr/local/etc/rc.d/html_vm

# Enable in /etc/rc.conf
echo 'html_vm_enable="YES"' | sudo tee -a /etc/rc.conf

# Start
sudo service html_vm start
```

---

## Running tests

No hypervisor or root access required — all external I/O is mocked.

```sh
.venv/bin/python -m unittest tests/test_vm.py tests/test_app.py -v
```

Current: **222 tests — all pass**.

---

## Project layout

```
app.py          Flask app: all routes, CSRF, login/logout
vm.py           vm CLI wrapper, parsers, console registry, tunnel helpers
auth.py         PAM authentication
run.py          gevent + geventwebsocket WSGI entry point
static/         CSS
templates/      Jinja2 templates
tests/          Unit + route tests (fixtures in tests/fixtures/)
tools/          Diagnostic utilities
etc/rc.d/       FreeBSD rc.d service script
```

---

## Platform notes

- FreeBSD 15 , maybe it will work on 14 .
- `grub` loader not present; valid loaders: `bhyveload`, `uefi`, `uefi-csm`
- ZFS snapshot/clone/migrate require `vm_dir="zfs:pool/dataset"` in `/etc/rc.conf`
- geventwebsocket 0.10.1 has two Python 3.12 patches applied to `handler.py` — do not reinstall without re-applying them
- nmdm device: `console-ports` section of `vm info`, e.g. `/dev/nmdm-<name>.1B`

---

## License

BSD 2-Clause License

