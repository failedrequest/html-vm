"""Flask route tests for app.py.

All external I/O (vm CLI, PAM, filesystem) is mocked so no running hypervisor,
real PAM credentials, or ISO files are required.

Run:
    .venv/bin/python -m unittest tests/test_app.py -v
"""
import io
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import app as _app_module
from app import app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _csrf(client):
    """GET /login to seed a CSRF token and return it."""
    resp = client.get("/login")
    assert resp.status_code == 200
    with client.session_transaction() as sess:
        return sess.get("_csrf", "")


def _login(client, csrf=None):
    """POST valid credentials with a CSRF token and return the response."""
    if csrf is None:
        csrf = _csrf(client)
    with patch("app.authenticate", return_value=True):
        return client.post("/login", data={"username": "testuser",
                                           "password": "secret",
                                           "csrf_token": csrf},
                           follow_redirects=True)


def _make_vm(name="testvm", running=False):
    return {
        "name": name, "state": "running" if running else "stopped",
        "running": running, "pid": 1234 if running else None,
        "cpu": 2, "memory": "1G", "memory_bytes": 1 << 30,
        "datastore": "default", "loader": "bhyveload",
        "disks": [{"size_human": "10G", "size_bytes": 10 * 1 << 30,
                   "used_bytes": 1024, "system_path": "/var/vm/testvm/disk0.img"}],
        "networks": [{"emulation": "virtio-net", "virtual_switch": "default",
                      "fixed_mac_address": "00:11:22:33:44:55",
                      "net_in_bytes": 0, "net_out_bytes": 0}],
        "uptime": None, "uptime_seconds": None, "pcpu": None, "rss_human": None,
        "loading": False, "uuid": None, "memory_resident": None,
        "nmdm_port": "/dev/nmdm-{0}.1B".format(name),
    }


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------

class AuthTests(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        self.client = app.test_client()

    def test_login_page_get(self):
        resp = self.client.get("/login")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"login", resp.data.lower())

    def test_redirect_to_login_when_unauthenticated(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])

    def test_login_success_redirects_to_index(self):
        csrf = _csrf(self.client)
        with patch("app.authenticate", return_value=True):
            resp = self.client.post("/login",
                                    data={"username": "alice", "password": "pw",
                                          "csrf_token": csrf},
                                    follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/", resp.headers["Location"])

    def test_login_failure_returns_401(self):
        csrf = _csrf(self.client)
        with patch("app.authenticate", return_value=False):
            resp = self.client.post("/login",
                                    data={"username": "alice", "password": "bad",
                                          "csrf_token": csrf})
        self.assertEqual(resp.status_code, 401)

    def test_logout_clears_session(self):
        _login(self.client)
        with self.client.session_transaction() as sess:
            self.assertIn("user", sess)
            csrf = sess.get("_csrf", "")
        resp = self.client.post("/logout",
                                data={"csrf_token": csrf},
                                follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])
        with self.client.session_transaction() as sess:
            self.assertNotIn("user", sess)

    def test_login_redirects_next_param(self):
        csrf = _csrf(self.client)
        with patch("app.authenticate", return_value=True):
            resp = self.client.post("/login?next=/networks",
                                    data={"username": "alice", "password": "pw",
                                          "csrf_token": csrf},
                                    follow_redirects=False)
        self.assertIn("/networks", resp.headers["Location"])

    def test_login_ignores_external_next(self):
        """next= pointing off-site must be ignored."""
        csrf = _csrf(self.client)
        with patch("app.authenticate", return_value=True):
            resp = self.client.post("/login?next=http://evil.com",
                                    data={"username": "alice", "password": "pw",
                                          "csrf_token": csrf},
                                    follow_redirects=False)
        loc = resp.headers["Location"]
        self.assertNotIn("evil.com", loc)


# ---------------------------------------------------------------------------
# CSRF tests
# ---------------------------------------------------------------------------

class CsrfTests(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        _login(self.client)

    def test_post_without_csrf_returns_403(self):
        resp = self.client.post("/networks", data={"name": "pub"})
        self.assertEqual(resp.status_code, 403)

    def test_post_wrong_csrf_returns_403(self):
        resp = self.client.post("/networks",
                                data={"name": "pub", "csrf_token": "wrong"})
        self.assertEqual(resp.status_code, 403)

    def test_post_correct_csrf_passes(self):
        csrf = _csrf(self.client)
        with patch("app.vm.create_switch", return_value=("", 0)), \
             patch("app.vm.list_switches", return_value=([], None)):
            resp = self.client.post("/networks",
                                    data={"name": "pub", "csrf_token": csrf},
                                    follow_redirects=False)
        self.assertNotEqual(resp.status_code, 403)


# ---------------------------------------------------------------------------
# Dashboard / API tests
# ---------------------------------------------------------------------------

class DashboardTests(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        _login(self.client)
        self._vms = [_make_vm("vm1"), _make_vm("vm2", running=True)]
        self._host = {
            "cpu_count": 4, "mem_total_bytes": 8 << 30, "mem_total_human": "8.00 GiB",
            "datastore_path": "/var/vm",
            "datastore_total_bytes": 100 << 30, "datastore_avail_bytes": 60 << 30,
            "datastore_used_bytes": 40 << 30,
            "datastore_total_human": "100.00 GiB",
            "datastore_avail_human": "60.00 GiB",
            "datastore_used_human": "40.00 GiB",
        }

    def test_dashboard_200(self):
        with patch("app.vm.list_vms", return_value=(self._vms, None)), \
             patch("app.vm.host_info", return_value=self._host):
            resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"vm1", resp.data)
        self.assertIn(b"vm2", resp.data)

    def test_api_vms_returns_json(self):
        with patch("app.vm.list_vms", return_value=(self._vms, None)):
            resp = self.client.get("/api/vms")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("vms", data)
        self.assertEqual(len(data["vms"]), 2)

    def test_api_host_returns_json(self):
        with patch("app.vm.host_info", return_value=self._host):
            resp = self.client.get("/api/host")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["cpu_count"], 4)

    def test_api_vms_includes_agg(self):
        with patch("app.vm.list_vms", return_value=(self._vms, None)):
            data = self.client.get("/api/vms").get_json()
        self.assertIn("agg", data)
        self.assertIn("cpu_allocated", data["agg"])


# ---------------------------------------------------------------------------
# VM detail + actions
# ---------------------------------------------------------------------------

