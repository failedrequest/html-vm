"""Flask admin UI for vm-bhyve.

Run as root (the ``vm`` CLI needs root to manage bhyve guests):

    sudo .venv/bin/python run.py       # gevent server on :8088

Login uses the local OS accounts via PAM (see auth.py).
"""
import functools
import os
import re
import secrets
from datetime import timedelta

from flask import (Flask, request, render_template, redirect, url_for,
                   session, flash, abort, jsonify)

import vm
from auth import authenticate

app = Flask(__name__)

# SECRET_KEY must survive restarts so browser sessions (and WS auth cookies)
# remain valid across server restarts.  Generate once and persist to disk.
def _load_secret_key():
    env = os.environ.get("SECRET_KEY")
    if env:
        return env
    key_file = os.path.join(os.path.dirname(__file__), ".secret_key")
    try:
        with open(key_file) as _f:
            key = _f.read().strip()
        if key:
            return key
    except OSError:
        pass
    key = secrets.token_hex(32)
    try:
        with open(key_file, "w") as _f:
            _f.write(key)
        os.chmod(key_file, 0o600)
    except OSError:
        pass
    return key

app.config["SECRET_KEY"] = _load_secret_key()
app.config["VM_HOST"] = os.environ.get("VM_HOST", "0.0.0.0:8088")
app.config["VM_BIN"] = vm.VM_BIN
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)

# Max ISO upload size: 8 GiB
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024 * 1024


# --------------------------------------------------------------------------
# Auth + CSRF
# --------------------------------------------------------------------------
def login_required(view):
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def csrf_token():
    tok = session.get("_csrf")
    if not tok:
        tok = secrets.token_hex(16)
        session["_csrf"] = tok
    return tok


def check_csrf():
    expected = session.get("_csrf")
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRFToken")
    if not expected or not sent or not secrets.compare_digest(expected, sent or ""):
        abort(403)


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def _csrf_protect():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        if request.endpoint != "login":
            check_csrf()


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = (request.form.get("username") or "")[:64]
        pw = request.form.get("password") or ""
        if authenticate(user, pw):
            session.clear()
            session["user"] = user
            session.permanent = True
            nxt = request.args.get("next")
            if not nxt or not nxt.startswith("/"):
                nxt = url_for("index")
            return redirect(nxt)
        flash("Login failed: invalid local account or password", "error")
        return render_template("login.html"), 401
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.context_processor
def inject_user():
    return {"current_user": session.get("user")}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _vm_name_or_404(name):
    if not vm.valid_name(name):
        abort(404)
    return name


def _flash_result(label, result, _output=None):
    ok = result[1] == 0 if isinstance(result, tuple) else False
    msg = result[0] if isinstance(result, tuple) else result
    if isinstance(msg, bytes):
        msg = msg.decode("utf-8", "replace")
    if ok:
        flash(label + ": success", "ok")
    else:
        flash(label + " failed: " + (msg.strip().splitlines()[-1] if msg.strip() else str(msg)),
              "error")


def _switch_names():
    sw, _ = vm.list_switches()
    names = [s["name"] for s in sw]
    if "default" not in names:
        names.insert(0, "default")
    return names


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------
@app.route("/")
@login_required
def index():
    vms, err = vm.list_vms()
    running = sum(1 for v in vms if v["running"])
    host = vm.host_info()
    agg = vm.vm_aggregate_stats(vms)
    return render_template("dashboard.html", vms=vms, running=running,
                           vm_error=err, host=host, agg=agg)


@app.route("/api/vms")
@login_required
def api_vms():
    vms, err = vm.list_vms()
    agg = vm.vm_aggregate_stats(vms)
    return jsonify({"vms": vms, "error": err, "agg": agg})


@app.route("/api/host")
@login_required
def api_host():
    return jsonify(vm.host_info())


# --------------------------------------------------------------------------
# VM detail + actions
# --------------------------------------------------------------------------
@app.route("/vm/<name>")
@login_required
def vm_detail(name):
    name = _vm_name_or_404(name)
    vms, err = vm.list_vms()
    target = vm.vm_detail(name, vms)
    if target is None:
        flash("VM '{0}' not found".format(name), "error")
        return redirect(url_for("index"))
    isos, _ = vm.list_isos()
    return render_template("vm_detail.html", v=target, switches=_switch_names(),
                           loaders=vm.LOADERS, disk_types=vm.DISK_TYPES,
                           nic_types=vm.NIC_TYPES, isos=isos,
                           iso_dir=vm.ISO_DIR)


