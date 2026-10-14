#!/usr/bin/env python3
"""create-issues.py — create GitHub issues for html-vm vm-bhyve feature gaps.

Usage:
    GH_TOKEN=ghp_xxxx python3 create-issues.py

Requires: requests  (pip install requests)
          or falls back to urllib (stdlib)
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error

REPO = "failedrequest/html-vm"
API  = "https://api.github.com/repos/{}/issues".format(REPO)
TOKEN = os.environ.get("GH_TOKEN", "")

if not TOKEN:
    print("ERROR: set GH_TOKEN=<your github personal access token>", file=sys.stderr)
    sys.exit(1)


def post(method, url, data=None):
    body = json.dumps(data).encode() if data else None
    req  = urllib.request.Request(
        url, data=body, method=method,
        headers={
            "Authorization": "token " + TOKEN,
            "Content-Type":  "application/json",
            "Accept":        "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def create_issue(title, body):
    print("Creating: {} ...".format(title), end=" ", flush=True)
    status, resp = post("POST", API, {"title": title, "body": body})
    if status == 201:
        print("OK (#{})" .format(resp["number"]))
    else:
        print("FAILED ({})".format(status))
        print(resp)
    time.sleep(1)


# ============================================================================
# Issues
# ============================================================================

create_issue(
    "vm switch add/remove: cannot add or remove physical interfaces from a switch",
    """\
**Command:** `vm switch add <name> <interface>` / `vm switch remove <name> <interface>`

**Summary:** The Networks page lets you create and delete switches, but \
provides no way to add or remove a physical interface (e.g. `em0`) as a \
bridge member of an existing switch. This is the original bug reported in \
issue #2: after creating a switch you cannot bind it to a physical NIC.

**Proposed UI change — Networks page, per-switch row:**

Add an inline "Members" section under each switch in the table:
```
Switch: public   [delete]
  Members: em0  [Remove]
  Add member: [ em0 v ]  [Add]
```
The interface dropdown should list physical host interfaces \
(already available via `vm.list_host_interfaces()`).

**Backend (vm.py):**
```python
def switch_add_iface(name, iface):
    return run(["switch", "add", name, iface])

def switch_remove_iface(name, iface):
    return run(["switch", "remove", name, iface])
```

**Routes (app.py):**
```
POST /network/<name>/add-iface      form: iface=em0
POST /network/<name>/remove-iface   form: iface=em0
```
""")

create_issue(
    "vm switch vlan: assign 802.1q VLAN ID to a switch not implemented",
    """\
**Command:** `vm switch vlan <name> <vlan|0>`

**Summary:** vm-bhyve can assign a VLAN ID to a switch (0 = off). \
This is not exposed in the Networks UI.

**Proposed UI change — Networks page, per-switch configure section:**
```
VLAN ID: [ 0 ]  [Set]   (0 = disabled)
```

**Backend (vm.py):**
```python
def switch_set_vlan(name, vlan_id):
    return run(["switch", "vlan", name, str(vlan_id)])
```

**Route (app.py):**
```
POST /network/<name>/vlan   form: vlan_id=100
```
""")

create_issue(
    "vm switch nat: enable/disable NAT on a switch not implemented",
    """\
**Command:** `vm switch nat <name> <on|off>`

**Summary:** vm-bhyve can enable source NAT on a switch to give VMs \
internet access without bridging to a physical interface. Not exposed in the UI.

**Proposed UI change — Networks page, per-switch configure section:**
```
NAT:  [on]  [off]
```

**Backend (vm.py):**
```python
def switch_set_nat(name, enabled):
    return run(["switch", "nat", name, "on" if enabled else "off"])
```

**Route (app.py):**
```
POST /network/<name>/nat   form: nat=on|off
```
""")

create_issue(
    "vm switch address: assign IP gateway address to a switch not implemented",
    """\
**Command:** `vm switch address <name> <a.b.c.d/xx|none>`

**Summary:** vm-bhyve can assign an IP address to the bridge interface of a \
switch, making it the default gateway for guest VMs. Not exposed in the UI.

**Proposed UI change — Networks page, per-switch configure section:**
```
Gateway address: [ 192.168.100.1/24 ]  [Set]  [Clear]
```

**Backend (vm.py):**
```python
def switch_set_address(name, cidr):
    return run(["switch", "address", name, cidr])
```

**Route (app.py):**
```
POST /network/<name>/address   form: address=192.168.100.1/24
                                      address=none  (to clear)
```
""")

create_issue(
    "vm switch private: toggle private (isolated) mode on a switch not implemented",
    """\
**Command:** `vm switch private <name> <on|off>`

