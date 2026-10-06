"""Local-account (PAM) authentication for the Flask admin UI.

"Local accounts" means the operating-system users in /etc/passwd, validated
through PAM.  No application-specific user database is used: whichever account
can already ``ssh`` in here can administer vm-bhyve through this UI.
"""
import os

import pam

# FreeBSD's generic PAM service; works for ordinary local accounts.
PAM_SERVICE = os.environ.get("PAM_SERVICE", "system")


def authenticate(username, password):
    """Return True if (username, password) validates against the local OS."""
    if not username or not password:
        return False
    if len(username) > 64 or len(password) > 1024:
        return False
    try:
        return bool(pam.authenticate(username, password, service=PAM_SERVICE))
    except Exception:
        return False