class VmDetailTests(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        _login(self.client)
        self._vm = _make_vm("myvm")

    def test_vm_detail_200(self):
        with patch("app.vm.list_vms", return_value=([self._vm], None)), \
             patch("app.vm.vm_detail", return_value=self._vm), \
             patch("app.vm.list_switches", return_value=([], None)):
            resp = self.client.get("/vm/myvm")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"myvm", resp.data)

    def test_vm_detail_unknown_redirects(self):
        with patch("app.vm.list_vms", return_value=([], None)), \
             patch("app.vm.vm_detail", return_value=None), \
             patch("app.vm.list_switches", return_value=([], None)):
            resp = self.client.get("/vm/myvm", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)

    def test_invalid_vm_name_404(self):
        resp = self.client.get("/vm/bad name!")
        self.assertEqual(resp.status_code, 404)

    def test_vm_action_start(self):
        csrf = _csrf(self.client)
        with patch("app.vm.start_vm", return_value=("", 0)) as mock_start, \
             patch("app.vm.list_vms", return_value=([self._vm], None)), \
             patch("app.vm.vm_detail", return_value=self._vm), \
             patch("app.vm.list_switches", return_value=([], None)):
            resp = self.client.post("/vm/myvm/start",
                                    data={"csrf_token": csrf},
                                    follow_redirects=False)
        mock_start.assert_called_once_with("myvm")
        self.assertEqual(resp.status_code, 302)

    def test_vm_action_stop(self):
        csrf = _csrf(self.client)
        with patch("app.vm.stop_vm", return_value=("", 0)), \
             patch("app.vm.list_vms", return_value=([self._vm], None)), \
             patch("app.vm.vm_detail", return_value=self._vm), \
             patch("app.vm.list_switches", return_value=([], None)):
            resp = self.client.post("/vm/myvm/stop",
                                    data={"csrf_token": csrf},
                                    follow_redirects=False)
        self.assertEqual(resp.status_code, 302)

    def test_vm_action_delete_redirects_to_index(self):
        csrf = _csrf(self.client)
        with patch("app.vm.delete_vm", return_value=("", 0)):
            resp = self.client.post("/vm/myvm/delete",
                                    data={"csrf_token": csrf},
                                    follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/", resp.headers["Location"])

    def test_vm_action_unknown_404(self):
        csrf = _csrf(self.client)
        resp = self.client.post("/vm/myvm/fly",
                                data={"csrf_token": csrf})
        self.assertEqual(resp.status_code, 404)

    def test_vm_action_update(self):
        csrf = _csrf(self.client)
        with patch("app.vm.update_vm", return_value=("ok", 0)) as mock_upd, \
             patch("app.vm.list_vms", return_value=([self._vm], None)), \
             patch("app.vm.vm_detail", return_value=self._vm), \
             patch("app.vm.list_switches", return_value=([], None)):
            resp = self.client.post("/vm/myvm/update",
                                    data={"csrf_token": csrf, "cpu": "4",
                                          "memory": "2G", "loader": "uefi"},
                                    follow_redirects=False)
        mock_upd.assert_called_once()
        self.assertEqual(resp.status_code, 302)


# ---------------------------------------------------------------------------
# VM create
# ---------------------------------------------------------------------------

class VmCreateTests(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        _login(self.client)

    def test_create_get_200(self):
        with patch("app.vm.list_isos", return_value=([], None)), \
             patch("app.vm.list_switches", return_value=([], None)):
            resp = self.client.get("/vm/create")
        self.assertEqual(resp.status_code, 200)

    def test_create_post_success_redirects(self):
        csrf = _csrf(self.client)
        newvm = _make_vm("newbox")
        with patch("app.vm.create_vm", return_value=("", 0)), \
             patch("app.vm.list_isos", return_value=([], None)), \
             patch("app.vm.list_switches", return_value=([], None)), \
             patch("app.vm.list_vms", return_value=([newvm], None)), \
             patch("app.vm.vm_detail", return_value=newvm):
            resp = self.client.post("/vm/create",
                                    data={"csrf_token": csrf, "name": "newbox",
                                          "cpu": "2", "memory": "1G", "size": "10G",
                                          "loader": "bhyveload", "disk_type": "virtio-blk",
                                          "nic_type": "virtio-net", "console": "serial"},
                                    follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("newbox", resp.headers["Location"])

    def test_create_post_failure_returns_400(self):
        csrf = _csrf(self.client)
        with patch("app.vm.create_vm", return_value=("error: boom", 1)), \
             patch("app.vm.list_isos", return_value=([], None)), \
             patch("app.vm.list_switches", return_value=([], None)):
            resp = self.client.post("/vm/create",
                                    data={"csrf_token": csrf, "name": "newbox",
                                          "cpu": "2", "memory": "1G", "size": "10G",
                                          "loader": "bhyveload", "disk_type": "virtio-blk",
                                          "nic_type": "virtio-net", "console": "serial"})
        self.assertEqual(resp.status_code, 400)


# ---------------------------------------------------------------------------
# ISO management
# ---------------------------------------------------------------------------

class IsoTests(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        _login(self.client)

    def test_iso_list_200(self):
        with patch("app.vm.list_isos", return_value=(["freebsd.iso"], None)), \
             patch("os.path.getsize", return_value=700 * 1024 * 1024):
            resp = self.client.get("/isos")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"freebsd.iso", resp.data)

    def test_iso_upload_bad_filename(self):
        csrf = _csrf(self.client)
        data = {"csrf_token": csrf,
                "iso_file": (io.BytesIO(b"data"), "../../etc/passwd")}
        resp = self.client.post("/isos/upload",
                                data=data,
                                content_type="multipart/form-data",
                                follow_redirects=True)
        self.assertIn(b"Invalid filename", resp.data)

    def test_iso_upload_no_file(self):
        csrf = _csrf(self.client)
        resp = self.client.post("/isos/upload",
                                data={"csrf_token": csrf},
                                content_type="multipart/form-data",
                                follow_redirects=True)
        self.assertIn(b"No file selected", resp.data)

    def test_iso_upload_valid_saves(self):
        csrf = _csrf(self.client)
        data = {"csrf_token": csrf,
                "iso_file": (io.BytesIO(b"isodata"), "test.iso")}
        with patch("app.os.makedirs"), \
             patch("app.vm.list_isos", return_value=(["test.iso"], None)), \
             patch("os.path.getsize", return_value=7), \
             patch("werkzeug.datastructures.FileStorage.save") as mock_save:
            resp = self.client.post("/isos/upload",
                                    data=data,
                                    content_type="multipart/form-data",
                                    follow_redirects=True)
        mock_save.assert_called_once()
        self.assertEqual(resp.status_code, 200)

    def test_iso_delete_success(self):
        csrf = _csrf(self.client)
        with patch("app.vm.delete_iso", return_value=("deleted", 0)), \
             patch("app.vm.list_isos", return_value=([], None)):
            resp = self.client.post("/isos/test.iso/delete",
                                    data={"csrf_token": csrf},
                                    follow_redirects=True)
        self.assertIn(b"Deleted", resp.data)

    def test_iso_delete_failure_shows_error(self):
        csrf = _csrf(self.client)
        with patch("app.vm.delete_iso", return_value=("file not found", 1)), \
             patch("app.vm.list_isos", return_value=([], None)):
            resp = self.client.post("/isos/ghost.iso/delete",
                                    data={"csrf_token": csrf},
                                    follow_redirects=True)
        self.assertIn(b"failed", resp.data.lower())


# ---------------------------------------------------------------------------
# Networks
# ---------------------------------------------------------------------------

class NetworkTests(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        _login(self.client)

    def test_networks_get_200(self):
        with patch("app.vm.list_switches", return_value=([], None)):
            resp = self.client.get("/networks")
        self.assertEqual(resp.status_code, 200)

    def test_network_create_success(self):
        csrf = _csrf(self.client)
        with patch("app.vm.create_switch", return_value=("", 0)), \
             patch("app.vm.list_switches", return_value=([], None)):
            resp = self.client.post("/networks",
                                    data={"csrf_token": csrf, "name": "pub"},
                                    follow_redirects=True)
        self.assertIn(b"created", resp.data.lower())

    def test_network_create_failure_shows_error(self):
        csrf = _csrf(self.client)
        with patch("app.vm.create_switch", return_value=("error: exists", 1)), \
             patch("app.vm.list_switches", return_value=([], None)):
            resp = self.client.post("/networks",
                                    data={"csrf_token": csrf, "name": "pub"},
                                    follow_redirects=True)
        self.assertIn(b"failed", resp.data.lower())

    def test_network_delete_success(self):
        csrf = _csrf(self.client)
        with patch("app.vm.delete_switch", return_value=("", 0)), \
             patch("app.vm.list_switches", return_value=([], None)):
            resp = self.client.post("/network/pub/delete",
                                    data={"csrf_token": csrf, "name": "pub"},
                                    follow_redirects=True)
        self.assertIn(b"removed", resp.data.lower())

    def test_network_delete_failure_shows_error(self):
        csrf = _csrf(self.client)
        with patch("app.vm.delete_switch", return_value=("error: busy", 1)), \
             patch("app.vm.list_switches", return_value=([], None)):
            resp = self.client.post("/network/pub/delete",
                                    data={"csrf_token": csrf, "name": "pub"},
                                    follow_redirects=True)
        self.assertIn(b"failed", resp.data.lower())


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

class ErrorHandlerTests(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_403_renders_error_page(self):
        # Trigger CSRF failure on a POST without a token
        _login(self.client)
        resp = self.client.post("/networks", data={"name": "x"})
        self.assertEqual(resp.status_code, 403)
        self.assertIn(b"403", resp.data)

    def test_404_renders_error_page(self):
        resp = self.client.get("/no/such/path")
        self.assertEqual(resp.status_code, 404)
        self.assertIn(b"404", resp.data)


# ---------------------------------------------------------------------------
# _flash_result helper
# ---------------------------------------------------------------------------

class FlashResultTests(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_flash_result_success(self):
        with app.test_request_context("/"):
            with app.test_client() as c:
                with c.session_transaction() as sess:
                    sess["user"] = "alice"
                    sess["_csrf"] = "tok"
                # Just verify the helper doesn't raise
                from app import _flash_result
                with app.app_context():
                    with app.test_request_context("/"):
                        from flask import session
                        _flash_result("Op", ("all good", 0))

    def test_flash_result_failure(self):
        with app.test_request_context("/"):
            from app import _flash_result
            _flash_result("Op", ("error: boom", 1))

    def test_flash_result_bytes_msg(self):
        with app.test_request_context("/"):
            from app import _flash_result
            _flash_result("Op", (b"bytes error", 1))


# ---------------------------------------------------------------------------
# _switch_names helper
# ---------------------------------------------------------------------------

class SwitchNamesTests(unittest.TestCase):

    def test_always_includes_default(self):
        with patch("app.vm.list_switches", return_value=([], None)):
            from app import _switch_names
            names = _switch_names()
        self.assertIn("default", names)

    def test_existing_default_not_duplicated(self):
        sw = [{"name": "default"}, {"name": "pub"}]
        with patch("app.vm.list_switches", return_value=(sw, None)):
            from app import _switch_names
            names = _switch_names()
        self.assertEqual(names.count("default"), 1)

    def test_non_default_switch_included(self):
        sw = [{"name": "pub"}]
        with patch("app.vm.list_switches", return_value=(sw, None)):
            from app import _switch_names
            names = _switch_names()
        self.assertIn("pub", names)


# ---------------------------------------------------------------------------
# Console routes
# ---------------------------------------------------------------------------

class ConsoleRouteTests(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        _login(self.client)

    def _running_vm(self, name="runner"):
        return _make_vm(name, running=True)

    def test_console_page_no_session(self):
        v = self._running_vm()
        with patch("app.vm.list_vms", return_value=([v], None)), \
             patch("app.vm.vm_detail", return_value=v), \
             patch("app.vm.console_registry") as reg:
            reg.get.return_value = None
            resp = self.client.get("/vm/runner/console")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Start console", resp.data)

    def test_console_page_with_session(self):
        v = self._running_vm()
        entry = {"port": 19100, "pid": 999, "nmdm": "/dev/nmdm-runner.1B"}
        with patch("app.vm.list_vms", return_value=([v], None)), \
             patch("app.vm.vm_detail", return_value=v), \
             patch("app.vm.console_registry") as reg:
            reg.get.return_value = entry
            resp = self.client.get("/vm/runner/console")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Active", resp.data)
        self.assertIn(b"19100", resp.data)

    def test_console_page_stopped_vm_redirects(self):
        v = _make_vm("stopped", running=False)
        with patch("app.vm.list_vms", return_value=([v], None)), \
             patch("app.vm.vm_detail", return_value=v):
            resp = self.client.get("/vm/stopped/console", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)

    def test_console_start_success(self):
        v = self._running_vm()
        csrf = _csrf(self.client)
        entry = {"port": 19100, "pid": 999, "nmdm": "/dev/nmdm-runner.1B", "proc": None}
        with patch("app.vm.list_vms", return_value=([v], None)), \
             patch("app.vm.vm_detail", return_value=v), \
             patch("app.vm.console_registry") as reg, \
             patch("app._time.sleep"):
            reg.start.return_value = (entry, None)
            reg.get.return_value = entry
            resp = self.client.post("/vm/runner/console/start",
                                    data={"csrf_token": csrf},
                                    follow_redirects=True)
        reg.start.assert_called_once_with("runner", "/dev/nmdm-runner.1B")
        self.assertIn(b"19100", resp.data)

    def test_console_start_failure(self):
        v = self._running_vm()
        csrf = _csrf(self.client)
        with patch("app.vm.list_vms", return_value=([v], None)), \
             patch("app.vm.vm_detail", return_value=v), \
             patch("app.vm.console_registry") as reg, \
             patch("app._time.sleep"):
            reg.start.return_value = (None, "no free port")
            reg.get.return_value = None
            resp = self.client.post("/vm/runner/console/start",
                                    data={"csrf_token": csrf},
                                    follow_redirects=True)
        self.assertIn(b"failed", resp.data.lower())

    def test_console_stop_success(self):
        csrf = _csrf(self.client)
        with patch("app.vm.console_registry") as reg:
            reg.stop.return_value = (True, "console for 'runner' stopped")
            resp = self.client.post("/vm/runner/console/stop",
                                    data={"csrf_token": csrf},
                                    follow_redirects=True)
        reg.stop.assert_called_once_with("runner")
        self.assertIn(b"stopped", resp.data.lower())

    def test_console_stop_missing(self):
        csrf = _csrf(self.client)
        with patch("app.vm.console_registry") as reg, \
             patch("app.vm.list_vms", return_value=([], None)), \
             patch("app.vm.vm_detail", return_value=None), \
             patch("app.vm.list_switches", return_value=([], None)):
            reg.stop.return_value = (False, "no console session for 'x'")
            resp = self.client.post("/vm/runner/console/stop",
                                    data={"csrf_token": csrf},
                                    follow_redirects=True)
        self.assertIn(b"no console", resp.data.lower())

    def test_console_kill_success(self):
        csrf = _csrf(self.client)
        with patch("app.vm.console_registry") as reg:
            reg.kill_stuck.return_value = (True, "killed pid 999 for 'runner'")
            resp = self.client.post("/vm/runner/console/kill",
                                    data={"csrf_token": csrf},
                                    follow_redirects=True)
        reg.kill_stuck.assert_called_once_with("runner")
        self.assertIn(b"killed", resp.data.lower())

    def test_api_consoles_returns_json(self):
        with patch("app.vm.console_registry") as reg:
            reg.list_all.return_value = [
                {"name": "runner", "port": 19100, "pid": 999,
                 "nmdm": "/dev/nmdm-runner.1B", "alive": True}
            ]
            resp = self.client.get("/api/consoles")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["name"], "runner")
        self.assertEqual(data[0]["port"], 19100)


# ---------------------------------------------------------------------------
# Tunnel routes
# ---------------------------------------------------------------------------

class TunnelRouteTests(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        _login(self.client)

    # ── VXLAN unicast tunnel ──────────────────────────────────────────────

    def _tunnel_mocks(self):
        return {
            "app.vm.list_switches": ([], None),
            "app.vm.list_tunnel_interfaces": {"vxlan": [], "gre": [], "ipsec": []},
            "app.vm.list_host_interfaces": ["em0"],
        }

    def test_vxlan_tunnel_create_success(self):
        csrf = _csrf(self.client)
        with patch("app.vm.create_vxlan_tunnel", return_value=("VXLAN unicast tunnel vxlan10 created", 0)) as m, \
             patch("app.vm.list_switches", return_value=([], None)), \
             patch("app.vm.list_tunnel_interfaces", return_value={"vxlan":[],"gre":[],"ipsec":[]}), \
             patch("app.vm.list_host_interfaces", return_value=["em0"]):
            resp = self.client.post("/network/vxlan/tunnel/create",
                                    data={"csrf_token": csrf, "name": "vxlan10",
                                          "vni": "200", "local_ip": "192.168.1.85",
                                          "remote_ip": "10.0.0.1"},
                                    follow_redirects=True)
        m.assert_called_once_with("vxlan10", "200", "192.168.1.85", "10.0.0.1", "4789")
        self.assertIn(b"created", resp.data.lower())

    def test_vxlan_tunnel_create_custom_port(self):
        csrf = _csrf(self.client)
        with patch("app.vm.create_vxlan_tunnel", return_value=("ok", 0)) as m, \
             patch("app.vm.list_switches", return_value=([], None)), \
             patch("app.vm.list_tunnel_interfaces", return_value={"vxlan":[],"gre":[],"ipsec":[]}), \
             patch("app.vm.list_host_interfaces", return_value=["em0"]):
            self.client.post("/network/vxlan/tunnel/create",
                             data={"csrf_token": csrf, "name": "vxlan10",
                                   "vni": "200", "local_ip": "1.1.1.1",
                                   "remote_ip": "2.2.2.2", "port": "5000"},
                             follow_redirects=True)
        m.assert_called_once_with("vxlan10", "200", "1.1.1.1", "2.2.2.2", "5000")

    def test_vxlan_tunnel_create_missing_fields(self):
        csrf = _csrf(self.client)
        with patch("app.vm.list_switches", return_value=([], None)), \
             patch("app.vm.list_tunnel_interfaces", return_value={"vxlan":[],"gre":[],"ipsec":[]}), \
             patch("app.vm.list_host_interfaces", return_value=["em0"]):
            resp = self.client.post("/network/vxlan/tunnel/create",
                                    data={"csrf_token": csrf, "name": "vxlan10"},
                                    follow_redirects=True)
        self.assertIn(b"required", resp.data.lower())

    def test_vxlan_tunnel_create_failure(self):
        csrf = _csrf(self.client)
        with patch("app.vm.create_vxlan_tunnel", return_value=("error: already exists", 1)), \
             patch("app.vm.list_switches", return_value=([], None)), \
             patch("app.vm.list_tunnel_interfaces", return_value={"vxlan":[],"gre":[],"ipsec":[]}), \
             patch("app.vm.list_host_interfaces", return_value=["em0"]):
            resp = self.client.post("/network/vxlan/tunnel/create",
                                    data={"csrf_token": csrf, "name": "vxlan10",
                                          "vni": "200", "local_ip": "1.1.1.1",
                                          "remote_ip": "2.2.2.2"},
                                    follow_redirects=True)
        self.assertIn(b"failed", resp.data.lower())

    def test_vxlan_tunnel_delete_success(self):
        csrf = _csrf(self.client)
        with patch("app.vm.destroy_vxlan_tunnel", return_value=("destroyed", 0)) as m, \
             patch("app.vm.list_switches", return_value=([], None)), \
             patch("app.vm.list_tunnel_interfaces", return_value={"vxlan":[],"gre":[],"ipsec":[]}), \
             patch("app.vm.list_host_interfaces", return_value=["em0"]):
            resp = self.client.post("/network/vxlan/tunnel/vxlan10/delete",
                                    data={"csrf_token": csrf},
                                    follow_redirects=True)
        m.assert_called_once_with("vxlan10")
        self.assertIn(b"destroyed", resp.data.lower())

    # ── VXLAN switch (vm-bhyve) ───────────────────────────────────────────

    def test_vxlan_create_success(self):
        csrf = _csrf(self.client)
        with patch("app.vm.create_vxlan_switch", return_value=("", 0)) as m, \
             patch("app.vm.list_switches", return_value=([], None)), \
             patch("app.vm.list_tunnel_interfaces", return_value={"vxlan":[],"gre":[],"ipsec":[]}), \
             patch("app.vm.list_host_interfaces", return_value=["em0"]):
            resp = self.client.post("/network/vxlan/create",
                                    data={"csrf_token": csrf, "name": "overlay0",
                                          "vni": "100", "iface": "em0"},
                                    follow_redirects=True)
        m.assert_called_once_with("overlay0", "100", "em0")
        self.assertIn(b"created", resp.data.lower())

    def test_vxlan_create_missing_fields(self):
        csrf = _csrf(self.client)
        with patch("app.vm.list_switches", return_value=([], None)), \
             patch("app.vm.list_tunnel_interfaces", return_value={"vxlan":[],"gre":[],"ipsec":[]}), \
             patch("app.vm.list_host_interfaces", return_value=["em0"]):
            resp = self.client.post("/network/vxlan/create",
                                    data={"csrf_token": csrf, "name": "overlay0"},
                                    follow_redirects=True)
        self.assertIn(b"required", resp.data.lower())

    def test_vxlan_create_failure_shows_error(self):
        csrf = _csrf(self.client)
        with patch("app.vm.create_vxlan_switch", return_value=("error: already exists", 1)), \
             patch("app.vm.list_switches", return_value=([], None)), \
             patch("app.vm.list_tunnel_interfaces", return_value={"vxlan":[],"gre":[],"ipsec":[]}), \
             patch("app.vm.list_host_interfaces", return_value=["em0"]):
            resp = self.client.post("/network/vxlan/create",
                                    data={"csrf_token": csrf, "name": "o", "vni": "100", "iface": "em0"},
                                    follow_redirects=True)
        self.assertIn(b"failed", resp.data.lower())

    # ── GRE ──────────────────────────────────────────────────────────────

    def test_gre_create_success(self):
        csrf = _csrf(self.client)
        with patch("app.vm.create_gre_tunnel", return_value=("GRE tunnel gre0 created", 0)) as m, \
             patch("app.vm.list_switches", return_value=([], None)), \
             patch("app.vm.list_tunnel_interfaces", return_value={"vxlan":[],"gre":[],"ipsec":[]}), \
             patch("app.vm.list_host_interfaces", return_value=["em0"]):
            resp = self.client.post("/network/gre/create",
                                    data={"csrf_token": csrf, "name": "gre0",
                                          "local_ip": "1.1.1.1", "remote_ip": "2.2.2.2",
                                          "inner_local": "10.0.0.1/30", "inner_remote": "10.0.0.2"},
                                    follow_redirects=True)
        m.assert_called_once_with("gre0", "1.1.1.1", "2.2.2.2", "10.0.0.1/30", "10.0.0.2")
        self.assertIn(b"created", resp.data.lower())

    def test_gre_create_missing_fields(self):
        csrf = _csrf(self.client)
        with patch("app.vm.list_switches", return_value=([], None)), \
             patch("app.vm.list_tunnel_interfaces", return_value={"vxlan":[],"gre":[],"ipsec":[]}), \
             patch("app.vm.list_host_interfaces", return_value=["em0"]):
            resp = self.client.post("/network/gre/create",
                                    data={"csrf_token": csrf, "name": "gre0"},
                                    follow_redirects=True)
        self.assertIn(b"required", resp.data.lower())

    def test_gre_delete_success(self):
        csrf = _csrf(self.client)
        with patch("app.vm.destroy_gre_tunnel", return_value=("destroyed", 0)) as m, \
             patch("app.vm.list_switches", return_value=([], None)), \
             patch("app.vm.list_tunnel_interfaces", return_value={"vxlan":[],"gre":[],"ipsec":[]}), \
             patch("app.vm.list_host_interfaces", return_value=["em0"]):
            resp = self.client.post("/network/gre/gre0/delete",
                                    data={"csrf_token": csrf},
                                    follow_redirects=True)
        m.assert_called_once_with("gre0")
        self.assertIn(b"destroyed", resp.data.lower())

    def test_gre_delete_failure(self):
        csrf = _csrf(self.client)
        with patch("app.vm.destroy_gre_tunnel", return_value=("error: no such", 1)), \
             patch("app.vm.list_switches", return_value=([], None)), \
             patch("app.vm.list_tunnel_interfaces", return_value={"vxlan":[],"gre":[],"ipsec":[]}), \
             patch("app.vm.list_host_interfaces", return_value=["em0"]):
            resp = self.client.post("/network/gre/gre0/delete",
                                    data={"csrf_token": csrf},
                                    follow_redirects=True)
        self.assertIn(b"failed", resp.data.lower())

    # ── IPsec ─────────────────────────────────────────────────────────────

    def test_ipsec_create_success(self):
        csrf = _csrf(self.client)
        with patch("app.vm.create_ipsec_tunnel", return_value=("IPsec tunnel ipsec0 created", 0)) as m, \
             patch("app.vm.list_switches", return_value=([], None)), \
             patch("app.vm.list_tunnel_interfaces", return_value={"vxlan":[],"gre":[],"ipsec":[]}), \
             patch("app.vm.list_host_interfaces", return_value=["em0"]):
            resp = self.client.post("/network/ipsec/create",
                                    data={"csrf_token": csrf, "name": "ipsec0",
                                          "local_ip": "1.1.1.1", "remote_ip": "2.2.2.2",
                                          "inner_local": "172.16.0.1/30", "inner_remote": "172.16.0.2"},
                                    follow_redirects=True)
        m.assert_called_once_with("ipsec0", "1.1.1.1", "2.2.2.2", "172.16.0.1/30", "172.16.0.2", None)
        self.assertIn(b"created", resp.data.lower())

    def test_ipsec_create_missing_fields(self):
        csrf = _csrf(self.client)
        with patch("app.vm.list_switches", return_value=([], None)), \
             patch("app.vm.list_tunnel_interfaces", return_value={"vxlan":[],"gre":[],"ipsec":[]}), \
             patch("app.vm.list_host_interfaces", return_value=["em0"]):
            resp = self.client.post("/network/ipsec/create",
                                    data={"csrf_token": csrf, "name": "ipsec0"},
                                    follow_redirects=True)
        self.assertIn(b"required", resp.data.lower())

    def test_ipsec_delete_success(self):
        csrf = _csrf(self.client)
        with patch("app.vm.destroy_ipsec_tunnel", return_value=("destroyed", 0)) as m, \
             patch("app.vm.list_switches", return_value=([], None)), \
             patch("app.vm.list_tunnel_interfaces", return_value={"vxlan":[],"gre":[],"ipsec":[]}), \
             patch("app.vm.list_host_interfaces", return_value=["em0"]):
            resp = self.client.post("/network/ipsec/ipsec0/delete",
                                    data={"csrf_token": csrf},
                                    follow_redirects=True)
        m.assert_called_once_with("ipsec0")
        self.assertIn(b"destroyed", resp.data.lower())

    # ── Bridge addm/deletem ───────────────────────────────────────────────

    def test_bridge_addm_success(self):
        csrf = _csrf(self.client)
        with patch("app.vm.bridge_addm", return_value=("added gre0 to vm-default", 0)) as m, \
             patch("app.vm.list_switches", return_value=([], None)), \
             patch("app.vm.list_tunnel_interfaces", return_value={"vxlan":[],"gre":[],"ipsec":[]}), \
             patch("app.vm.list_host_interfaces", return_value=["em0"]):
            resp = self.client.post("/network/bridge/vm-default/addm",
                                    data={"csrf_token": csrf, "iface": "gre0"},
                                    follow_redirects=True)
        m.assert_called_once_with("vm-default", "gre0")
        self.assertIn(b"added", resp.data.lower())

    def test_bridge_addm_missing_iface(self):
        csrf = _csrf(self.client)
        with patch("app.vm.list_switches", return_value=([], None)), \
             patch("app.vm.list_tunnel_interfaces", return_value={"vxlan":[],"gre":[],"ipsec":[]}), \
             patch("app.vm.list_host_interfaces", return_value=["em0"]):
            resp = self.client.post("/network/bridge/vm-default/addm",
                                    data={"csrf_token": csrf},
                                    follow_redirects=True)
        self.assertIn(b"required", resp.data.lower())

    def test_bridge_deletem_success(self):
        csrf = _csrf(self.client)
        with patch("app.vm.bridge_deletem", return_value=("removed gre0 from vm-default", 0)) as m, \
             patch("app.vm.list_switches", return_value=([], None)), \
             patch("app.vm.list_tunnel_interfaces", return_value={"vxlan":[],"gre":[],"ipsec":[]}), \
             patch("app.vm.list_host_interfaces", return_value=["em0"]):
            resp = self.client.post("/network/bridge/vm-default/deletem",
                                    data={"csrf_token": csrf, "iface": "gre0"},
                                    follow_redirects=True)
        m.assert_called_once_with("vm-default", "gre0")
        self.assertIn(b"removed", resp.data.lower())

    # ── /api/tunnels ──────────────────────────────────────────────────────

    def test_api_tunnels_returns_json(self):
        payload = {"vxlan": [], "gre": [{"name": "gre0"}], "ipsec": []}
        with patch("app.vm.list_tunnel_interfaces", return_value=payload):
            resp = self.client.get("/api/tunnels")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("gre", data)
        self.assertEqual(data["gre"][0]["name"], "gre0")


class VmDiskNetRouteTests(unittest.TestCase):
    """Flask route tests for disk/network add/remove/update endpoints."""

    def setUp(self):
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        self.client = app.test_client()
        _login(self.client)

    def _vm_detail_mocks(self):
        """Return a context-manager stack that makes /vm/<name> render."""
        from contextlib import ExitStack
        stack = ExitStack()
        vms = [_make_vm("testvm")]
        stack.enter_context(patch("app.vm.list_vms", return_value=(vms, None)))
        stack.enter_context(patch("app.vm.vm_detail", return_value=_make_vm("testvm")))
        stack.enter_context(patch("app.vm.list_switches", return_value=(["default"], None)))
        return stack

    # ── disk add ─────────────────────────────────────────────────────────

    def test_disk_add_success(self):
        csrf = _csrf(self.client)
        with patch("app.vm.add_disk_conf", return_value=("disk1.img created", 0)) as m, \
             patch("app.vm.list_vms", return_value=([_make_vm("testvm")], None)), \
             patch("app.vm.vm_detail", return_value=_make_vm("testvm")), \
             patch("app.vm.list_switches", return_value=([], None)):
            resp = self.client.post("/vm/testvm/disk/add",
                                    data={"csrf_token": csrf, "disk_type": "virtio-blk",
                                          "size": "10G", "dev": "file"},
                                    follow_redirects=True)
        m.assert_called_once_with("testvm", "virtio-blk", "10G", "file")
        self.assertEqual(resp.status_code, 200)

    def test_disk_add_missing_size(self):
        csrf = _csrf(self.client)
        with patch("app.vm.list_vms", return_value=([_make_vm("testvm")], None)), \
             patch("app.vm.vm_detail", return_value=_make_vm("testvm")), \
             patch("app.vm.list_switches", return_value=([], None)):
            resp = self.client.post("/vm/testvm/disk/add",
                                    data={"csrf_token": csrf, "disk_type": "virtio-blk",
                                          "dev": "file"},
                                    follow_redirects=True)
        self.assertIn(b"required", resp.data.lower())

    def test_disk_add_bad_name(self):
        csrf = _csrf(self.client)
        resp = self.client.post("/vm/bad name!/disk/add",
                                data={"csrf_token": csrf, "size": "10G"},
                                follow_redirects=True)
        self.assertEqual(resp.status_code, 404)

    # ── disk remove ───────────────────────────────────────────────────────

    def test_disk_remove_success(self):
        csrf = _csrf(self.client)
        with patch("app.vm.remove_disk_conf", return_value=("disk 0 removed", 0)) as m, \
             patch("app.vm.list_vms", return_value=([_make_vm("testvm")], None)), \
             patch("app.vm.vm_detail", return_value=_make_vm("testvm")), \
             patch("app.vm.list_switches", return_value=([], None)):
            resp = self.client.post("/vm/testvm/disk/0/remove",
                                    data={"csrf_token": csrf},
                                    follow_redirects=True)
        m.assert_called_once_with("testvm", 0)
        self.assertEqual(resp.status_code, 200)

    def test_disk_remove_failure(self):
        csrf = _csrf(self.client)
        with patch("app.vm.remove_disk_conf", return_value=("error: does not exist", 1)), \
             patch("app.vm.list_vms", return_value=([_make_vm("testvm")], None)), \
             patch("app.vm.vm_detail", return_value=_make_vm("testvm")), \
             patch("app.vm.list_switches", return_value=([], None)):
            resp = self.client.post("/vm/testvm/disk/99/remove",
                                    data={"csrf_token": csrf},
                                    follow_redirects=True)
        self.assertIn(b"error", resp.data.lower())

    # ── disk update ───────────────────────────────────────────────────────

    def test_disk_update_success(self):
        csrf = _csrf(self.client)
        with patch("app.vm.update_disk_conf", return_value=("disk 0 updated", 0)) as m, \
             patch("app.vm.list_vms", return_value=([_make_vm("testvm")], None)), \
             patch("app.vm.vm_detail", return_value=_make_vm("testvm")), \
             patch("app.vm.list_switches", return_value=([], None)):
            resp = self.client.post("/vm/testvm/disk/0/update",
                                    data={"csrf_token": csrf, "disk_type": "ahci-hd",
                                          "disk_name": "", "dev": ""},
                                    follow_redirects=True)
        m.assert_called_once_with("testvm", 0, disk_type="ahci-hd",
                                  disk_name=None, dev=None)
        self.assertEqual(resp.status_code, 200)

    # ── network add ───────────────────────────────────────────────────────

    def test_network_add_success(self):
        csrf = _csrf(self.client)
        with patch("app.vm.add_network_conf", return_value=("nic added", 0)) as m, \
             patch("app.vm.list_vms", return_value=([_make_vm("testvm")], None)), \
             patch("app.vm.vm_detail", return_value=_make_vm("testvm")), \
             patch("app.vm.list_switches", return_value=([], None)):
            resp = self.client.post("/vm/testvm/network/add",
                                    data={"csrf_token": csrf, "switch": "default",
                                          "nic_type": "virtio-net"},
                                    follow_redirects=True)
        m.assert_called_once_with("testvm", "default", "virtio-net")
        self.assertEqual(resp.status_code, 200)

    def test_network_add_missing_switch(self):
        csrf = _csrf(self.client)
        with patch("app.vm.list_vms", return_value=([_make_vm("testvm")], None)), \
             patch("app.vm.vm_detail", return_value=_make_vm("testvm")), \
             patch("app.vm.list_switches", return_value=([], None)):
            resp = self.client.post("/vm/testvm/network/add",
                                    data={"csrf_token": csrf, "nic_type": "virtio-net"},
                                    follow_redirects=True)
        self.assertIn(b"required", resp.data.lower())

    # ── network remove ────────────────────────────────────────────────────

    def test_network_remove_success(self):
        csrf = _csrf(self.client)
        with patch("app.vm.remove_network_conf", return_value=("network 0 removed", 0)) as m, \
             patch("app.vm.list_vms", return_value=([_make_vm("testvm")], None)), \
             patch("app.vm.vm_detail", return_value=_make_vm("testvm")), \
             patch("app.vm.list_switches", return_value=([], None)):
            resp = self.client.post("/vm/testvm/network/0/remove",
                                    data={"csrf_token": csrf},
                                    follow_redirects=True)
        m.assert_called_once_with("testvm", 0)
        self.assertEqual(resp.status_code, 200)

    def test_network_remove_failure(self):
        csrf = _csrf(self.client)
        with patch("app.vm.remove_network_conf", return_value=("error: does not exist", 1)), \
             patch("app.vm.list_vms", return_value=([_make_vm("testvm")], None)), \
             patch("app.vm.vm_detail", return_value=_make_vm("testvm")), \
             patch("app.vm.list_switches", return_value=([], None)):
            resp = self.client.post("/vm/testvm/network/9/remove",
                                    data={"csrf_token": csrf},
                                    follow_redirects=True)
        self.assertIn(b"error", resp.data.lower())

    # ── network update ────────────────────────────────────────────────────

    def test_network_update_success(self):
        csrf = _csrf(self.client)
        with patch("app.vm.update_network_conf", return_value=("network 0 updated", 0)) as m, \
             patch("app.vm.list_vms", return_value=([_make_vm("testvm")], None)), \
             patch("app.vm.vm_detail", return_value=_make_vm("testvm")), \
             patch("app.vm.list_switches", return_value=([], None)):
            resp = self.client.post("/vm/testvm/network/0/update",
                                    data={"csrf_token": csrf, "switch": "public",
                                          "nic_type": "e1000", "mac": ""},
                                    follow_redirects=True)
        m.assert_called_once_with("testvm", 0, switch="public",
                                  nic_type="e1000", mac=None)
        self.assertEqual(resp.status_code, 200)


class UnlockRouteTests(unittest.TestCase):
    """Route tests for /vm/<name>/unlock."""

    def setUp(self):
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        self.client = app.test_client()
        _login(self.client)

    def test_unlock_success(self):
        csrf = _csrf(self.client)
        v = _make_vm("testvm")
        with patch("app.vm.remove_lock_file", return_value=("removed /var/vm/testvm/run.lock", 0)) as m, \
             patch("app.vm.list_vms", return_value=([v], None)), \
             patch("app.vm.vm_detail", return_value=v), \
             patch("app.vm.list_switches", return_value=([], None)):
            resp = self.client.post("/vm/testvm/unlock",
                                    data={"csrf_token": csrf},
                                    follow_redirects=True)
        m.assert_called_once_with("testvm")
        self.assertIn(b"removed", resp.data.lower())

    def test_unlock_no_lock_file(self):
        csrf = _csrf(self.client)
        v = _make_vm("testvm")
        with patch("app.vm.remove_lock_file", return_value=("no lock file found", 1)), \
             patch("app.vm.list_vms", return_value=([v], None)), \
             patch("app.vm.vm_detail", return_value=v), \
             patch("app.vm.list_switches", return_value=([], None)):
            resp = self.client.post("/vm/testvm/unlock",
                                    data={"csrf_token": csrf},
                                    follow_redirects=True)
        self.assertIn(b"failed", resp.data.lower())


class RenameStopAllRouteTests(unittest.TestCase):
    """Route tests for vm_rename, vms_stopall, vms_startall, iso_fetch."""

    def setUp(self):
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        self.client = app.test_client()
        _login(self.client)

    # ── rename ────────────────────────────────────────────────────────────

    def test_rename_success_redirects_to_new_name(self):
        csrf = _csrf(self.client)
        v = _make_vm("old")
        with patch("app.vm.vm_rename", return_value=("", 0)) as m, \
             patch("app.vm.list_vms", return_value=([v], None)), \
             patch("app.vm.vm_detail", return_value=_make_vm("newvm")), \
             patch("app.vm.list_switches", return_value=([], None)):
            resp = self.client.post("/vm/old/rename",
                                    data={"csrf_token": csrf, "new_name": "newvm"},
                                    follow_redirects=False)
        m.assert_called_once_with("old", "newvm")
        self.assertEqual(resp.status_code, 302)
        self.assertIn(b"newvm", resp.headers["Location"].encode())

    def test_rename_failure_flashes_error(self):
        csrf = _csrf(self.client)
        v = _make_vm("old")
        with patch("app.vm.vm_rename", return_value=("vm rename: not stopped", 1)), \
             patch("app.vm.list_vms", return_value=([v], None)), \
             patch("app.vm.vm_detail", return_value=v), \
             patch("app.vm.list_switches", return_value=([], None)):
            resp = self.client.post("/vm/old/rename",
                                    data={"csrf_token": csrf, "new_name": "newvm"},
                                    follow_redirects=True)
        self.assertIn(b"failed", resp.data.lower())

    def test_rename_missing_new_name(self):
        csrf = _csrf(self.client)
        v = _make_vm("old")
        with patch("app.vm.list_vms", return_value=([v], None)), \
             patch("app.vm.vm_detail", return_value=v), \
             patch("app.vm.list_switches", return_value=([], None)):
            resp = self.client.post("/vm/old/rename",
                                    data={"csrf_token": csrf, "new_name": ""},
                                    follow_redirects=True)
        self.assertIn(b"required", resp.data.lower())

    # ── stopall / startall ────────────────────────────────────────────────

    def test_stopall_success(self):
        csrf = _csrf(self.client)
        with patch("app.vm.vm_stopall", return_value=("", 0)) as m, \
             patch("app.vm.list_vms", return_value=([], None)), \
             patch("app.vm.host_info", return_value={}), \
             patch("app.vm.vm_aggregate_stats", return_value={}):
            resp = self.client.post("/vms/stopall",
                                    data={"csrf_token": csrf},
                                    follow_redirects=True)
        m.assert_called_once_with(force=False)
        self.assertEqual(resp.status_code, 200)

    def test_stopall_force(self):
        csrf = _csrf(self.client)
        with patch("app.vm.vm_stopall", return_value=("", 0)) as m, \
             patch("app.vm.list_vms", return_value=([], None)), \
             patch("app.vm.host_info", return_value={}), \
             patch("app.vm.vm_aggregate_stats", return_value={}):
            resp = self.client.post("/vms/stopall",
                                    data={"csrf_token": csrf, "force": "1"},
                                    follow_redirects=True)
        m.assert_called_once_with(force=True)
        self.assertEqual(resp.status_code, 200)

    def test_startall_success(self):
        csrf = _csrf(self.client)
        with patch("app.vm.vm_startall", return_value=("", 0)) as m, \
             patch("app.vm.list_vms", return_value=([], None)), \
             patch("app.vm.host_info", return_value={}), \
             patch("app.vm.vm_aggregate_stats", return_value={}):
            resp = self.client.post("/vms/startall",
                                    data={"csrf_token": csrf},
                                    follow_redirects=True)
        m.assert_called_once_with()
        self.assertEqual(resp.status_code, 200)

    # ── iso_fetch ─────────────────────────────────────────────────────────

    def test_iso_fetch_success(self):
        csrf = _csrf(self.client)
        with patch("app.vm.iso_fetch", return_value=("", 0)) as m, \
             patch("app.vm.list_isos", return_value=([], None)):
            resp = self.client.post("/isos/fetch",
                                    data={"csrf_token": csrf,
                                          "url": "https://example.com/x.iso"},
                                    follow_redirects=True)
        m.assert_called_once_with("https://example.com/x.iso")
        self.assertEqual(resp.status_code, 200)

    def test_iso_fetch_failure(self):
        csrf = _csrf(self.client)
        with patch("app.vm.iso_fetch", return_value=("vm iso: download failed", 1)), \
             patch("app.vm.list_isos", return_value=([], None)):
            resp = self.client.post("/isos/fetch",
                                    data={"csrf_token": csrf,
                                          "url": "https://example.com/x.iso"},
                                    follow_redirects=True)
        self.assertIn(b"failed", resp.data.lower())

    def test_iso_fetch_missing_url(self):
        csrf = _csrf(self.client)
        with patch("app.vm.list_isos", return_value=([], None)):
            resp = self.client.post("/isos/fetch",
                                    data={"csrf_token": csrf, "url": ""},
                                    follow_redirects=True)
        self.assertIn(b"required", resp.data.lower())


if __name__ == "__main__":
    unittest.main()