**Summary:** vm-bhyve can mark a switch as private, preventing VMs on it \
from communicating with the host or other bridges. The `-p` flag is available \
at creation time but toggling it afterwards via `vm switch private` is not \
exposed in the UI.

**Proposed UI change — Networks page, per-switch configure section:**
```
Private (isolated):  [on]  [off]
```

**Backend (vm.py):**
```python
def switch_set_private(name, enabled):
    return run(["switch", "private", name, "on" if enabled else "off"])
```

**Route (app.py):**
```
POST /network/<name>/private   form: private=on|off
```
""")

create_issue(
    "vm switch info: detailed per-switch statistics not exposed",
    """\
**Command:** `vm switch info [name]`

**Summary:** `vm switch info` shows detailed per-switch information including \
member interfaces, VLAN, NAT status, IP address, and port stats. The Networks \
page only shows the basic `vm switch list` columns. There is no way to see \
per-switch detail from the UI.

**Proposed UI change — Networks page, [Info] button per switch:**
```
Switch: public
  Type:    standard
  Bridge:  vm-public
  Members: em0
  NAT:     off
  VLAN:    0
  Address: 192.168.1.1/24
```

**Backend (vm.py):**
```python
def switch_info(name=None):
    args = ["switch", "info"]
    if name:
        args.append(name)
    return run(args)
```

**Route (app.py):**
```
GET /api/switch/<name>/info
```
""")


create_issue(
    "vm install: attach ISO and boot VM into installer",
    """\
**Command:** `vm install [-fi] <name> <iso>`

**Summary:** vm-bhyve has a dedicated `install` sub-command that attaches an \
ISO and boots the VM directly into the installer. The current UI only supports \
attaching an ISO at creation time as a conf entry. There is no "Install from \
ISO" action for an existing VM.

**What is missing:**
- No route or button to run `vm install <name> <iso>` on an existing stopped VM
- `-f` (force-stop first) and `-i` (interactive / foreground) flags not exposed

**Proposed UI change — VM detail page, new section below lifecycle buttons:**
```
Install from ISO
  ISO: [ FreeBSD-14.iso ▼ ]   [ ] Force stop first (-f)
  [Install]
```

**Backend (vm.py):**
```python
def install_vm(name, iso, force=False):
    args = ["install"]
    if force:
        args.append("-f")
    args += [name, os.path.join(ISO_DIR, iso)]
    return run(args, timeout=120)
```

**Route (app.py):**
```
POST /vm/<name>/install
```
""")

create_issue(
    "vm suspend / discard / suspendall: VM state suspend not exposed",
    """\
**Commands:**
- `vm suspend <name>` — save VM state to disk (requires BHYVE_SNAPSHOT kernel option)
- `vm discard [-f] <name>` — discard saved state without resuming
- `vm suspendall [-f]` — suspend all running VMs

**Summary:** Suspend/resume is not exposed anywhere in the UI. The VM detail \
page has no suspend button and there is no bulk-suspend on the dashboard.

**Note:** Suspend requires `BHYVE_SNAPSHOT` in the kernel. This should be \
detected at runtime and the button greyed out with a tooltip if unavailable:
```python
def bhyve_snapshot_supported():
    out, rc = _exec(["sysctl", "-n", "kern.features.bhyve_snapshot"])
    return rc == 0 and out.strip() == "1"
```

**Proposed UI changes:**
- VM detail page: "Suspend" button in lifecycle section (disabled + tooltip if unsupported)
- VM detail page: "Discard saved state" button (only shown when VM is in suspended state)
- Dashboard: "Suspend all" button alongside "Stop all" / "Start all"

**Backend (vm.py):**
```python
def suspend_vm(name, force=False):
    args = ["suspend"]
    if force: args.append("-f")
    return run(args + [name])

def discard_vm(name, force=False):
    args = ["discard"]
    if force: args.append("-f")
    return run(args + [name])

def vm_suspendall(force=False):
    args = ["suspendall"]
    if force: args.append("-f")
    return run(args, timeout=120)
```

**Routes (app.py):**
```
POST /vm/<name>/<action>  (extend — add "suspend", "discard")
POST /vms/suspendall
```
""")

create_issue(
    "vm reset / poweroff: graceful ACPI reset and power-off not exposed",
    """\
**Commands:**
- `vm reset [-f] <name>` — send ACPI reset signal (graceful, guest is notified)
- `vm poweroff [-f] <name>` — send ACPI power-off signal (graceful)

**Summary:** The VM detail page has "Force reset" and "Force power-off" via \
`bhyvectl`, but not the vm-bhyve graceful equivalents. These are different: \
`vm reset` sends an ACPI reset and waits; `bhyvectl --force-reset` kills \
immediately without notifying the guest.