@app.route("/vm/<name>/<action>", methods=["POST"])
@login_required
def vm_action(name, action):
    name = _vm_name_or_404(name)
    if action == "start":
        _flash_result("Start", vm.start_vm(name))
    elif action == "stop":
        _flash_result("Stop", vm.stop_vm(name))
    elif action == "reboot":
        _flash_result("Reboot", vm.reboot_vm(name))
    elif action == "force-poweroff":
        _flash_result("Force power-off", vm.force_poweroff_vm(name))
    elif action == "force-reset":
        _flash_result("Force reset", vm.force_reset_vm(name))
    elif action == "destroy-vmm":
        _flash_result("Destroy VMM context", vm.destroy_vmm(name))
    elif action == "delete":
        out, rc = vm.delete_vm(name)
        if rc != 0 and "locked" in (out or "").lower():
            flash(
                "Delete failed: {0} — VM is locked (bhyve still holds the VMM context). "
                "Click 'Destroy VMM context' below, then delete again. "
                "Or run: sudo bhyvectl --destroy --vm={1}".format(
                    out.strip().splitlines()[-1] if out.strip() else "vm is locked",
                    name,
                ),
                "error",
            )
        else:
            _flash_result("Delete", (out, rc))
            if rc == 0:
                return redirect(url_for("index"))
        return redirect(url_for("vm_detail", name=name))
    elif action == "update":
        cpu = request.form.get("cpu") or None
        memory = request.form.get("memory") or None
        loader = request.form.get("loader") or None
        utctime = request.form.get("utctime") or None
        _flash_result("Update", vm.update_vm(
            name, cpu=cpu, memory=memory, loader=loader, utctime=utctime,
        ))
    else:
        abort(404)
    return redirect(url_for("vm_detail", name=name))


@app.route("/vm/<name>/rename", methods=["POST"])
@login_required
def vm_rename(name):
    name = _vm_name_or_404(name)
    new_name = (request.form.get("new_name") or "").strip()
    if not new_name:
        flash("New name is required", "error")
        return redirect(url_for("vm_detail", name=name))
    out, rc = vm.vm_rename(name, new_name)
    if rc == 0:
        flash("VM renamed to '{0}'".format(new_name), "ok")
        return redirect(url_for("vm_detail", name=new_name))
    flash("Rename failed: " + (out.strip().splitlines()[-1]
                               if out.strip() else str(out)), "error")
    return redirect(url_for("vm_detail", name=name))


@app.route("/vms/stopall", methods=["POST"])
@login_required
def vms_stopall():
    force = bool(request.form.get("force"))
    _flash_result("Stop all", vm.vm_stopall(force=force))
    return redirect(url_for("index"))


@app.route("/vms/startall", methods=["POST"])
@login_required
def vms_startall():
    _flash_result("Start all", vm.vm_startall())
    return redirect(url_for("index"))


# --------------------------------------------------------------------------
# Serial console via gotty
# --------------------------------------------------------------------------

import time as _time

@app.route("/vm/<name>/console")
@login_required
def vm_console(name):
    name = _vm_name_or_404(name)
    vms, _ = vm.list_vms()
    target = vm.vm_detail(name, vms)
    if target is None:
        flash("VM '{0}' not found".format(name), "error")
        return redirect(url_for("index"))
    if not target["running"]:
        flash("VM must be running to open a console", "error")
        return redirect(url_for("vm_detail", name=name))
    entry = vm.console_registry.get(name)
    return render_template("console.html", v=target, entry=entry)


@app.route("/vm/<name>/console/start", methods=["POST"])
@login_required
def console_start(name):
    name = _vm_name_or_404(name)
    vms, _ = vm.list_vms()
    target = vm.vm_detail(name, vms)
    if target is None or not target["running"]:
        flash("VM must be running to open a console", "error")
        return redirect(url_for("vm_detail", name=name))
    nmdm = target.get("nmdm_port") or "/dev/nmdm-{0}.1B".format(name)
    entry, err = vm.console_registry.start(name, nmdm)
    if err:
        flash("Console start failed: " + err, "error")
    else:
        _time.sleep(0.4)   # give gotty a moment to bind
        flash("Console started on port {0}".format(entry["port"]), "ok")
    return redirect(url_for("vm_console", name=name))


