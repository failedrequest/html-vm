# vm-bhyve features not yet exposed in html-vm

Generated from `man vm` and `vm help` output on vm-bhyve v1.7.5 / FreeBSD 15.1.

## Fully implemented

| Feature | Where |
|---|---|
| VM list + detail | Dashboard, VM detail |
| VM create / destroy | VM create form |
| Start / stop / reboot | VM detail lifecycle |
| **Force power-off / reset / destroy VMM** | VM detail — Force stop section |
| Config edit (cpu, memory, loader, utctime) | VM detail |
| Disk add / remove / update | VM detail — Disks |
| Network add / remove / update | VM detail — Networks |
| ISO upload / delete | Storage → ISO Images |
| Serial console via gotty | VM detail / Console page |
| Switch create / delete | Networks |
| VXLAN switch (vm-bhyve native) | Networks |
| VXLAN unicast tunnel | Networks |
| GRE tunnel | Networks |
| IPsec tunnel | Networks |
| Bridge addm / deletem | Networks |
| **ZFS snapshot / rollback / delete snapshot** | Storage → Snapshots |
| **Clone VM** | Storage → Snapshots |
| **Portable VM image create / provision / delete** | Storage |
| **VM migration to remote host** | Migrate VM |
| Host reboot | Maintenance |

---

## Not yet exposed — implementation gaps

### VM lifecycle

| Feature | vm-bhyve command | Notes |
|---|---|---|
| Suspend VM (save state) | `vm suspend <name>` | Experimental; requires `BHYVE_SNAPSHOT` kernel option |
| Resume suspended VM | `vm start <name>` (auto-detects) | Paired with suspend |
| Discard saved state | `vm discard [-f] <name>` | Force-remove snapshot state without resuming |
| Suspend all | `vm suspendall [-f]` | Bulk suspend |
| Stop all | `vm stopall [-f]` | Bulk stop |
| Start all (autostart) | `vm startall` | Starts VMs listed in `$vm_list` rc.conf var |
| Rename VM | `vm rename <name> <new>` | VM must be stopped |
| Reset VM (vm-bhyve graceful reset) | `vm reset [-f] <name>` | Distinct from `bhyvectl --force-reset` |
| Power-off (vm-bhyve) | `vm poweroff [-f] <name>` | Distinct from `bhyvectl --force-poweroff` |

### VM creation

| Feature | vm-bhyve command | Notes |
|---|---|---|
| Cloud-init provisioning | `vm create -C -k pubkey -u userdata -n netconfig` | Requires cloud-init image |
| Provision from VM image | `vm image provision <uuid> <name>` | **Partially implemented** in Storage |
| Install from ISO (attach + boot) | `vm install [-fi] <name> <iso>` | Separate from create; UI could offer "Install ISO" button |
| Foreground / interactive start | `vm start -f` / `vm start -i` | Advanced; useful for debugging boot |

### Snapshots (ZFS only)

| Feature | vm-bhyve command | Notes |
|---|---|---|
| Snapshot a running VM | `vm snapshot -f <name>` | `-f` = force on running guest; **partially implemented** (note shown) |
| Clone from specific snapshot | `vm clone <name>@<snap> <new>` | **Implemented** via snap selector |
| Multi-stage migration | `vm migrate -1` / `-2 -i <snap>` | Split migration into two phases; not exposed |

### Networking

| Feature | vm-bhyve command / conf key | Notes |
|---|---|---|
| VLAN on switch | `vm switch vlan <name> <id>` | Assign 802.1q VLAN to an existing switch |
| NAT on switch | `vm switch nat <name> on\|off` | Source NAT for guest internet access |
| Switch address (IP) | `vm switch address <name> <cidr>` | Assign IP to bridge (gateway for guests) |
| Private switch mode toggle | `vm switch private <name> on\|off` | Toggle after creation |
| Switch remove interface | `vm switch remove <name> <iface>` | Remove physical port from bridge |
| Switch info | `vm switch info [name]` | Detailed switch + port stats |
| Per-guest `network0_device` | conf key | Use pre-existing tap/interface instead of dynamic one |
| Per-guest `network0_span` | conf key | Add as span port instead of bridge member |
| VALE / netgraph switch types | `vm switch create -t vale\|netgraph` | High-performance switching |
| Manual switch type | `vm switch create -t manual -b bridge0` | Attach to existing manually-managed bridge |

### Storage

| Feature | vm-bhyve / zfs command | Notes |
|---|---|---|
| Multiple datastores | `vm datastore add/remove/list` | Add secondary storage locations |
| ISO datastore | `vm datastore iso <name> <path>` | Point vm-bhyve at an arbitrary ISO directory |
| Download ISO | `vm iso <url>` | Fetch ISO directly from URL into datastore |
| Download cloud-init image | `vm img <url>` | Fetch cloud-init image |
| Image list | `vm image list` | **Implemented** in Storage |

### Guest configuration (conf file keys not exposed in UI)

| Key | Purpose |
|---|---|
| `cpu_sockets`, `cpu_cores`, `cpu_threads` | CPU topology (FreeBSD 12+) |
| `wired_memory` | Wire guest RAM |
| `hostbridge` | `default` / `amd` / `none` |
| `comports` | `com1` / `com2` / `com1 com2` |
| `graphics` | VGA framebuffer + VNC |
| `graphics_port`, `graphics_listen`, `graphics_res`, `graphics_wait` | VNC config |
| `virt_random` | virtio-rng device |
| `uefi_vars` | Persistent UEFI variable storage |
| `bhyveload_loader`, `bhyveload_args` | Custom bhyveload options |
| `loader_timeout` | GRUB/bhyveload boot timeout |
| `debug` | Write bhyve output to `bhyve.log` |
| `bhyve_options` | Raw extra args to bhyve (CPU pinning etc.) |
| `ignore_bad_msr` | AMD MSR compatibility |
| `fwcfg` | `bhyve` or `qemu` fw_cfg interface |
| `ahci_device_limit` | AHCI devices per controller (FreeBSD 12+) |
| `passthru0`, `passthru0_options` | PCI passthrough |
| `disk0_opts` | Extra per-disk options |
| `disk0_dev=iscsi` + `disk0_name=session/lun` | iSCSI disk |
| `network0_mac` | Fixed MAC address (exposed) |
| `grub_installX`, `grub_runX`, `grub_run_partition` etc. | GRUB boot config |

### Passthrough

| Feature | vm-bhyve command | Notes |
|---|---|---|
| List PCI devices + passthru status | `vm passthru` | Shows all PCI devices and readiness |
| Assign passthru device | conf: `passthru0="1/2/3"` | Requires `pptdevs` in loader.conf |

### Global settings

| Feature | vm-bhyve command | Notes |
|---|---|---|
| Get/set global config | `vm get all` / `vm set key=value` | Console type, compress, etc. |
| Datastore list / add / remove | `vm datastore ...` | Multiple storage locations |

---

## Notes on this installation

- `vm_dir="/var/vm"` is a plain directory on the root pool, **not** a dedicated ZFS dataset.
  `vm snapshot`, `vm clone`, `vm image`, and `vm migrate` all require
  `vm_dir="zfs:pool/dataset"` — they will return errors until reconfigured.
- To enable ZFS features: create a dataset (`zfs create pool0/vm`), set
  `vm_dir="zfs:pool0/vm"` in `/etc/rc.conf`, run `vm init`, and move existing VMs.
