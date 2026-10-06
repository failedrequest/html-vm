# Network Overlay Plan — html-vm

**Platform:** FreeBSD 15.1  
**Goal:** Extend the Networks page to create and manage VXLAN overlay networks,
GRE tunnels, and IPsec tunnels. Each tunnel type can be added as a member port
of a vm-bhyve bridge so VMs can reach the overlay.

**Key facts discovered:**

- `if_vxlan.ko`, `if_gre.ko`, `ipsec.ko` are all present in `/boot/kernel/`
- `if_bridge.ko` and `bridgestp.ko` are already loaded (vm-bhyve uses them)
- vm-bhyve has a **native vxlan switch type** (`vm switch create -t vxlan -i <iface> -n <vni> <name>`)
  — it creates a bridge + vxlan interface with multicast and manages it entirely
- vm-bhyve has a **manual switch type** (`vm switch create -t manual -b <bridge> <name>`)
  — wraps a pre-existing bridge; we manage tunnel membership ourselves
- `vm switch add <name> <interface>` adds a port to a standard switch bridge
- `setkey` is at `/sbin/setkey` (base system)
- The existing `vm-default` bridge is `192.168.1.x` upstream, `vm-default` bridge
- Host physical uplink: `em0` at `192.168.1.85`

---

## Architecture

```
                  ┌─────────────────────────────────┐
                  │         html-vm host             │
                  │                                  │
  VMs ──tap──► vm-default (bridge)                  │
                  │  addm: vxlan0 / gre0 / ipsec0   │
                  │                                  │
  vxlan0 ──UDP/4789──► remote host(s)               │
  gre0   ──IP/47 ──► remote host(s)                 │
  ipsec0 ──ESP   ──► remote host(s)                 │
                  └─────────────────────────────────┘
```

Two paths depending on tunnel type:

**Path A — vm-bhyve native VXLAN switch**  
`vm switch create -t vxlan -i em0 -n <VNI> <name>`  
vm-bhyve creates `vxlanN`, a new bridge, and registers everything.
VMs then attach to this new switch instead of `vm-default`.

**Path B — manual tunnel + bridge member (GRE, IPsec, custom VXLAN)**  
1. Create tunnel interface (`ifconfig greN create …` or `ifconfig ipsecN create …`)
2. Load kernel module if not yet loaded
3. Configure tunnel endpoints
4. `ifconfig vm-default addm greN` (or create a new bridge for isolation)
5. Optionally register as a vm-bhyve manual switch

---

## Steps

### Step 1 — VXLAN overlay via vm-bhyve native switch

This is the recommended path for VM-to-VM overlays across hosts.

**1a. Load kernel module (if not already)**
```sh
sudo kldload if_vxlan        # no-op if already in kernel
echo 'if_vxlan_load="YES"' | sudo tee -a /boot/loader.conf
```

**1b. Create the VXLAN switch**
```sh
# VNI 100, rides on em0, multicast group auto-derived from switch name
sudo vm switch create -t vxlan -i em0 -n 100 overlay0
```
This creates:
- `vxlan100` — the VXLAN tunnel endpoint (multicast mode, VNI 100)
- `vm-overlay0` — bridge with `vxlan100` as a member

**1c. Verify**
```sh
sudo vm switch list
ifconfig vxlan100
ifconfig vm-overlay0
```
Expected: `vxlan100` up, `vm-overlay0` bridge with `vxlan100` as member.

**1d. Attach a VM to the overlay switch**
Edit VM config or use vm-bhyve to set the switch to `overlay0`:
```sh
sudo vm switch add overlay0 tap1    # after vm is running
# or at VM create time: set network0_switch="overlay0" in .conf
```

**1e. Test multicast reachability (requires a second host)**
On remote host, run the same `vm switch create -t vxlan -i <iface> -n 100 overlay0`.
VMs on both hosts get L2 connectivity over the VXLAN overlay.

**1f. Persist across reboots**
vm-bhyve writes switch config to its datastore (`/var/vm/.config/system.conf`).
`vm-bhyve` is already started at boot via rc.d — no extra step needed.

---

### Step 2 — GRE tunnel attached to a bridge

Use this for point-to-point L3 tunnels to a remote host/router.

**2a. Load module**
```sh
sudo kldload if_gre
echo 'if_gre_load="YES"' | sudo tee -a /boot/loader.conf
```

**2b. Create GRE tunnel interface**
```sh
# local outer IP: 192.168.1.85 (em0)
# remote outer IP: 192.168.1.X  (the other host)
# inner tunnel IPs: 10.99.0.1/30 local, 10.99.0.2/30 remote
sudo ifconfig gre0 create
sudo ifconfig gre0 tunnel 192.168.1.85 192.168.1.X
sudo ifconfig gre0 inet 10.99.0.1/30 10.99.0.2 up
```

**2c. Add gre0 to vm-default bridge (for VM access)**
```sh
sudo ifconfig vm-default addm gre0
```
Or create an isolated bridge:
```sh
sudo ifconfig bridge1 create
sudo ifconfig bridge1 addm gre0 up
sudo vm switch create -t manual -b bridge1 gre-switch
```