@app.route("/vm/<name>/console/stop", methods=["POST"])
@login_required
def console_stop(name):
    name = _vm_name_or_404(name)
    ok, msg = vm.console_registry.stop(name)
    if ok:
        flash(msg, "ok")
    else:
        flash(msg, "error")
    return redirect(url_for("vm_detail", name=name))


@app.route("/vm/<name>/console/kill", methods=["POST"])
@login_required
def console_kill(name):
    name = _vm_name_or_404(name)
    ok, msg = vm.console_registry.kill_stuck(name)
    if ok:
        flash("Force-killed: " + msg, "ok")
    else:
        flash(msg, "error")
    return redirect(url_for("vm_detail", name=name))


@app.route("/api/consoles")
@login_required
def api_consoles():
    return jsonify(vm.console_registry.list_all())


# --------------------------------------------------------------------------
# Create VM
# --------------------------------------------------------------------------
@app.route("/vm/create", methods=["GET", "POST"])
@login_required
def vm_create():
    isos, _ = vm.list_isos()
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        cpu = (request.form.get("cpu") or "").strip()
        memory = (request.form.get("memory") or "").strip()
        size = (request.form.get("size") or "20G").strip()
        switch = (request.form.get("switch") or "").strip() or None
        loader = (request.form.get("loader") or "bhyveload").strip()
        disk_type = (request.form.get("disk_type") or "virtio-blk").strip()
        nic_type = (request.form.get("nic_type") or "virtio-net").strip()
        iso = (request.form.get("iso") or "").strip() or None
        utctime = "no" if (request.form.get("utctime") == "no") else "yes"
        zfs_dataset = bool(request.form.get("zfs_dataset"))

        out, rc = vm.create_vm(
            name, cpu, memory, size,
            switch=switch, loader=loader,
            disk_type=disk_type, nic_type=nic_type,
            iso=iso, utctime=utctime,
            zfs_dataset=zfs_dataset,
        )
        if rc == 0:
            flash("VM '{0}' created".format(name), "ok")
            return redirect(url_for("vm_detail", name=name))
        flash("Create failed: " + (out.strip().splitlines()[-1]
                                   if out.strip() else str(out)), "error")
        return render_template("vm_create.html",
                               switches=_switch_names(), isos=isos,
                               os_profiles=vm.OS_PROFILES,
                               loaders=vm.LOADERS,
                               disk_types=vm.DISK_TYPES,
                               nic_types=vm.NIC_TYPES), 400
    return render_template("vm_create.html",
                           switches=_switch_names(), isos=isos,
                           os_profiles=vm.OS_PROFILES,
                           loaders=vm.LOADERS,
                           disk_types=vm.DISK_TYPES,
                           nic_types=vm.NIC_TYPES)


# --------------------------------------------------------------------------
# ISO management
# --------------------------------------------------------------------------
@app.route("/isos")
@login_required
def iso_list():
    isos, err = vm.list_isos()
    iso_details = []
    for name in isos:
        path = os.path.join(vm.ISO_DIR, name)
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        iso_details.append({"name": name, "size": size,
                             "size_human": vm.fmt_bytes(size)})
    return render_template("isos.html", isos=iso_details, iso_error=err)


@app.route("/isos/upload", methods=["POST"])
@login_required
def iso_upload():
    f = request.files.get("iso_file")
    if not f or not f.filename:
        flash("No file selected", "error")
        return redirect(url_for("iso_list"))
    filename = f.filename
    # Sanitise: allow alphanum, dash, underscore, dot, space
    if not re.match(r'^[\w\-. ]+\.(iso|img)$', filename, re.IGNORECASE):
        flash("Invalid filename — must end in .iso or .img", "error")
        return redirect(url_for("iso_list"))
    # Strip any path component
    filename = os.path.basename(filename)
    dest = os.path.join(vm.ISO_DIR, filename)
    os.makedirs(vm.ISO_DIR, exist_ok=True)
    f.save(dest)
    flash("Uploaded '{0}' ({1})".format(filename, vm.fmt_bytes(os.path.getsize(dest))), "ok")
    return redirect(url_for("iso_list"))


@app.route("/isos/<filename>/delete", methods=["POST"])
@login_required
def iso_delete(filename):
    msg, rc = vm.delete_iso(filename)
    if rc == 0:
        flash("Deleted '{0}'".format(filename), "ok")
    else:
        flash("Delete failed: " + msg, "error")
    return redirect(url_for("iso_list"))