**Proposed UI change — VM detail page, lifecycle section:**
Add alongside Stop/Reboot:
```
[Graceful reset]  [Power off]
```
Keep the existing Force stop section for when graceful ops fail.

**Backend (vm.py):**
```python
def reset_vm(name, force=False):
    args = ["reset"]
    if force: args.append("-f")
    return run(args + [name])

def poweroff_vm(name, force=False):
    args = ["poweroff"]
    if force: args.append("-f")
    return run(args + [name])
```

**Route (app.py):**
```
POST /vm/<name>/<action>  (extend — add "reset", "poweroff" actions)
```
""")

create_issue(
    "vm configure / edit: raw conf file editor not implemented",
    """\
**Commands:** `vm configure <name>` / `vm edit <name>` (opens conf in $EDITOR)

**Summary:** The VM detail page allows editing a fixed set of fields (cpu, \
memory, loader, utctime). There is no way to view or edit the raw `.conf` \
file from the browser for advanced options such as `hostbridge`, \
`bhyve_options`, `passthru`, `comports`, `graphics`, `debug`, etc.

**Proposed UI change — VM detail page, new collapsible "Raw conf" section:**
```
▶ Advanced / raw conf  [Edit]

loader="uefi"
cpu="2"
memory="2G"
disk0_type="virtio-blk"
...
```
Clicking Edit opens a `<textarea>` with the full conf content and a [Save] button. \
Save validates that every non-comment line matches `key="value"` before writing.

**Backend (vm.py):**
```python
def read_conf_raw(name):
    path = conf_path(name)
    if not path: return None
    with open(path) as f:
        return f.read()

def write_conf_raw(name, content):
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"): continue
        if not re.match(r'^[a-z0-9_]+=".*"$', line):
            return "invalid conf line: " + line, 1
    _atomic_write(conf_path(name), content)
    return "saved", 0
```

**Routes (app.py):**
```
GET  /vm/<name>/conf
POST /vm/<name>/conf/save
```
""")

create_issue(
    "vm passthru: PCI passthrough list and assignment not implemented",
    """\
**Command:** `vm passthru`

**Summary:** `vm passthru` lists all PCI devices with their passthrough \
readiness (whether they appear in `pptdevs` in loader.conf). There is no UI \
for viewing passthrough-capable devices or assigning them to a VM via \
`passthru0="slot/bus/function"` conf keys.

**Note:** Enabling passthrough also requires adding `pptdevs="X/Y/Z"` to \
`/boot/loader.conf` and rebooting — this is a host-level step that the UI \
should document but not automate.

**Proposed UI — VM detail page, new "PCI Passthrough" section:**
```
PCI Passthrough
  Slot    Description                Status
  0/2/0   Intel I219 Ethernet        ppt-ready
  0/3/0   NVIDIA GPU                 not ready (needs pptdevs in loader.conf)

  Add:  Slot [ 0/2/0 ]  [Add to VM]
  Current:  passthru0="0/2/0"  [Remove]
```

**Backend (vm.py):**
```python
def list_passthru_devices():
    out, rc = run(["passthru"])
    # parse tabular output: SLOT, DEVICE, READY, VMM
    ...

def add_passthru_conf(name, index, slot):
    # writes passthruN="slot" to VM conf
    ...

def remove_passthru_conf(name, index):
    # removes passthruN from VM conf
    ...
```
""")

create_issue(
    "vm datastore: multiple datastore management not implemented",
    """\
**Commands:**
- `vm datastore list` — list all datastores
- `vm datastore add <name> <spec>` — add a named datastore (path or zfs:dataset)
- `vm datastore remove <name>` — remove a named datastore

**Summary:** html-vm assumes a single datastore (read from `vm_dir` in \
rc.conf). vm-bhyve supports multiple named datastores. VMs and ISOs can be \
placed in any of them. There is no UI to manage datastores or to select a \
non-default one when creating a VM.

**What is missing:**
- No datastore list / add / remove page
- The Create VM form has no datastore selector (`-d datastore` flag)
- ISO upload always goes to the default datastore

**Proposed UI — Storage page, new "Datastores" section:**
```
Name      Type   Path / Dataset       VMs   ISOs
default   zfs    pool0/vm              4     3
backup    path   /mnt/backup/vms       1     0

[Add datastore]  Name: [    ]  Spec: [               ]  [Add]
```

VM create form: add a "Datastore" dropdown (default pre-selected).

**Backend (vm.py):**
```python
def list_datastores():        # vm datastore list
def add_datastore(name, spec): # vm datastore add
def remove_datastore(name):   # vm datastore remove
```
""")

create_issue(
    "vm img: cloud-init image download not implemented",
    """\
