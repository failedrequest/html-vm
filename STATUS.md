# html-vm — Project Status

**Last updated:** 2025-07-04  
**Branch:** main  
**Tests:** 227 passing (as of last push)  
**Repo:** git@github.com:failedrequest/html-vm.git

---

## What this project is

Flask web admin UI for vm-bhyve on FreeBSD 15.1.  
Runs as root on port 8088 (`sudo .venv/bin/python run.py`).  
Installs to `/usr/local/html-vm` via `install.sh`.

---

## Completed & pushed

| Area | Notes |
|---|---|
| Full Flask app | PAM login, CSRF, dashboard, VM lifecycle, disk/NIC edit |
| VM create | OS profiles, ZFS-aware storage section |
| ISO management | upload, fetch from URL (`vm iso`), delete |
| Serial console | gotty + nmdm, ConsoleRegistry, kill-stuck |
| VM log viewer | SSE live stream, two tabs, pause/clear |
| Snapshots/clone/image/migrate | Full coverage |
| Maintenance/reboot | Host reboot via `shutdown -r` |
| Dashboard health section | FreeBSD ver+arch, vm-bhyve version, uptime |
| Rename VM | `vm rename`, redirects to new name |
| Stop All / Start All | `/vms/stopall` + `/vms/startall` |
| Locked VM | `remove_lock_file()` + UI button |
| VM_DATASTORE | Reads from `sysrc -n vm_dir` at module load |
| ZFS dataset creation | `-p` flag, existing-dir safe |
| VXLAN / GRE / IPsec tunnels | Full create/destroy + bridge addm/deletem |
| **Switch config page** | `network_switch.html` template + 6 routes in app.py |
| GitHub issues #7–22 | Created via `create-issues.py` |

---

## In progress — issue #2 (switch config, final pieces)

### Done
- `vm.py`: `switch_add_iface`, `switch_remove_iface`, `switch_set_vlan`,
  `switch_set_nat`, `switch_set_address`, `switch_set_private`
- `app.py`: routes `network_configure`, `network_add_iface`,
  `network_remove_iface`, `network_set_vlan`, `network_set_nat`,
  `network_set_address`, `network_set_private`
- `templates/network_switch.html`: per-switch config page (all sections)

### NOT done (remaining work)
1. **`templates/networks.html`** — make switch names clickable links to
   `/network/<name>/configure`; add inline "Add to bridge" forms on each
   GRE and IPsec tunnel row; **remove** the entire "Bridge membership" section
   at the bottom of the page.
2. **Tests** — `test_app.py`: routes for `network_configure`,
   `network_add_iface`, `network_remove_iface`, `network_set_vlan`,
   `network_set_nat`, `network_set_address`, `network_set_private`.
   `test_vm.py`: `switch_add_iface`, `switch_remove_iface`, `switch_set_vlan`,
   `switch_set_nat`, `switch_set_address`, `switch_set_private`.
3. `pyflakes` clean + all 227+ tests passing.
4. `git add`, `git commit`, `git push`.

---

## File map

```
/home/nonesuch/html-vm/
├── app.py                     # All Flask routes
├── vm.py                      # vm CLI wrapper + parsers
├── run.py                     # gevent WSGI entry point
├── auth.py                    # PAM authentication
├── requirements.txt           # flask-sock==0.7.0 only
├── install.sh                 # FreeBSD installer
├── pyproject.toml
├── static/style.css
├── templates/
│   ├── base.html
│   ├── dashboard.html
│   ├── networks.html          ← NEEDS EDIT (step 1 above)
│   ├── network_switch.html    ← NEW (done, not yet committed)
│   ├── vm_detail.html
│   ├── vm_create.html
│   ├── vm_log.html
│   ├── vm_snapshots.html
│   ├── console.html
│   ├── isos.html
│   ├── storage.html
│   ├── migrate.html
│   ├── maintenance.html
│   ├── error.html
│   └── login.html
├── tests/
│   ├── test_vm.py             ← needs switch_* tests (step 2)
│   ├── test_app.py            ← needs switch config route tests (step 2)
│   └── fixtures/
└── tools/
    ├── vnc_diag.py
    └── ws_rfb_probe.py
```

---

## Key facts / gotchas

- **FreeBSD only** — no Linux tools, no GNU userland
- `geventwebsocket` is `pkg install py312-gevent-websocket` + symlink into `.venv` — never `pip install`
- SSH agent: `export SSH_AUTH_SOCK="/tmp/ssh-GibiYD4XnYZu/agent.53166"` before push
- `VM_DATASTORE` = `sysrc -n vm_dir` at module load (handles `zfs:` prefix)
- `IFACE_RE`, `_valid_iface`, `_valid_ip`, `_valid_cidr` defined above `switch_add_iface` in vm.py
- Run tests: `.venv/bin/python -m unittest tests/test_vm.py tests/test_app.py`
- App: `sudo .venv/bin/python run.py`