@app.route("/isos/fetch", methods=["POST"])
@login_required
def iso_fetch():
    url = (request.form.get("url") or "").strip()
    if not url:
        flash("URL is required", "error")
        return redirect(url_for("iso_list"))
    out, rc = vm.iso_fetch(url)
    if rc == 0:
        flash("ISO fetched from '{0}'".format(url), "ok")
    else:
        flash("Fetch failed: " + (out.strip().splitlines()[-1]
                                  if out.strip() else str(out)), "error")
    return redirect(url_for("iso_list"))


# --------------------------------------------------------------------------
# Networks (vm-bhyve switches)
# --------------------------------------------------------------------------
@app.route("/networks", methods=["GET", "POST"])
@login_required
def networks():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        private = bool(request.form.get("private"))
        out, rc = vm.create_switch(name, private=private)
        if rc == 0:
            flash("Switch '{0}' created".format(name), "ok")
        else:
            flash("Create switch failed: " + (out.strip().splitlines()[-1]
                                              if out.strip() else str(out)), "error")
        return redirect(url_for("networks"))
    sw, err = vm.list_switches()
    tunnels = vm.list_tunnel_interfaces()
    host_ifaces = vm.list_host_interfaces()
    return render_template("networks.html", switches=sw, switch_error=err,
                           tunnels=tunnels, host_ifaces=host_ifaces)


@app.route("/network/<name>/delete", methods=["POST"])
@login_required
def network_delete(name):
    name = (request.form.get("name") or name).strip()
    out, rc = vm.delete_switch(name)
    if rc == 0:
        flash("Switch '{0}' removed".format(name), "ok")
    else:
        flash("Delete switch failed: " + (out.strip().splitlines()[-1]
                                          if out.strip() else str(out)), "error")
    return redirect(url_for("networks"))


# --------------------------------------------------------------------------
# Tunnel management — VXLAN, GRE, IPsec
# --------------------------------------------------------------------------

@app.route("/network/vxlan/tunnel/create", methods=["POST"])
@login_required
def vxlan_tunnel_create():
    name      = (request.form.get("name") or "").strip()
    vni       = (request.form.get("vni") or "").strip()
    local_ip  = (request.form.get("local_ip") or "").strip()
    remote_ip = (request.form.get("remote_ip") or "").strip()
    port      = (request.form.get("port") or "4789").strip() or "4789"
    if not all([name, vni, local_ip, remote_ip]):
        flash("VXLAN tunnel: name, VNI, local IP, and remote IP are required", "error")
        return redirect(url_for("networks"))
    out, rc = vm.create_vxlan_tunnel(name, vni, local_ip, remote_ip, port)
    if rc == 0:
        flash("VXLAN tunnel '{0}' (VNI {1} → {2}) created".format(name, vni, remote_ip), "ok")
    else:
        flash("VXLAN tunnel create failed: " + (out.strip().splitlines()[-1]
                                                if out.strip() else str(out)), "error")
    return redirect(url_for("networks"))


@app.route("/network/vxlan/tunnel/<name>/delete", methods=["POST"])
@login_required
def vxlan_tunnel_delete(name):
    out, rc = vm.destroy_vxlan_tunnel(name)
    if rc == 0:
        flash("VXLAN tunnel '{0}' destroyed".format(name), "ok")
    else:
        flash("VXLAN tunnel delete failed: " + (out.strip().splitlines()[-1]
                                                if out.strip() else str(out)), "error")
    return redirect(url_for("networks"))


@app.route("/network/vxlan/create", methods=["POST"])
@login_required
def vxlan_create():
    name   = (request.form.get("name") or "").strip()
    vni    = (request.form.get("vni") or "").strip()
    iface  = (request.form.get("iface") or "").strip()
    if not name or not vni or not iface:
        flash("VXLAN: name, VNI, and interface are required", "error")
        return redirect(url_for("networks"))
    out, rc = vm.create_vxlan_switch(name, vni, iface)
    if rc == 0:
        flash("VXLAN switch '{0}' (VNI {1}) created".format(name, vni), "ok")
    else:
        flash("VXLAN create failed: " + (out.strip().splitlines()[-1]
                                         if out.strip() else str(out)), "error")
    return redirect(url_for("networks"))