**Command:** `vm img [-46] [url]`

**Summary:** vm-bhyve can download cloud-init images into the datastore \
`.img` directory via `vm img <url>`. The ISO page already has a \
"Download from URL" form for ISOs (`vm iso`) but there is no equivalent \
for cloud-init images, and `.img` files are not listed anywhere.

**What is missing:**
- No UI to download a cloud-init image by URL
- No listing of `.img` files in the storage page (needed for `vm image provision`)

**Proposed UI — Storage page, new "Cloud-init Images (.img)" section:**
```
Filename                   Size       Del
FreeBSD-14-RELEASE.img     800 MiB    [delete]

Download image from URL:
  URL [ https://...             ]  [Fetch]
```

**Backend (vm.py):**
```python
def img_fetch(url):
    if not url or not url.startswith(("http://", "https://", "ftp://")):
        return "error: invalid URL", 1
    return run(["img", url], timeout=3600)

def list_imgs():
    img_dir = os.path.join(VM_DATASTORE, ".img")
    # similar to list_isos()
    ...
```

**Route (app.py):**
```
POST /imgs/fetch
```
""")

create_issue(
    "vm create: cloud-init provisioning (-C flag) not implemented",
    """\
**Command:** `vm create -C [-k pubkey-file] [-u user-data-file] [-n netconfig] <name>`

**Summary:** vm-bhyve supports creating cloud-init VMs with the `-C` flag \
which injects an SSH public key, user-data, and network config at creation \
time. The current Create VM form has no cloud-init support.

**What is missing:**
- No `-C` toggle on the Create VM form
- No fields for pubkey, user-data, or network config upload/paste

**Proposed UI — Create VM form, new "Cloud-init" section (shown when \
a .img file is selected as install source):**
```
[ ] Enable cloud-init provisioning (-C)

  SSH public key   [paste or upload .pub]
  User-data        [paste cloud-init YAML]
  Network config   [paste or leave blank for DHCP]
```

**Backend (vm.py):**
```python
def create_vm_cloudinit(name, cpu, memory, size,
                         pubkey=None, userdata=None, netconfig=None, **kw):
    # writes files to temp dir, passes -C -k -u -n to vm create
    ...
```
""")

create_issue(
    "vm snapshot: force flag (-f) for snapshotting a running VM not exposed",
    """\
**Command:** `vm snapshot -f <name[@snapshot]>`

**Summary:** `vm snapshot` without `-f` refuses to snapshot a running VM. \
The Snapshots page currently shows a warning for running VMs but provides \
no way to proceed. The `-f` flag is the correct fix.

**What is partially implemented:**
- `vm.vm_snapshot(name, snap_name)` exists but never passes `-f`
- The snapshots template shows a note for running VMs but no checkbox

**Fix needed in vm.py:**
```python
def vm_snapshot(name, snap_name=None, force=False):
    if not valid_name(name):
        return "error: invalid name", 1
    target = name if not snap_name else "{0}@{1}".format(name, snap_name)
    args = ["snapshot"]
    if force:
        args.append("-f")
    args.append(target)
    return run(args)
```

**Fix needed in vm_snapshots.html:**
Add a `[ ] Force (VM is running)` checkbox to the snapshot form, \
shown/enabled only when `v.running` is True. Pass `force=1` in the POST body \
and thread it through the route to `vm_snapshot()`.
""")

create_issue(
    "vm set / vm get: global vm-bhyve configuration not exposed",
    """\
**Commands:**
- `vm get [all|setting]` — read global vm-bhyve settings
- `vm set [setting=value] [...]` — write global settings

**Summary:** vm-bhyve stores global configuration in `.config/system.conf` \
(console type, compression, switch defaults, etc.). These can be read and \
changed with `vm get`/`vm set`. None of this is exposed in the html-vm UI.

**What is missing:**
- No page or section showing current global vm-bhyve settings
- No way to change them from the browser

**Proposed UI — Maintenance page or new "Settings" sidebar link:**
```
vm-bhyve global settings
  Key         Current value    New value       
  console     serial           [ serial ▼ ]  [Save]
  compress    xz               [ xz     ▼ ]  [Save]
```

**Backend (vm.py):**
```python
def vm_get(key="all"):
    return run(["get", key])

def vm_set(key, value):
    if not re.match(r'^[a-z_]+=\\S+$', "{}={}".format(key, value)):
        return "invalid setting", 1
    return run(["set", "{}={}".format(key, value)])
```

**Routes (app.py):**
```
GET  /settings         (renders settings page with vm get all output)
POST /settings/set     (calls vm_set(key, value))
```
""")

print("\nAll done.")