**2d. Verify**
```sh
ifconfig gre0
ping 10.99.0.2          # ping the remote GRE endpoint
# from a VM on the bridge, route 10.99.0.0/30 via bridge gateway
```

**2e. Persist in /etc/rc.conf**
```sh
# /etc/rc.conf additions:
cloned_interfaces="gre0"
ifconfig_gre0="tunnel 192.168.1.85 192.168.1.X inet 10.99.0.1/30 10.99.0.2 up"
```

---

### Step 3 — IPsec tunnel (route-based, if_ipsec)

Use this to encrypt traffic between two hosts. Requires `setkey` for manual SA
configuration (no IKE daemon needed for static keys; strongSwan for production).

**3a. Load kernel module**
```sh
sudo kldload ipsec
echo 'ipsec_load="YES"' | sudo tee -a /boot/loader.conf
```

**3b. Create ipsec interface**
```sh
sudo ifconfig ipsec0 create reqid 100
sudo ifconfig ipsec0 inet tunnel 192.168.1.85 192.168.1.X
sudo ifconfig ipsec0 inet 172.16.99.1/30 172.16.99.2 up
```

**3c. Install Security Associations (manual keying)**
```sh
sudo setkey -c <<EOF
add 192.168.1.85 192.168.1.X esp 10000 -m tunnel -u 100 \
    -E aes-cbc "a16bytekey123456" -A hmac-sha256 "a32bytekeyforhmacsha256!!!!!!!!";
add 192.168.1.X 192.168.1.85 esp 10001 -m tunnel -u 100 \
    -E aes-cbc "a16bytekey123456" -A hmac-sha256 "a32bytekeyforhmacsha256!!!!!!!!";
EOF
```
> **Production note:** Replace manual keys with strongSwan (`security/strongswan` via pkg)
> using IKEv2 for automatic SA negotiation and rekeying.

**3d. Add ipsec0 to bridge**
```sh
sudo ifconfig vm-default addm ipsec0
# or create isolated bridge as in Step 2c
```

**3e. Verify**
```sh
ifconfig ipsec0
ping 172.16.99.2
setkey -D     # show installed SAs
setkey -DP    # show security policies
```

---

### Step 4 — Web UI changes in html-vm

The Networks page gains three new sections below the existing vm-bhyve switch table.

#### 4a. Backend — vm.py additions

New functions:

| Function | What it does |
|---|---|
| `create_vxlan_switch(name, vni, iface)` | Calls `vm switch create -t vxlan -i <iface> -n <vni> <name>` |
| `list_tunnel_interfaces()` | Parses `ifconfig -a` for `gre*`, `vxlan*`, `ipsec*` interfaces |
| `create_gre_tunnel(name, local_ip, remote_ip, inner_local, inner_remote)` | `ifconfig greN create`, `tunnel`, `inet`, optionally adds to a bridge |
| `destroy_gre_tunnel(name)` | `ifconfig greN destroy` |
| `create_ipsec_tunnel(name, local_ip, remote_ip, inner_local, inner_remote, reqid)` | `ifconfig ipsecN create reqid N`, `tunnel`, `inet` |
| `destroy_ipsec_tunnel(name)` | `ifconfig ipsecN destroy` |
| `bridge_addm(bridge, iface)` | `ifconfig <bridge> addm <iface>` |
| `bridge_deletem(bridge, iface)` | `ifconfig <bridge> deletem <iface>` |
| `kld_load(module)` | `kldload <module>` — idempotent, ignores "already loaded" |

#### 4b. Backend — app.py additions

New routes:

| Method | Path | Action |
|---|---|---|
| POST | `/network/vxlan/create` | Call `create_vxlan_switch` |
| POST | `/network/gre/create` | Call `create_gre_tunnel` |
| POST | `/network/gre/<name>/delete` | Call `destroy_gre_tunnel` |
| POST | `/network/ipsec/create` | Call `create_ipsec_tunnel` |
| POST | `/network/ipsec/<name>/delete` | Call `destroy_ipsec_tunnel` |
| POST | `/network/bridge/<bridge>/addm` | Call `bridge_addm` |
| POST | `/network/bridge/<bridge>/deletem` | Call `bridge_deletem` |
| GET  | `/api/tunnels` | JSON list of all tunnel interfaces (for live status) |

#### 4c. Frontend — networks.html sections

Three new collapsible cards below the existing switch table:

**VXLAN overlay card**
- Fields: Name, VNI (1–16777215), Physical interface (select from host interfaces)
- Submit: POST `/network/vxlan/create`
- Table: existing vxlan switches (from `vm switch list` filtered by type=vxlan)

**GRE tunnels card**
- Fields: Name (gre0…greN auto or manual), Local outer IP, Remote outer IP,
  Inner local IP/prefix, Inner remote IP
- Optional: bridge to add to (select from existing vm-bhyve switches)
- Table: live gre* interfaces from `/api/tunnels`