@app.route("/network/gre/create", methods=["POST"])
@login_required
def gre_create():
    name         = (request.form.get("name") or "").strip()
    local_ip     = (request.form.get("local_ip") or "").strip()
    remote_ip    = (request.form.get("remote_ip") or "").strip()
    inner_local  = (request.form.get("inner_local") or "").strip()
    inner_remote = (request.form.get("inner_remote") or "").strip()
    if not all([name, local_ip, remote_ip, inner_local, inner_remote]):
        flash("GRE: all fields are required", "error")
        return redirect(url_for("networks"))
    out, rc = vm.create_gre_tunnel(name, local_ip, remote_ip, inner_local, inner_remote)
    if rc == 0:
        flash("GRE tunnel '{0}' created".format(name), "ok")
    else:
        flash("GRE create failed: " + (out.strip().splitlines()[-1]
                                       if out.strip() else str(out)), "error")
    return redirect(url_for("networks"))


@app.route("/network/gre/<name>/delete", methods=["POST"])
@login_required
def gre_delete(name):
    out, rc = vm.destroy_gre_tunnel(name)
    if rc == 0:
        flash("GRE tunnel '{0}' destroyed".format(name), "ok")
    else:
        flash("GRE delete failed: " + (out.strip().splitlines()[-1]
                                       if out.strip() else str(out)), "error")
    return redirect(url_for("networks"))


@app.route("/network/ipsec/create", methods=["POST"])
@login_required
def ipsec_create():
    name         = (request.form.get("name") or "").strip()
    local_ip     = (request.form.get("local_ip") or "").strip()
    remote_ip    = (request.form.get("remote_ip") or "").strip()
    inner_local  = (request.form.get("inner_local") or "").strip()
    inner_remote = (request.form.get("inner_remote") or "").strip()
    reqid        = (request.form.get("reqid") or "").strip() or None
    if not all([name, local_ip, remote_ip, inner_local, inner_remote]):
        flash("IPsec: all fields are required", "error")
        return redirect(url_for("networks"))
    out, rc = vm.create_ipsec_tunnel(name, local_ip, remote_ip, inner_local, inner_remote, reqid)
    if rc == 0:
        flash("IPsec tunnel '{0}' created".format(name), "ok")
    else:
        flash("IPsec create failed: " + (out.strip().splitlines()[-1]
                                         if out.strip() else str(out)), "error")
    return redirect(url_for("networks"))


@app.route("/network/ipsec/<name>/delete", methods=["POST"])
@login_required
def ipsec_delete(name):
    out, rc = vm.destroy_ipsec_tunnel(name)
    if rc == 0:
        flash("IPsec tunnel '{0}' destroyed".format(name), "ok")
    else:
        flash("IPsec delete failed: " + (out.strip().splitlines()[-1]
                                         if out.strip() else str(out)), "error")
    return redirect(url_for("networks"))


@app.route("/network/bridge/<bridge>/addm", methods=["POST"])
@login_required
def bridge_addm(bridge):
    iface = (request.form.get("iface") or "").strip()
    if not iface:
        flash("Bridge addm: interface name required", "error")
        return redirect(url_for("networks"))
    out, rc = vm.bridge_addm(bridge, iface)
    if rc == 0:
        flash(out, "ok")
    else:
        flash(out, "error")
    return redirect(url_for("networks"))


@app.route("/network/bridge/<bridge>/deletem", methods=["POST"])
@login_required
def bridge_deletem(bridge):
    iface = (request.form.get("iface") or "").strip()
    if not iface:
        flash("Bridge deletem: interface name required", "error")
        return redirect(url_for("networks"))
    out, rc = vm.bridge_deletem(bridge, iface)
    if rc == 0:
        flash(out, "ok")
    else:
        flash(out, "error")
    return redirect(url_for("networks"))


# --------------------------------------------------------------------------
# VM disk management
# --------------------------------------------------------------------------

@app.route("/vm/<name>/disk/add", methods=["POST"])
@login_required
def vm_disk_add(name):
    name = _vm_name_or_404(name)
    disk_type = (request.form.get("disk_type") or "virtio-blk").strip()
    size = (request.form.get("size") or "").strip()
    dev = (request.form.get("dev") or "file").strip()
    if not size:
        flash("Size is required (e.g. 10G)", "error")
        return redirect(url_for("vm_detail", name=name))
    _flash_result("Add disk", vm.add_disk_conf(name, disk_type, size, dev))
    return redirect(url_for("vm_detail", name=name))


