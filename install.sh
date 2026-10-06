#!/bin/sh
# install.sh — html-vm installer for FreeBSD
#
# Installs all dependencies via pkg, creates a Python venv at
# /usr/local/html-vm/.venv, symlinks pkg-managed packages in, installs the
# rc.d service, and enables it in /etc/rc.conf.
#
# Safe to re-run: every step is idempotent.
# Does NOT touch vm-bhyve configuration if already installed and configured.
#
# Usage (from the repo root):
#   sudo sh install.sh

set -e

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
APP_DIR="/usr/local/html-vm"
VENV="${APP_DIR}/.venv"
PYTHON="/usr/local/bin/python3.12"
SYS_SITE="/usr/local/lib/python3.12/site-packages"
RCCONF="/etc/rc.conf"
SERVICE_DST="/usr/local/etc/rc.d/html_vm"

# Source of the repo (directory containing this script)
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

# ---------------------------------------------------------------------------
# Must run as root
# ---------------------------------------------------------------------------
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: install.sh must be run as root (sudo sh install.sh)" >&2
    exit 1
fi

echo "==> html-vm installer"
echo "    repo        : ${REPO_DIR}"
echo "    install dir : ${APP_DIR}"
echo "    venv        : ${VENV}"
echo ""

# ---------------------------------------------------------------------------
# Step 1 — pkg packages
# ---------------------------------------------------------------------------
echo "==> [1/6] Installing pkg packages ..."

# Detect vm-bhyve before installing so we know whether it was pre-configured
VMBHYVE_PREINSTALLED=0
if pkg info -q vm-bhyve 2>/dev/null; then
    VMBHYVE_PREINSTALLED=1
fi

pkg install -y \
    python312 \
    py312-gevent \
    py312-gevent-websocket \
    py312-flask \
    py312-blinker \
    py312-click \
    py312-greenlet \
    py312-h11 \
    py312-itsdangerous \
    py312-Jinja2 \
    py312-markupsafe \
    py312-pyflakes \
    py312-python-pam \
    py312-simple-websocket \
    py312-werkzeug \
    py312-wsproto \
    vm-bhyve \
    gotty

echo "    pkg packages OK"

# ---------------------------------------------------------------------------
# Step 2 — vm-bhyve first-run guidance (only for fresh installs)
# ---------------------------------------------------------------------------
echo "==> [2/6] Checking vm-bhyve configuration ..."

if [ "${VMBHYVE_PREINSTALLED}" -eq 0 ]; then
    echo ""
    echo "    vm-bhyve was just installed. You must configure it before"
    echo "    starting html-vm. Minimum required steps:"
    echo ""
    echo "    1. Add to ${RCCONF}:"
    echo "         vm_enable=\"YES\""
    echo "         vm_dir=\"/var/vm\""
    echo "         # Use vm_dir=\"zfs:pool/dataset\" to enable ZFS snapshots/clone"
    echo ""
    echo "    2. Initialise the datastore:"
    echo "         vm init"
    echo ""
    echo "    3. Create at least one network switch, e.g.:"
    echo "         vm switch create public"
    echo "         vm switch add public em0"
    echo ""
else
    echo "    vm-bhyve already installed — configuration untouched"
fi

# ---------------------------------------------------------------------------
# Step 3 — Copy app to /usr/local/html-vm
# ---------------------------------------------------------------------------
echo "==> [3/6] Installing app to ${APP_DIR} ..."

mkdir -p "${APP_DIR}"

# rsync if available, otherwise cp -R; exclude venv and cache
if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
        --exclude='.venv' \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        --exclude='.git' \
        --exclude='.secret_key' \
        "${REPO_DIR}/" "${APP_DIR}/"
else
    # Portable fallback
    find "${REPO_DIR}" \
        -not -path '*/.git/*' \
        -not -path '*/.venv/*' \
        -not -path '*/__pycache__/*' \
        -not -name '*.pyc' \
        -not -name '.secret_key' | \
    while IFS= read -r src; do
        rel="${src#${REPO_DIR}/}"
        dst="${APP_DIR}/${rel}"
        if [ -d "${src}" ]; then
            mkdir -p "${dst}"
        else
            cp -p "${src}" "${dst}"
        fi
    done
fi

echo "    app installed to ${APP_DIR}"

# ---------------------------------------------------------------------------
# Step 4 — Python venv
# ---------------------------------------------------------------------------
echo "==> [4/6] Creating Python venv ..."

if [ ! -x "${VENV}/bin/python" ]; then
    "${PYTHON}" -m venv --system-site-packages "${VENV}"
    echo "    venv created (--system-site-packages)"
else
    echo "    venv already exists"
fi

# ---------------------------------------------------------------------------
# Step 5 — pip install only what pkg cannot provide
# ---------------------------------------------------------------------------
echo "==> [5/6] Installing pip-only packages from requirements.txt ..."

"${VENV}/bin/pip" install --upgrade pip --quiet
"${VENV}/bin/pip" install \
    --requirement "${APP_DIR}/requirements.txt" \
    --quiet

echo "    pip packages OK"

# ---------------------------------------------------------------------------
# Step 6 — rc.d service
# ---------------------------------------------------------------------------
echo "==> [6/6] Installing rc.d service ..."

sed "s|/home/nonesuch/html-vm|${APP_DIR}|g" \
    "${APP_DIR}/etc/rc.d/html_vm" > "${SERVICE_DST}"
chmod 555 "${SERVICE_DST}"
echo "    installed ${SERVICE_DST}"

if ! grep -q "html_vm_enable" "${RCCONF}" 2>/dev/null; then
    printf '\n# html-vm web admin\nhtml_vm_enable="YES"\nhtml_vm_dir="%s"\n' \
        "${APP_DIR}" >> "${RCCONF}"
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
echo "    Start : service html_vm start"
echo "    Logs  : tail -f /var/log/html_vm.log"
echo "    UI    : http://$(hostname):8088/"
echo ""
if [ "${VMBHYVE_PREINSTALLED}" -eq 0 ]; then
    echo "    REMINDER: complete vm-bhyve setup (step 2 above) before starting."
    echo ""
fi