**IPsec tunnels card**
- Fields: Name, Local outer IP, Remote outer IP, Inner local IP/prefix,
  Inner remote IP, ReqID (auto)
- Note: "Keys must be configured separately via setkey(8) or strongSwan"
- Table: live ipsec* interfaces from `/api/tunnels`

---

### Step 5 — Tests

#### 5a. Unit tests — vm.py (test_vm.py additions)

| Test class | Tests |
|---|---|
| `TunnelHelpersTests` | `list_tunnel_interfaces` parses mock ifconfig output correctly |
| `TunnelHelpersTests` | `create_vxlan_switch` calls `vm switch create -t vxlan ...` with correct args |
| `TunnelHelpersTests` | `create_gre_tunnel` calls `ifconfig gre0 create`, `tunnel`, `inet` in order |
| `TunnelHelpersTests` | `destroy_gre_tunnel` calls `ifconfig gre0 destroy` |
| `TunnelHelpersTests` | `kld_load` ignores rc=1 with "already loaded" in output |
| `TunnelHelpersTests` | `bridge_addm` / `bridge_deletem` call correct ifconfig commands |

#### 5b. Route tests — app.py (test_app.py additions)

| Test class | Tests |
|---|---|
| `TunnelRouteTests` | POST `/network/vxlan/create` valid → 302 + flash ok |
| `TunnelRouteTests` | POST `/network/vxlan/create` missing field → 400 |
| `TunnelRouteTests` | POST `/network/gre/create` valid → 302 + flash ok |
| `TunnelRouteTests` | POST `/network/gre/gre0/delete` → calls destroy, 302 |
| `TunnelRouteTests` | POST `/network/ipsec/create` valid → 302 + flash ok |
| `TunnelRouteTests` | GET `/api/tunnels` → JSON with `gre`, `vxlan`, `ipsec` keys |

#### 5c. Live integration tests (manual, requires root)

Run these on the host after the web UI changes are deployed:

```sh
# 1. VXLAN switch via UI
#    - Open Networks page, fill VXLAN form: name=overlay0, VNI=100, iface=em0
#    - Submit, check flash "ok"
#    - Verify: sudo vm switch list  →  overlay0  vxlan
#    - Verify: ifconfig vxlan100    →  up, correct VNI and local addr
#    - Verify: ifconfig vm-overlay0 →  bridge with vxlan100 member

# 2. GRE tunnel via UI (loopback self-test, no remote needed)
#    - Create: name=gre0, local=127.0.0.1, remote=127.0.0.2,
#              inner_local=10.99.0.1/30, inner_remote=10.99.0.2
#    - Verify: ifconfig gre0  →  tunnel 127.0.0.1 → 127.0.0.2, inet 10.99.0.1
#    - Verify: ping -c1 10.99.0.2  →  1 packet received
#    - Delete via UI, verify: ifconfig gre0 → no such interface

# 3. IPsec interface creation (no SA needed to check interface exists)
#    - Create: name=ipsec0, local=192.168.1.85, remote=192.168.1.1 (gateway),
#              inner_local=172.16.0.1/30, inner_remote=172.16.0.2
#    - Verify: ifconfig ipsec0  →  tunnel 192.168.1.85 → 192.168.1.1
#    - Note: traffic won't pass until SAs loaded via setkey

# 4. Add tunnel to bridge via UI
#    - With gre0 up, use bridge addm: bridge=vm-default, iface=gre0
#    - Verify: ifconfig vm-default  →  member: gre0
#    - Remove via UI, verify member gone

# 5. VM connectivity over VXLAN (requires two hosts)
#    - Host B: sudo vm switch create -t vxlan -i <iface> -n 100 overlay0
#    - Host A VM: ip addr add 10.100.0.1/24 dev vtnet0 (or bhyve nic)
#    - Host B VM: ip addr add 10.100.0.2/24 dev vtnet0
#    - Ping 10.100.0.2 from Host A VM  →  success
```

---

## Implementation order

1. `vm.py` — add tunnel helper functions + tests  ← **start here**
2. `app.py` — add new routes  
3. `templates/networks.html` — add three new form cards + tunnel table  
4. Run full test suite (`pyflakes` + unittest)  
5. Live integration tests (Steps 5c above)  

---

## Decisions / constraints

| Decision | Reason |
|---|---|
| VXLAN uses vm-bhyve native (`-t vxlan`) | vm-bhyve manages lifecycle, bridge creation, rc.d persistence — less code |
| GRE and IPsec use raw `ifconfig` | vm-bhyve has no GRE/IPsec switch type; we shell out directly |
| IPsec keys NOT managed by the UI | Key material in a web form is a security risk; setkey/strongSwan is out of scope |
| No IKE daemon integration | strongSwan is a separate pkg; out of scope for this iteration |
| Kernel modules loaded on demand | `kldload` is idempotent; checked before each create operation |
| Tunnels are not persisted to rc.conf by the UI | Phase 1 only; rc.conf persistence is a follow-up step |
| MTU on VXLAN | 50-byte overhead; physical MTU must be ≥ 1550 or inner MTU set to 1450 |