@app.route("/vm/<name>/disk/<int:idx>/remove", methods=["POST"])
@login_required
def vm_disk_remove(name, idx):
    name = _vm_name_or_404(name)
    _flash_result("Remove disk", vm.remove_disk_conf(name, idx))
    return redirect(url_for("vm_detail", name=name))


@app.route("/vm/<name>/disk/<int:idx>/update", methods=["POST"])
@login_required
def vm_disk_update(name, idx):
    name = _vm_name_or_404(name)
    disk_type = request.form.get("disk_type") or None
    disk_name = request.form.get("disk_name") or None
    dev = request.form.get("dev") or None
    _flash_result("Update disk", vm.update_disk_conf(name, idx,
                                                     disk_type=disk_type,
                                                     disk_name=disk_name,
                                                     dev=dev))
    return redirect(url_for("vm_detail", name=name))


# --------------------------------------------------------------------------
# VM network management
# --------------------------------------------------------------------------

@app.route("/vm/<name>/network/add", methods=["POST"])
@login_required
def vm_network_add(name):
    name = _vm_name_or_404(name)
    switch = (request.form.get("switch") or "").strip()
    nic_type = (request.form.get("nic_type") or "").strip() or None
    if not switch:
        flash("Switch name is required", "error")
        return redirect(url_for("vm_detail", name=name))
    _flash_result("Add network", vm.add_network_conf(name, switch, nic_type))
    return redirect(url_for("vm_detail", name=name))


@app.route("/vm/<name>/network/<int:idx>/remove", methods=["POST"])
@login_required
def vm_network_remove(name, idx):
    name = _vm_name_or_404(name)
    _flash_result("Remove network", vm.remove_network_conf(name, idx))
    return redirect(url_for("vm_detail", name=name))


@app.route("/vm/<name>/network/<int:idx>/update", methods=["POST"])
@login_required
def vm_network_update(name, idx):
    name = _vm_name_or_404(name)
    switch = request.form.get("switch") or None
    nic_type = request.form.get("nic_type") or None
    mac = request.form.get("mac")          # empty string = clear mac
    if mac is not None:
        mac = mac.strip()
    _flash_result("Update network", vm.update_network_conf(name, idx,
                                                           switch=switch,
                                                           nic_type=nic_type,
                                                           mac=mac if mac != "" else None))
    return redirect(url_for("vm_detail", name=name))


@app.route("/api/tunnels")
@login_required
def api_tunnels():
    return jsonify(vm.list_tunnel_interfaces())


# --------------------------------------------------------------------------
# Maintenance
# --------------------------------------------------------------------------
@app.route("/maintenance")
@login_required
def maintenance():
    return render_template("maintenance.html")


@app.route("/maintenance/reboot", methods=["POST"])
@login_required
def maintenance_reboot():
    import subprocess
    flash("Host reboot initiated — server will restart in ~1 minute.", "ok")
    subprocess.Popen(["sudo", "shutdown", "-r", "+1",
                      "html-vm: reboot requested via admin UI"])
    return redirect(url_for("maintenance"))


# --------------------------------------------------------------------------
# Storage — ZFS snapshots, clone, images
# --------------------------------------------------------------------------

@app.route("/storage")
@login_required
def storage():
    vms, _ = vm.list_vms()
    vm_names = [v["name"] for v in vms]
    images, img_err = vm.vm_image_list()
    # Annotate each VM with whether it has a dedicated ZFS dataset
    vm_zfs = {n: vm.zfs_vm_dataset_exists(n) for n in vm_names}
    return render_template("storage.html", vm_names=vm_names,
                           images=images, img_err=img_err,
                           vm_zfs=vm_zfs)


@app.route("/storage/<name>/create-dataset", methods=["POST"])
@login_required
def vm_create_dataset(name):
    name = _vm_name_or_404(name)
    ds, err = vm.create_vm_zfs_dataset(name)
    if err:
        flash("ZFS dataset creation failed: " + err, "error")
    else:
        flash("ZFS dataset {0} created for VM '{1}'.".format(ds, name), "ok")
    return redirect(url_for("storage"))


@app.route("/storage/<name>/snapshots")
@login_required
def vm_snapshots(name):
    name = _vm_name_or_404(name)
    snaps, err = vm.vm_snapshots(name)
    vms, _ = vm.list_vms()
    target = vm.vm_detail(name, vms)
    return render_template("vm_snapshots.html", vmname=name, v=target,
                           snaps=snaps, err=err)


