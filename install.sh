#!/bin/sh
# install.sh — html-vm installer for FreeBSD
#
# Installs system pkg dependencies, creates a Python venv, installs pip
# packages from requirements.txt, symlinks pkg-managed geventwebsocket into
# the venv, installs the rc.d service, and enables it in /etc/rc.conf.
#
# Safe to re-run: skips anything already done.
# Does NOT touch vm-bhyve configuration if vm-bhyve is already installed.
#
# Usage:
#   sudo sh install.sh [--install-dir /path/to/html-vm]

set -e

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
RCCONF="/etc/rc.conf"
SERVICE_SRC="${INSTALL_DIR}/etc/rc.d/html_vm"
SERVICE_DST="/usr/local/etc/rc.d/html_vm"
VENV="${INSTALL_DIR}/.venv"
PYTHON="/usr/local/bin/python3.12"
SYS_SITE="/usr/local/lib/python3.12/site-packages"

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [ $# -gt 0 ]; do
    case "$1" in
        --install-dir) INSTALL_DIR="$2"; shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Must run as root
# ---------------------------------------------------------------------------
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: install.sh must be run as root (sudo sh install.sh)" >&2
    exit 1
fi

echo "==> html-vm installer"
echo "    install dir : ${INSTALL_DIR}"
echo "    venv        : ${VENV}"
echo ""

# ---------------------------------------------------------------------------
# Step 1 — pkg packages
# ---------------------------------------------------------------------------
echo "==> [1/6] Installing pkg packages ..."

# Core runtime
PKG_RUNTIME="python312 py312-gevent py312-gevent-websocket vm-bhyve gotty"

# vm-bhyve may already be installed and configured — detect before installing
VMBHYVE_ALREADY=0
if pkg info -q vm-bhyve 2>/dev/null; then
    VMBHYVE_ALREADY=1
    echo "    vm-bhyve is already installed — skipping vm-bhyve first-run init"
fi

pkg install -y ${PKG_RUNTIME}

echo "    pkg packages OK"

# ---------------------------------------------------------------------------
# Step 2 — vm-bhyve first-run init (only if not already configured)
# ---------------------------------------------------------------------------
echo "==> [2/6] Checking vm-bhyve configuration ..."

if [ "${VMBHYVE_ALREADY}" -eq 0 ]; then
    # vm-bhyve was just installed; ask the user to configure it.
    echo ""
    echo "    vm-bhyve was just installed."
    echo "    You must configure it before starting html-vm:"
    echo ""
    echo "    1. Add to /etc/rc.conf:"
    echo "         vm_enable=\"YES\""
    echo "         vm_dir=\"/var/vm\"          # or zfs:pool/dataset for ZFS features"
    echo ""
    echo "    2. Initialise the datastore:"
    echo "         vm init"
    echo ""
    echo "    3. Create at least one switch, e.g.:"
    echo "         vm switch create public"
    echo "         vm switch add public em0"
    echo ""
    echo "    Re-run this installer after completing those steps if you want"
    echo "    the rc.d service to start automatically."
    echo ""
else
    echo "    vm-bhyve already configured — no changes made"
fi

# ---------------------------------------------------------------------------
# Step 3 — Python venv
# ---------------------------------------------------------------------------
echo "==> [3/6] Creating Python venv at ${VENV} ..."

if [ ! -x "${VENV}/bin/python" ]; then
    "${PYTHON}" -m venv "${VENV}"
    echo "    venv created"
else
    echo "    venv already exists, skipping creation"
fi

# ---------------------------------------------------------------------------
# Step 4 — pip install from requirements.txt
# ---------------------------------------------------------------------------
echo "==> [4/6] Installing pip packages from requirements.txt ..."

# gevent is provided by pkg; install everything else via pip.
# Pass --no-deps for gevent so pip does not try to rebuild it.
"${VENV}/bin/pip" install --upgrade pip --quiet
"${VENV}/bin/pip" install \
    --requirement "${INSTALL_DIR}/requirements.txt" \
    --ignore-requires-python \
    --quiet \
    2>&1 | grep -v "^WARNING.*gevent_websocket"

echo "    pip packages OK"

# ---------------------------------------------------------------------------
# Step 5 — symlink pkg-managed geventwebsocket into the venv
# ---------------------------------------------------------------------------
echo "==> [5/6] Symlinking geventwebsocket and gevent into venv ..."

VENV_SITE="${VENV}/lib/python3.12/site-packages"

# geventwebsocket
if [ ! -e "${VENV_SITE}/geventwebsocket" ]; then
    ln -s "${SYS_SITE}/geventwebsocket" "${VENV_SITE}/geventwebsocket"
    echo "    symlinked geventwebsocket"
else
    echo "    geventwebsocket already linked"
fi

# gevent egg-info (needed for pkg-installed gevent to be visible inside venv)
GEVENT_EGG=$(ls -d "${SYS_SITE}"/gevent_websocket-*.egg-info 2>/dev/null | head -1)
if [ -n "${GEVENT_EGG}" ]; then
    DEST="${VENV_SITE}/$(basename "${GEVENT_EGG}")"
    if [ ! -e "${DEST}" ]; then
        ln -s "${GEVENT_EGG}" "${DEST}"
        echo "    symlinked geventwebsocket egg-info"
    fi
fi

# gevent itself (pkg version may be newer than pip; ensure venv sees pkg copy)
if [ -d "${SYS_SITE}/gevent" ] && [ ! -L "${VENV_SITE}/gevent" ]; then
    # Only re-link if the venv pip copy is a different path
    VENV_GEVENT=$(readlink "${VENV_SITE}/gevent" 2>/dev/null || echo "")
    if [ "${VENV_GEVENT}" != "${SYS_SITE}/gevent" ]; then
        echo "    gevent already managed by pip in venv — leaving as-is"
    fi
fi

echo "    symlinks OK"

# ---------------------------------------------------------------------------
# Step 6 — rc.d service
# ---------------------------------------------------------------------------
echo "==> [6/6] Installing rc.d service ..."

# Update the default install dir in the service script to match this install
sed "s|/home/nonesuch/html-vm|${INSTALL_DIR}|g" \
    "${SERVICE_SRC}" > "${SERVICE_DST}"
chmod 555 "${SERVICE_DST}"
echo "    installed ${SERVICE_DST}"

# Add html_vm_enable to /etc/rc.conf only if not already present
if ! grep -q "html_vm_enable" "${RCCONF}" 2>/dev/null; then
    printf '\n# html-vm web admin\nhtml_vm_enable="YES"\nhtml_vm_dir="%s"\n' \
        "${INSTALL_DIR}" >> "${RCCONF}"
    echo "    added html_vm_enable to ${RCCONF}"
else
    echo "    html_vm_enable already in ${RCCONF} — not modified"
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo "==> Installation complete."
echo ""
echo "    Start now  : service html_vm start"
echo "    Logs       : tail -f /var/log/html_vm.log"
echo "    Web UI     : http://$(hostname):8088/"
echo ""
if [ "${VMBHYVE_ALREADY}" -eq 0 ]; then
    echo "    REMINDER: configure vm-bhyve (see step 2 above) before starting."
    echo ""
fi