@app.route("/storage/<name>/snapshot", methods=["POST"])
@login_required
def vm_snapshot_create(name):
    name = _vm_name_or_404(name)
    snap_name = (request.form.get("snap_name") or "").strip() or None
    _flash_result("Snapshot", vm.vm_snapshot(name, snap_name))
    return redirect(url_for("vm_snapshots", name=name))


@app.route("/storage/<name>/snapshot/<snap>/delete", methods=["POST"])
@login_required
def vm_snapshot_delete(name, snap):
    name = _vm_name_or_404(name)
    _flash_result("Delete snapshot", vm.vm_delete_snapshot(name, snap))
    return redirect(url_for("vm_snapshots", name=name))


@app.route("/storage/<name>/rollback", methods=["POST"])
@login_required
def vm_rollback(name):
    name = _vm_name_or_404(name)
    snap = (request.form.get("snap_name") or "").strip()
    force = bool(request.form.get("force"))
    if not snap:
        flash("Snapshot name required", "error")
        return redirect(url_for("vm_snapshots", name=name))
    _flash_result("Rollback", vm.vm_rollback(name, snap, force=force))
    return redirect(url_for("vm_snapshots", name=name))


@app.route("/storage/<name>/clone", methods=["POST"])
@login_required
def vm_clone(name):
    name = _vm_name_or_404(name)
    new_name = (request.form.get("new_name") or "").strip()
    snap = (request.form.get("snap_name") or "").strip() or None
    if not new_name:
        flash("New VM name required", "error")
        return redirect(url_for("vm_snapshots", name=name))
    _flash_result("Clone", vm.vm_clone(name, new_name, snap_name=snap))
    return redirect(url_for("storage"))


@app.route("/storage/image/create", methods=["POST"])
@login_required
def image_create():
    name = (request.form.get("name") or "").strip()
    description = (request.form.get("description") or "").strip()
    if not name:
        flash("VM name required", "error")
        return redirect(url_for("storage"))
    _flash_result("Create image", vm.vm_image_create(name, description))
    return redirect(url_for("storage"))


@app.route("/storage/image/<uuid>/provision", methods=["POST"])
@login_required
def image_provision(uuid):
    new_name = (request.form.get("new_name") or "").strip()
    if not new_name:
        flash("New VM name required", "error")
        return redirect(url_for("storage"))
    _flash_result("Provision image", vm.vm_image_provision(uuid, new_name))
    return redirect(url_for("storage"))


@app.route("/storage/image/<uuid>/delete", methods=["POST"])
@login_required
def image_delete(uuid):
    _flash_result("Delete image", vm.vm_image_destroy(uuid))
    return redirect(url_for("storage"))


# --------------------------------------------------------------------------
# Migrate
# --------------------------------------------------------------------------

@app.route("/migrate")
@login_required
def migrate():
    vms, _ = vm.list_vms()
    vm_names = [v["name"] for v in vms]
    return render_template("migrate.html", vm_names=vm_names)


@app.route("/migrate/start", methods=["POST"])
@login_required
def migrate_start():
    name = (request.form.get("name") or "").strip()
    host = (request.form.get("host") or "").strip()
    remote_name = (request.form.get("remote_name") or "").strip() or None
    start_remote = bool(request.form.get("start_remote"))
    triple = bool(request.form.get("triple"))
    destroy_local = bool(request.form.get("destroy_local"))
    if not name or not host:
        flash("VM name and destination host are required", "error")
        return redirect(url_for("migrate"))
    out, rc = vm.vm_migrate(name, host, remote_name=remote_name,
                            start_remote=start_remote, triple=triple,
                            destroy_local=destroy_local)
    if rc == 0:
        flash("Migration of '{0}' to {1} complete. {2}".format(
              name, host, out.strip()), "ok")
    else:
        flash("Migration failed: " + (out.strip().splitlines()[-1]
              if out.strip() else str(out)), "error")
    return redirect(url_for("migrate"))


@app.errorhandler(403)
def forbidden(e):
    return render_template("error.html", code=403,
                           message="Forbidden (CSRF token missing or invalid)"), 403


@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404, message="Not found"), 404


@app.errorhandler(413)
def too_large(e):
    return render_template("error.html", code=413,
                           message="File too large (max 8 GiB)"), 413


if __name__ == "__main__":
    host, _, port = app.config["VM_HOST"].partition(":")
    app.run(host=host, port=int(port), debug=True)
