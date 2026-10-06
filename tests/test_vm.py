"""Unit tests for vm.py parsers and helpers (no hypervisor required; use fixtures)."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import vm  # noqa: E402

FIX = os.path.join(HERE, "fixtures")


def slurp(name):
    with open(os.path.join(FIX, name)) as f:
        return f.read()


class ParseVmInfoTests(unittest.TestCase):
    def setUp(self):
        self.vms = vm.parse_vm_info(slurp("vm_info_all.txt"))

    def test_two_vms(self):
        self.assertEqual([v["name"] for v in self.vms], ["testvm1", "testvm2"])

    def test_state_and_cpu_mem(self):
        v1 = self.vms[0]
        self.assertEqual(v1["state"], "stopped")
        self.assertFalse(v1["running"])
        self.assertEqual(v1["cpu"], 2)
        self.assertEqual(v1["memory"], "1024M")
        self.assertEqual(v1["memory_bytes"], 1024 * 1024 * 1024)
        self.assertEqual(v1["datastore"], "default")
        self.assertEqual(v1["loader"], "bhyveload")

    def test_disk_sizes(self):
        d0 = self.vms[0]["disks"][0]
        self.assertEqual(d0["size_human"], "2.000G")
        self.assertEqual(d0["size_bytes"], 2147483648)
        self.assertEqual(d0["used_bytes"], 1024)
        self.assertEqual(d0["system_path"], "/var/vm/testvm1/disk0.img")

    def test_networks(self):
        n0 = self.vms[1]["networks"][0]
        self.assertEqual(n0["emulation"], "virtio-net")
        self.assertEqual(n0["virtual_switch"], "public")
        self.assertEqual(n0["fixed_mac_address"], "58:9c:fc:00:d0:81")

    def test_running_state_parses_pid(self):
        vms = vm.parse_vm_info(
            "Virtual Machine: foo\n"
            "------------------------\n"
            "  state: Running (12345)\n"
            "  cpu: 2\n"
            "  memory: 1G\n"
        )
        self.assertTrue(vms[0]["running"])
        self.assertEqual(vms[0]["pid"], 12345)

    def test_new_vm_has_loading_field(self):
        v = vm._new_vm("test")
        self.assertIn("loading", v)
        self.assertIn("disks", v)


class HelpersTests(unittest.TestCase):
    def test_bytes_value(self):
        self.assertEqual(vm.bytes_value("1024M"), 1024 ** 3)
        self.assertEqual(vm.bytes_value("2G"), 2 * 1024 ** 3)
        self.assertEqual(vm.bytes_value("512"), 512)

    def test_fmt_bytes(self):
        self.assertEqual(vm.fmt_bytes(0), "0.00 B")
        self.assertEqual(vm.fmt_bytes(1024), "1.00 KiB")
        self.assertEqual(vm.fmt_bytes(1024 * 1024), "1.00 MiB")
        self.assertEqual(vm.fmt_bytes(2 * 1024 ** 3), "2.00 GiB")
        self.assertEqual(vm.fmt_bytes(None), "-")

    def test_uptime_seconds(self):
        self.assertEqual(vm._uptime_seconds("1-02:00:00"), 1 * 86400 + 2 * 3600)
        self.assertEqual(vm._uptime_seconds("02:03:04"), 2 * 3600 + 3 * 60 + 4)
        self.assertEqual(vm._uptime_seconds("45"), 45)
        self.assertIsNone(vm._uptime_seconds(""))

    def test_valid_name(self):
        self.assertTrue(vm.valid_name("testvm1"))
        self.assertTrue(vm.valid_name("web-01.nginx"))
        self.assertFalse(vm.valid_name("Bad Name"))
        self.assertFalse(vm.valid_name("a b"))
        self.assertFalse(vm.valid_name("Leading"))
        self.assertFalse(vm.valid_name("leading-"))

    def test_valid_loader(self):
        for l in vm.LOADERS:
            self.assertTrue(vm.valid_loader(l))
        self.assertFalse(vm.valid_loader(""))
        self.assertFalse(vm.valid_loader("kboot"))
        self.assertFalse(vm.valid_loader("grub"))

    def test_valid_disk_type(self):
        for t in vm.DISK_TYPES:
            self.assertTrue(vm.valid_disk_type(t))
        self.assertFalse(vm.valid_disk_type("ide"))

    def test_valid_nic_type(self):
        for t in vm.NIC_TYPES:
            self.assertTrue(vm.valid_nic_type(t))
        self.assertFalse(vm.valid_nic_type("pcnet"))


class ConfHelpersTests(unittest.TestCase):
    def _lines(self, text):
        return text.split("\n")

    def test_conf_set_replaces(self):
        lines = self._lines('loader="bhyveload"\ncpu="1"')
        out = vm._conf_set(lines, "cpu", "4")
        self.assertIn('cpu="4"', out)
        self.assertNotIn('cpu="1"', out)

    def test_conf_set_appends_when_absent(self):
        lines = self._lines('loader="bhyveload"')
        out = vm._conf_set(lines, "memory", "2G")
        self.assertIn('memory="2G"', out)

    def test_conf_del_removes(self):
        lines = self._lines('loader="bhyveload"\ncpu="2"\nmemory="1G"')
        out = vm._conf_del(lines, "cpu")
        joined = "\n".join(out)
        self.assertNotIn("cpu=", joined)
        self.assertIn("memory=", joined)

    def test_conf_value(self):
        lines = self._lines('loader="bhyveload"\nutctime="yes"\n')
        self.assertEqual(vm._conf_value(lines, "loader"), "bhyveload")
        self.assertEqual(vm._conf_value(lines, "utctime"), "yes")
        self.assertIsNone(vm._conf_value(lines, "nonexistent"))


class SwitchesTests(unittest.TestCase):
    def test_parse_switches_empty(self):
        sw = vm.parse_switches(slurp("vm_switch_list.txt"))
        self.assertEqual(sw, [])


class AggregateTests(unittest.TestCase):
    def test_aggregate_empty(self):
        agg = vm.vm_aggregate_stats([])
        self.assertEqual(agg["cpu_allocated"], 0)
        self.assertEqual(agg["disk_used_bytes"], 0)
        self.assertEqual(agg["net_in_bytes"], 0)

    def test_aggregate_sums(self):
        vms = [
            {"cpu": 2, "memory_bytes": 1 << 30,
             "disks": [{"used_bytes": 1024}],
             "networks": [{"net_in_bytes": 512, "net_out_bytes": 256}]},
            {"cpu": 4, "memory_bytes": 2 << 30,
             "disks": [{"used_bytes": 2048}],
             "networks": [{"net_in_bytes": 100, "net_out_bytes": 50}]},
        ]
        agg = vm.vm_aggregate_stats(vms)
        self.assertEqual(agg["cpu_allocated"], 6)
        self.assertEqual(agg["disk_used_bytes"], 3072)
        self.assertEqual(agg["net_in_bytes"], 612)
        self.assertEqual(agg["net_out_bytes"], 306)


class OsProfilesTests(unittest.TestCase):
    def test_all_profiles_have_required_keys(self):
        required = {"label", "loader", "disk_type", "nic_type",
                    "console", "utctime"}
        for name, p in vm.OS_PROFILES.items():
            for k in required:
                self.assertIn(k, p, "profile '{0}' missing key '{1}'".format(name, k))

    def test_loaders_valid(self):
        for name, p in vm.OS_PROFILES.items():
            self.assertTrue(vm.valid_loader(p["loader"]),
                            "profile '{0}' has invalid loader '{1}'".format(name, p["loader"]))


class ParseVmListVTests(unittest.TestCase):
    def setUp(self):
        self.rows = vm.parse_vm_list_v(slurp("vm_list_v.txt"))

    def test_two_rows(self):
        self.assertEqual(len(self.rows), 2)
        self.assertEqual([r["name"] for r in self.rows], ["testvm1", "testvm2"])

    def test_fields(self):
        r = self.rows[0]
        self.assertEqual(r["datastore"], "default")
        self.assertEqual(r["loader"], "bhyveload")
        self.assertEqual(r["cpu"], 2)
        self.assertEqual(r["memory"], "1024M")
        self.assertEqual(r["state"], "Stopped")
        self.assertFalse(r["running"])

    def test_second_row_cpu(self):
        self.assertEqual(self.rows[1]["cpu"], 4)
        self.assertEqual(self.rows[1]["memory"], "2G")

    def test_stopped_not_running(self):
        for r in self.rows:
            self.assertFalse(r["running"])

    def test_empty_input(self):
        self.assertEqual(vm.parse_vm_list_v(""), [])

    def test_header_only(self):
        header = "NAME  DATASTORE  LOADER  CPU  MEMORY  VNC  AUTO  %CPU  RSZ  UPTIME  STATE"
        self.assertEqual(vm.parse_vm_list_v(header), [])

    def test_running_state(self):
        text = (
            "NAME  DATASTORE  LOADER     CPU  MEMORY  VNC  AUTO  %CPU  RSZ  UPTIME  STATE\n"
            "myvm  default    bhyveload  2    1G      -    No    1.2   64M  01:00   Running\n"
        )
        rows = vm.parse_vm_list_v(text)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["running"])
        self.assertEqual(rows[0]["pcpu"], "1.2")
        self.assertEqual(rows[0]["rsz"], "64M")


class ConsoleRegistryTests(unittest.TestCase):
    """Tests for ConsoleRegistry — all Popen calls are mocked."""

    def _make_proc(self, alive=True):
        from unittest.mock import MagicMock
        proc = MagicMock()
        proc.pid = 12345
        proc.poll.return_value = None if alive else 0
        return proc

    def setUp(self):
        # fresh registry per test
        self.reg = vm.ConsoleRegistry()

    def test_start_returns_entry(self):
        from unittest.mock import patch, MagicMock
        proc = self._make_proc()
        with patch("vm._free_gotty_port", return_value=19100), \
             patch("vm.subprocess.Popen", return_value=proc):
            entry, err = self.reg.start("vm1", "/dev/nmdm-vm1.1B")
        self.assertIsNone(err)
        self.assertEqual(entry["port"], 19100)
        self.assertEqual(entry["pid"], 12345)
        self.assertEqual(entry["nmdm"], "/dev/nmdm-vm1.1B")

    def test_start_reuses_alive_session(self):
        from unittest.mock import patch
        proc = self._make_proc()
        with patch("vm._free_gotty_port", return_value=19100), \
             patch("vm.subprocess.Popen", return_value=proc) as mock_popen:
            self.reg.start("vm1", "/dev/nmdm-vm1.1B")
            self.reg.start("vm1", "/dev/nmdm-vm1.1B")
        # Popen called only once — second start reuses existing
        self.assertEqual(mock_popen.call_count, 1)

    def test_start_restarts_dead_session(self):
        from unittest.mock import patch, MagicMock
        dead_proc = self._make_proc(alive=False)
        live_proc = self._make_proc()
        call_count = [0]
        def popen_side(*a, **kw):
            call_count[0] += 1
            return dead_proc if call_count[0] == 1 else live_proc
        with patch("vm._free_gotty_port", return_value=19100), \
             patch("vm.subprocess.Popen", side_effect=popen_side):
            self.reg.start("vm1", "/dev/nmdm-vm1.1B")
            entry, err = self.reg.start("vm1", "/dev/nmdm-vm1.1B")
        self.assertIsNone(err)
        self.assertEqual(call_count[0], 2)

    def test_start_no_free_port(self):
        from unittest.mock import patch
        with patch("vm._free_gotty_port", return_value=None):
            entry, err = self.reg.start("vm1", "/dev/nmdm-vm1.1B")
        self.assertIsNone(entry)
        self.assertIn("no free port", err)

    def test_start_popen_failure(self):
        from unittest.mock import patch
        with patch("vm._free_gotty_port", return_value=19100), \
             patch("vm.subprocess.Popen", side_effect=OSError("not found")):
            entry, err = self.reg.start("vm1", "/dev/nmdm-vm1.1B")
        self.assertIsNone(entry)
        self.assertIn("failed to start", err)

    def test_stop_existing(self):
        from unittest.mock import patch
        proc = self._make_proc()
        with patch("vm._free_gotty_port", return_value=19100), \
             patch("vm.subprocess.Popen", return_value=proc):
            self.reg.start("vm1", "/dev/nmdm-vm1.1B")
        ok, msg = self.reg.stop("vm1")
        self.assertTrue(ok)
        self.assertIn("vm1", msg)
        self.assertIsNone(self.reg.get("vm1"))

    def test_stop_missing(self):
        ok, msg = self.reg.stop("noexist")
        self.assertFalse(ok)
        self.assertIn("no console session", msg)

    def test_kill_stuck(self):
        from unittest.mock import patch
        proc = self._make_proc()
        with patch("vm._free_gotty_port", return_value=19100), \
             patch("vm.subprocess.Popen", return_value=proc):
            self.reg.start("vm1", "/dev/nmdm-vm1.1B")
        ok, msg = self.reg.kill_stuck("vm1")
        self.assertTrue(ok)
        proc.kill.assert_called()
        self.assertIn("killed", msg)

    def test_kill_stuck_missing(self):
        ok, msg = self.reg.kill_stuck("noexist")
        self.assertFalse(ok)

    def test_get_returns_none_for_exited(self):
        from unittest.mock import patch
        proc = self._make_proc(alive=False)
        with patch("vm._free_gotty_port", return_value=19100), \
             patch("vm.subprocess.Popen", return_value=proc):
            self.reg.start("vm1", "/dev/nmdm-vm1.1B")
        self.assertIsNone(self.reg.get("vm1"))

    def test_list_all_prunes_dead(self):
        from unittest.mock import patch
        alive = self._make_proc(alive=True)
        dead  = self._make_proc(alive=False)
        with patch("vm._free_gotty_port", side_effect=[19100, 19101]), \
             patch("vm.subprocess.Popen", side_effect=[alive, dead]):
            self.reg.start("live", "/dev/nmdm-live.1B")
            self.reg.start("dead", "/dev/nmdm-dead.1B")
        result = self.reg.list_all()
        names = [e["name"] for e in result]
        self.assertIn("live", names)
        self.assertNotIn("dead", names)

    def test_free_gotty_port_range(self):
        port = vm._free_gotty_port()
        if port is not None:
            self.assertGreaterEqual(port, vm._GOTTY_PORT_START)
            self.assertLessEqual(port, vm._GOTTY_PORT_END)


class TunnelHelpersTests(unittest.TestCase):
    """Unit tests for tunnel helper functions in vm.py.

    All kernel/ifconfig calls are mocked — no root or real interfaces needed.
    """

    # ── validators ────────────────────────────────────────────────────────

    def test_valid_ip_good(self):
        self.assertTrue(vm._valid_ip("192.168.1.1"))
        self.assertTrue(vm._valid_ip("10.0.0.1"))

    def test_valid_ip_bad(self):
        self.assertFalse(vm._valid_ip("not-an-ip"))
        self.assertFalse(vm._valid_ip(""))
        self.assertFalse(vm._valid_ip(None))

    def test_valid_cidr_good(self):
        self.assertTrue(vm._valid_cidr("10.0.0.1/30"))
        self.assertTrue(vm._valid_cidr("172.16.0.1/24"))

    def test_valid_cidr_bad(self):
        self.assertFalse(vm._valid_cidr("10.0.0.1"))
        self.assertFalse(vm._valid_cidr("10.0.0.1/33"))
        self.assertFalse(vm._valid_cidr(""))

    def test_valid_iface_good(self):
        self.assertTrue(vm._valid_iface("em0"))
        self.assertTrue(vm._valid_iface("gre0"))
        self.assertTrue(vm._valid_iface("vm-default"))

    def test_valid_iface_bad(self):
        self.assertFalse(vm._valid_iface(""))
        self.assertFalse(vm._valid_iface(None))
        self.assertFalse(vm._valid_iface("bad name!"))

    # ── kld_load ──────────────────────────────────────────────────────────

    def test_kld_load_success(self):
        from unittest.mock import patch
        with patch("vm._exec", return_value=("", 0)) as m:
            out, rc = vm.kld_load("if_gre")
        self.assertEqual(rc, 0)
        m.assert_called_once_with(["kldload", "if_gre"], timeout=10)

    def test_kld_load_already_loaded(self):
        from unittest.mock import patch
        with patch("vm._exec", return_value=("kldload: if_gre.ko: already loaded", 1)):
            out, rc = vm.kld_load("if_gre")
        self.assertEqual(rc, 0)

    def test_kld_load_real_failure(self):
        from unittest.mock import patch
        with patch("vm._exec", return_value=("no such file", 1)):
            out, rc = vm.kld_load("nonexistent_module")
        self.assertEqual(rc, 1)

    # ── create_vxlan_tunnel (unicast) ─────────────────────────────────────

    def test_create_vxlan_tunnel_success(self):
        from unittest.mock import patch, call
        with patch("vm.kld_load", return_value=("", 0)), \
             patch("vm._exec", return_value=("", 0)) as m:
            out, rc = vm.create_vxlan_tunnel("vxlan10", 200, "1.1.1.1", "2.2.2.2")
        self.assertEqual(rc, 0)
        self.assertIn("vxlan unicast tunnel", out.lower())
        calls = m.call_args_list
        self.assertEqual(len(calls), 1)
        args = calls[0].args[0]
        self.assertIn("vxlan10", args)
        self.assertIn("vxlanid", args)
        self.assertIn("200", args)
        self.assertIn("vxlanlocal", args)
        self.assertIn("1.1.1.1", args)
        self.assertIn("vxlanremote", args)
        self.assertIn("2.2.2.2", args)
        self.assertIn("vxlanport", args)
        self.assertIn("4789", args)

    def test_create_vxlan_tunnel_custom_port(self):
        from unittest.mock import patch
        with patch("vm.kld_load", return_value=("", 0)), \
             patch("vm._exec", return_value=("", 0)) as m:
            vm.create_vxlan_tunnel("vxlan10", 200, "1.1.1.1", "2.2.2.2", port=5000)
        args = m.call_args_list[0].args[0]
        self.assertIn("5000", args)

    def test_create_vxlan_tunnel_bad_name(self):
        _, rc = vm.create_vxlan_tunnel("gre0", 100, "1.1.1.1", "2.2.2.2")
        self.assertEqual(rc, 1)

    def test_create_vxlan_tunnel_bad_vni(self):
        _, rc = vm.create_vxlan_tunnel("vxlan0", 0, "1.1.1.1", "2.2.2.2")
        self.assertEqual(rc, 1)
        _, rc = vm.create_vxlan_tunnel("vxlan0", 16777216, "1.1.1.1", "2.2.2.2")
        self.assertEqual(rc, 1)

    def test_create_vxlan_tunnel_bad_ip(self):
        _, rc = vm.create_vxlan_tunnel("vxlan0", 100, "not-ip", "2.2.2.2")
        self.assertEqual(rc, 1)
        _, rc = vm.create_vxlan_tunnel("vxlan0", 100, "1.1.1.1", "not-ip")
        self.assertEqual(rc, 1)

    def test_create_vxlan_tunnel_bad_port(self):
        _, rc = vm.create_vxlan_tunnel("vxlan0", 100, "1.1.1.1", "2.2.2.2", port=99999)
        self.assertEqual(rc, 1)
        _, rc = vm.create_vxlan_tunnel("vxlan0", 100, "1.1.1.1", "2.2.2.2", port="notaport")
        self.assertEqual(rc, 1)

    def test_destroy_vxlan_tunnel_success(self):
        from unittest.mock import patch
        with patch("vm._exec", return_value=("", 0)) as m:
            out, rc = vm.destroy_vxlan_tunnel("vxlan10")
        self.assertEqual(rc, 0)
        m.assert_called_once_with(["ifconfig", "vxlan10", "destroy"], timeout=5)

    def test_destroy_vxlan_tunnel_bad_name(self):
        _, rc = vm.destroy_vxlan_tunnel("gre0")
        self.assertEqual(rc, 1)

    def test_list_tunnel_vxlan_mode_unicast(self):
        """vxlanremote with non-multicast addr → mode=unicast."""
        from unittest.mock import patch
        sample = (
            "vxlan10: flags=8843<UP,BROADCAST,RUNNING,SIMPLEX,MULTICAST> metric 0 mtu 1450\n"
            "\tvxlanid 200\n"
            "\tvxlanlocal 192.168.1.85\n"
            "\tvxlanremote 10.0.0.1\n"
        )
        with patch("vm._exec", return_value=(sample, 0)):
            result = vm.list_tunnel_interfaces()
        vx = result["vxlan"][0]
        self.assertEqual(vx["mode"], "unicast")
        self.assertEqual(vx["vxlanremote"], "10.0.0.1")

    def test_list_tunnel_vxlan_mode_multicast(self):
        """vxlanremote with multicast addr → mode=multicast."""
        from unittest.mock import patch
        sample = (
            "vxlan100: flags=8843<UP,BROADCAST,RUNNING,SIMPLEX,MULTICAST> metric 0 mtu 1450\n"
            "\tvxlanid 100\n"
            "\tvxlanlocal 192.168.1.85\n"
            "\tvxlanremote 239.1.2.3\n"
        )
        with patch("vm._exec", return_value=(sample, 0)):
            result = vm.list_tunnel_interfaces()
        vx = result["vxlan"][0]
        self.assertEqual(vx["mode"], "multicast")

    # ── create_vxlan_switch ───────────────────────────────────────────────

    def test_create_vxlan_switch_calls_vm(self):
        from unittest.mock import patch
        with patch("vm.run", return_value=("", 0)) as m:
            out, rc = vm.create_vxlan_switch("overlay0", 100, "em0")
        self.assertEqual(rc, 0)
        m.assert_called_once_with(
            ["switch", "create", "-t", "vxlan", "-i", "em0", "-n", "100", "overlay0"]
        )

    def test_create_vxlan_switch_bad_name(self):
        _, rc = vm.create_vxlan_switch("bad name!", 100, "em0")
        self.assertEqual(rc, 1)

    def test_create_vxlan_switch_bad_vni(self):
        _, rc = vm.create_vxlan_switch("overlay0", 0, "em0")
        self.assertEqual(rc, 1)
        _, rc = vm.create_vxlan_switch("overlay0", 16777216, "em0")
        self.assertEqual(rc, 1)
        _, rc = vm.create_vxlan_switch("overlay0", "notanint", "em0")
        self.assertEqual(rc, 1)

    def test_create_vxlan_switch_bad_iface(self):
        _, rc = vm.create_vxlan_switch("overlay0", 100, "bad iface!")
        self.assertEqual(rc, 1)

    # ── create_gre_tunnel ─────────────────────────────────────────────────

    def test_create_gre_tunnel_success(self):
        from unittest.mock import patch, call
        with patch("vm.kld_load", return_value=("", 0)), \
             patch("vm._exec", return_value=("", 0)) as m:
            out, rc = vm.create_gre_tunnel(
                "gre0", "192.168.1.1", "192.168.1.2", "10.99.0.1/30", "10.99.0.2"
            )
        self.assertEqual(rc, 0)
        calls = m.call_args_list
        self.assertEqual(calls[0], call(["ifconfig", "gre0", "create"], timeout=5))
        self.assertEqual(calls[1], call(["ifconfig", "gre0", "tunnel",
                                          "192.168.1.1", "192.168.1.2"], timeout=5))
        self.assertEqual(calls[2], call(["ifconfig", "gre0", "inet",
                                          "10.99.0.1/30", "10.99.0.2", "up"], timeout=5))

    def test_create_gre_tunnel_bad_name(self):
        _, rc = vm.create_gre_tunnel("eth0", "1.1.1.1", "2.2.2.2", "10.0.0.1/30", "10.0.0.2")
        self.assertEqual(rc, 1)

    def test_create_gre_tunnel_bad_ip(self):
        _, rc = vm.create_gre_tunnel("gre0", "not-ip", "2.2.2.2", "10.0.0.1/30", "10.0.0.2")
        self.assertEqual(rc, 1)

    def test_create_gre_tunnel_bad_cidr(self):
        _, rc = vm.create_gre_tunnel("gre0", "1.1.1.1", "2.2.2.2", "10.0.0.1", "10.0.0.2")
        self.assertEqual(rc, 1)

    def test_create_gre_tunnel_destroys_on_tunnel_fail(self):
        from unittest.mock import patch, call
        def side(*args, **kw):
            if "tunnel" in args[0]:
                return ("err", 1)
            return ("", 0)
        with patch("vm.kld_load", return_value=("", 0)), \
             patch("vm._exec", side_effect=side) as m:
            _, rc = vm.create_gre_tunnel(
                "gre0", "1.1.1.1", "2.2.2.2", "10.0.0.1/30", "10.0.0.2"
            )
        self.assertEqual(rc, 1)
        # destroy must have been called after the failure
        destroy_calls = [c for c in m.call_args_list if "destroy" in c.args[0]]
        self.assertTrue(len(destroy_calls) >= 1)

    # ── destroy_gre_tunnel ────────────────────────────────────────────────

    def test_destroy_gre_tunnel_success(self):
        from unittest.mock import patch
        with patch("vm._exec", return_value=("", 0)) as m:
            out, rc = vm.destroy_gre_tunnel("gre0")
        self.assertEqual(rc, 0)
        m.assert_called_once_with(["ifconfig", "gre0", "destroy"], timeout=5)

    def test_destroy_gre_tunnel_bad_name(self):
        _, rc = vm.destroy_gre_tunnel("eth0")
        self.assertEqual(rc, 1)

    # ── create_ipsec_tunnel ───────────────────────────────────────────────

    def test_create_ipsec_tunnel_success(self):
        from unittest.mock import patch, call
        with patch("vm.kld_load", return_value=("", 0)), \
             patch("vm._exec", return_value=("", 0)) as m:
            out, rc = vm.create_ipsec_tunnel(
                "ipsec0", "192.168.1.1", "192.168.1.2", "172.16.0.1/30", "172.16.0.2"
            )
        self.assertEqual(rc, 0)
        calls = m.call_args_list
        self.assertEqual(calls[0], call(["ifconfig", "ipsec0", "create"], timeout=5))
        self.assertEqual(calls[1], call(["ifconfig", "ipsec0", "inet", "tunnel",
                                          "192.168.1.1", "192.168.1.2"], timeout=5))

    def test_create_ipsec_tunnel_with_reqid(self):
        from unittest.mock import patch, call
        with patch("vm.kld_load", return_value=("", 0)), \
             patch("vm._exec", return_value=("", 0)) as m:
            vm.create_ipsec_tunnel(
                "ipsec0", "1.1.1.1", "2.2.2.2", "172.16.0.1/30", "172.16.0.2", reqid=42
            )
        self.assertIn(call(["ifconfig", "ipsec0", "create", "reqid", "42"], timeout=5),
                      m.call_args_list)

    def test_create_ipsec_tunnel_bad_name(self):
        _, rc = vm.create_ipsec_tunnel("gre0", "1.1.1.1", "2.2.2.2", "172.16.0.1/30", "172.16.0.2")
        self.assertEqual(rc, 1)

    # ── destroy_ipsec_tunnel ──────────────────────────────────────────────

    def test_destroy_ipsec_tunnel_success(self):
        from unittest.mock import patch
        with patch("vm._exec", return_value=("", 0)) as m:
            out, rc = vm.destroy_ipsec_tunnel("ipsec0")
        self.assertEqual(rc, 0)
        m.assert_called_once_with(["ifconfig", "ipsec0", "destroy"], timeout=5)

    def test_destroy_ipsec_tunnel_bad_name(self):
        _, rc = vm.destroy_ipsec_tunnel("gre0")
        self.assertEqual(rc, 1)

    # ── bridge_addm / bridge_deletem ──────────────────────────────────────

    def test_bridge_addm_success(self):
        from unittest.mock import patch
        with patch("vm._exec", return_value=("", 0)) as m:
            out, rc = vm.bridge_addm("vm-default", "gre0")
        self.assertEqual(rc, 0)
        m.assert_called_once_with(["ifconfig", "vm-default", "addm", "gre0"], timeout=5)

    def test_bridge_addm_bad_bridge(self):
        _, rc = vm.bridge_addm("bad bridge!", "gre0")
        self.assertEqual(rc, 1)

    def test_bridge_deletem_success(self):
        from unittest.mock import patch
        with patch("vm._exec", return_value=("", 0)) as m:
            out, rc = vm.bridge_deletem("vm-default", "gre0")
        self.assertEqual(rc, 0)
        m.assert_called_once_with(["ifconfig", "vm-default", "deletem", "gre0"], timeout=5)

    # ── list_tunnel_interfaces ────────────────────────────────────────────

    _IFCONFIG_SAMPLE = """\
em0: flags=1008843<UP,BROADCAST,RUNNING,SIMPLEX,MULTICAST,LOWER_UP> metric 0 mtu 1500
\tether c8:d3:ff:99:f5:ea
\tinet 192.168.1.85 netmask 0xffffff00 broadcast 192.168.1.255
gre0: flags=1008051<UP,POINTOPOINT,RUNNING,MULTICAST,LOWER_UP> metric 0 mtu 1476
\ttunnel inet 192.168.1.85 --> 192.168.1.2
\tinet 10.99.0.1 --> 10.99.0.2 netmask 0xfffffffc
vxlan100: flags=8843<UP,BROADCAST,RUNNING,SIMPLEX,MULTICAST> metric 0 mtu 1500
\tvxlanid 100
\tvxlanlocal 192.168.1.85
\tvxlanremote 239.1.2.3
ipsec0: flags=1008051<UP,POINTOPOINT,RUNNING,MULTICAST,LOWER_UP> metric 0 mtu 1480
\ttunnel inet 192.168.1.85 --> 192.168.1.3
\tinet 172.16.0.1 --> 172.16.0.2 netmask 0xfffffffc
lo0: flags=1008049<UP,LOOPBACK,RUNNING,MULTICAST,LOWER_UP> metric 0 mtu 16384
\tinet 127.0.0.1 netmask 0xff000000
"""

    def test_list_tunnel_interfaces_gre(self):
        from unittest.mock import patch
        with patch("vm._exec", return_value=(self._IFCONFIG_SAMPLE, 0)):
            result = vm.list_tunnel_interfaces()
        self.assertEqual(len(result["gre"]), 1)
        gre = result["gre"][0]
        self.assertEqual(gre["name"], "gre0")
        self.assertEqual(gre["tunnel_src"], "192.168.1.85")
        self.assertEqual(gre["tunnel_dst"], "192.168.1.2")
        self.assertEqual(gre["status"], "up")

    def test_list_tunnel_interfaces_vxlan(self):
        from unittest.mock import patch
        with patch("vm._exec", return_value=(self._IFCONFIG_SAMPLE, 0)):
            result = vm.list_tunnel_interfaces()
        self.assertEqual(len(result["vxlan"]), 1)
        vx = result["vxlan"][0]
        self.assertEqual(vx["name"], "vxlan100")
        self.assertEqual(vx["vxlanid"], "100")
        self.assertEqual(vx["vxlanlocal"], "192.168.1.85")

    def test_list_tunnel_interfaces_ipsec(self):
        from unittest.mock import patch
        with patch("vm._exec", return_value=(self._IFCONFIG_SAMPLE, 0)):
            result = vm.list_tunnel_interfaces()
        self.assertEqual(len(result["ipsec"]), 1)
        ipc = result["ipsec"][0]
        self.assertEqual(ipc["name"], "ipsec0")
        self.assertEqual(ipc["tunnel_src"], "192.168.1.85")
        self.assertEqual(ipc["tunnel_dst"], "192.168.1.3")

    def test_list_tunnel_interfaces_empty(self):
        from unittest.mock import patch
        with patch("vm._exec", return_value=("", 1)):
            result = vm.list_tunnel_interfaces()
        self.assertEqual(result["gre"], [])
        self.assertEqual(result["vxlan"], [])
        self.assertEqual(result["ipsec"], [])

    def test_list_tunnel_no_tunnel_ifaces(self):
        plain = "em0: flags=1008843<UP> metric 0 mtu 1500\n\tinet 1.2.3.4\n"
        from unittest.mock import patch
        with patch("vm._exec", return_value=(plain, 0)):
            result = vm.list_tunnel_interfaces()
        self.assertEqual(result["gre"], [])
        self.assertEqual(result["vxlan"], [])
        self.assertEqual(result["ipsec"], [])

    # ── list_host_interfaces ──────────────────────────────────────────────

    def test_list_host_interfaces_filters(self):
        from unittest.mock import patch
        mock_out = "em0 em1 lo0 tap0 bridge0 vm-default gre0 vxlan0 ipsec0"
        with patch("vm._exec", return_value=(mock_out, 0)):
            ifaces = vm.list_host_interfaces()
        self.assertIn("em0", ifaces)
        self.assertIn("em1", ifaces)
        self.assertNotIn("lo0", ifaces)
        self.assertNotIn("tap0", ifaces)
        self.assertNotIn("bridge0", ifaces)
        self.assertNotIn("gre0", ifaces)
        self.assertNotIn("vxlan0", ifaces)
        self.assertNotIn("ipsec0", ifaces)


class RemoveLockFileTests(unittest.TestCase):
    """Unit tests for remove_lock_file."""

    def test_removes_existing_lock(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            # Fake VM_DATASTORE so the path resolves into our tmpdir
            lock_dir = os.path.join(d, "testvm")
            os.makedirs(lock_dir)
            lock_path = os.path.join(lock_dir, "run.lock")
            open(lock_path, "w").close()
            orig = vm.VM_DATASTORE
            vm.VM_DATASTORE = d
            try:
                msg, rc = vm.remove_lock_file("testvm")
            finally:
                vm.VM_DATASTORE = orig
        self.assertEqual(rc, 0)
        self.assertIn("removed", msg)
        self.assertFalse(os.path.exists(lock_path))

    def test_no_lock_file_returns_error(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            lock_dir = os.path.join(d, "testvm")
            os.makedirs(lock_dir)
            orig = vm.VM_DATASTORE
            vm.VM_DATASTORE = d
            try:
                msg, rc = vm.remove_lock_file("testvm")
            finally:
                vm.VM_DATASTORE = orig
        self.assertEqual(rc, 1)
        self.assertIn("no lock file", msg)

    def test_invalid_name(self):
        _, rc = vm.remove_lock_file("bad name!")
        self.assertEqual(rc, 1)


class RenameStopAllTests(unittest.TestCase):
    """Unit tests for vm_rename, vm_stopall, vm_startall, iso_fetch."""

    def test_vm_rename_success(self):
        from unittest.mock import patch
        with patch("vm.run", return_value=("", 0)) as m:
            out, rc = vm.vm_rename("old", "newvm")
        self.assertEqual(rc, 0)
        m.assert_called_once_with(["rename", "old", "newvm"])

    def test_vm_rename_invalid_name(self):
        _, rc = vm.vm_rename("bad name!", "newvm")
        self.assertEqual(rc, 1)
        _, rc = vm.vm_rename("old", "bad name!")
        self.assertEqual(rc, 1)

    def test_vm_stopall_no_force(self):
        from unittest.mock import patch
        with patch("vm.run", return_value=("", 0)) as m:
            vm.vm_stopall(force=False)
        m.assert_called_once_with(["stopall"], timeout=120)

    def test_vm_stopall_force(self):
        from unittest.mock import patch
        with patch("vm.run", return_value=("", 0)) as m:
            vm.vm_stopall(force=True)
        m.assert_called_once_with(["stopall", "-f"], timeout=120)

    def test_vm_startall(self):
        from unittest.mock import patch
        with patch("vm.run", return_value=("", 0)) as m:
            vm.vm_startall()
        m.assert_called_once_with(["startall"], timeout=120)

    def test_iso_fetch_success(self):
        from unittest.mock import patch
        with patch("vm.run", return_value=("ok", 0)) as m:
            out, rc = vm.iso_fetch("https://example.com/install.iso")
        self.assertEqual(rc, 0)
        m.assert_called_once_with(["iso", "https://example.com/install.iso"], timeout=3600)

    def test_iso_fetch_bad_url(self):
        _, rc = vm.iso_fetch("not-a-url")
        self.assertEqual(rc, 1)
        _, rc = vm.iso_fetch("")
        self.assertEqual(rc, 1)

    def test_iso_fetch_ftp(self):
        from unittest.mock import patch
        with patch("vm.run", return_value=("", 0)):
            out, rc = vm.iso_fetch("ftp://mirror.example.com/file.iso")
        self.assertEqual(rc, 0)


class DiskNetConfTests(unittest.TestCase):
    """Unit tests for vm_conf_disks/networks and add/remove/update helpers.

    All file I/O uses a real tempfile; list_vms + conf_path are mocked so
    no running hypervisor or /var/vm filesystem is required.
    """

    _CONF = (
        'loader="bhyveload"\n'
        'cpu="2"\n'
        'disk0_type="virtio-blk"\n'
        'disk0_name="disk0.img"\n'
        'disk0_dev="file"\n'
        'disk1_type="ahci-cd"\n'
        'disk1_name="/var/vm/.iso/FreeBSD.iso"\n'
        'disk1_dev="custom"\n'
        'network0_type="virtio-net"\n'
        'network0_switch="default"\n'
        'network0_mac="58:9c:fc:00:01:02"\n'
    )

    def setUp(self):
        import tempfile
        self._tf = tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False)
        self._tf.write(self._CONF)
        self._tf.flush()
        self._tf.close()
        self._conf_path = self._tf.name
        # Fake vm entry — must include all keys that app.py index() accesses
        self._fake_vm = [{"name": "testvm", "state": "stopped", "running": False,
                          "pid": None, "cpu": 1, "memory": "1G", "memory_bytes": 0,
                          "datastore": "default", "loader": "bhyveload",
                          "datastore_path": os.path.dirname(self._conf_path),
                          "disks": [], "networks": [], "uptime": None,
                          "uptime_seconds": None, "pcpu": None, "rss_human": None,
                          "loading": False, "uuid": None, "memory_resident": None,
                          "nmdm_port": None}]
        # Patch list_vms and conf_path for the whole test
        from unittest.mock import patch
        patcher_lv = patch("vm.list_vms", return_value=(self._fake_vm, None))
        patcher_cp = patch("vm.conf_path", return_value=self._conf_path)
        self._p1 = patcher_lv.start()
        self._p2 = patcher_cp.start()

    def tearDown(self):
        import os as _os
        self._p1.stop()
        self._p2.stop()
        _os.unlink(self._conf_path)

    def _read_conf(self):
        with open(self._conf_path) as f:
            return f.read()

    # ── vm_conf_disks ─────────────────────────────────────────────────────

    def test_conf_disks_returns_two_entries(self):
        disks, err = vm.vm_conf_disks("testvm")
        self.assertIsNone(err)
        self.assertEqual(len(disks), 2)

    def test_conf_disks_index_and_fields(self):
        disks, _ = vm.vm_conf_disks("testvm")
        d0 = disks[0]
        self.assertEqual(d0["index"], 0)
        self.assertEqual(d0["type"], "virtio-blk")
        self.assertEqual(d0["name"], "disk0.img")
        self.assertEqual(d0["dev"], "file")

    def test_conf_disks_cd_entry(self):
        disks, _ = vm.vm_conf_disks("testvm")
        d1 = disks[1]
        self.assertEqual(d1["type"], "ahci-cd")
        self.assertEqual(d1["dev"], "custom")

    def test_conf_disks_missing_vm(self):
        # Stop the class-level conf_path patcher temporarily, use a context one.
        self._p2.stop()
        try:
            from unittest.mock import patch
            with patch("vm.conf_path", return_value=None):
                disks, err = vm.vm_conf_disks("nosuchvm")
            self.assertIsNotNone(err)
            self.assertEqual(disks, [])
        finally:
            # Restart so tearDown can stop it without error.
            from unittest.mock import patch as _patch
            self._p2 = _patch("vm.conf_path", return_value=self._conf_path).start()

    # ── vm_conf_networks ──────────────────────────────────────────────────

    def test_conf_networks_one_entry(self):
        nets, err = vm.vm_conf_networks("testvm")
        self.assertIsNone(err)
        self.assertEqual(len(nets), 1)

    def test_conf_networks_fields(self):
        nets, _ = vm.vm_conf_networks("testvm")
        n = nets[0]
        self.assertEqual(n["type"], "virtio-net")
        self.assertEqual(n["switch"], "default")
        self.assertEqual(n["mac"], "58:9c:fc:00:01:02")

    # ── remove_disk_conf ──────────────────────────────────────────────────

    def test_remove_disk_removes_keys(self):
        # Remove disk0; only 1 disk remains (renumbered as disk0).
        # The original disk0 values (virtio-blk, disk0.img) should be gone.
        out, rc = vm.remove_disk_conf("testvm", 0)
        self.assertEqual(rc, 0)
        conf = self._read_conf()
        self.assertNotIn("disk0.img", conf)   # original disk0 backing file gone
        self.assertNotIn("virtio-blk", conf)  # original disk0 emulation type gone

    def test_remove_disk_renumbers(self):
        # Remove disk0; disk1 should become disk0
        vm.remove_disk_conf("testvm", 0)
        conf = self._read_conf()
        self.assertIn("disk0_type", conf)
        self.assertIn("ahci-cd", conf)
        self.assertNotIn("disk1_type", conf)

    def test_remove_disk_preserves_others(self):
        vm.remove_disk_conf("testvm", 1)
        conf = self._read_conf()
        self.assertIn("disk0_name", conf)
        self.assertNotIn("disk1_name", conf)

    def test_remove_disk_out_of_range(self):
        out, rc = vm.remove_disk_conf("testvm", 99)
        self.assertEqual(rc, 1)
        self.assertIn("does not exist", out)

    def test_remove_disk_invalid_name(self):
        out, rc = vm.remove_disk_conf("bad name!", 0)
        self.assertEqual(rc, 1)

    def test_remove_disk_invalid_index(self):
        out, rc = vm.remove_disk_conf("testvm", "abc")
        self.assertEqual(rc, 1)

    # ── update_disk_conf ──────────────────────────────────────────────────

    def test_update_disk_type(self):
        out, rc = vm.update_disk_conf("testvm", 0, disk_type="ahci-hd")
        self.assertEqual(rc, 0)
        conf = self._read_conf()
        self.assertIn('disk0_type="ahci-hd"', conf)

    def test_update_disk_name(self):
        out, rc = vm.update_disk_conf("testvm", 0, disk_name="newdisk.img")
        self.assertEqual(rc, 0)
        conf = self._read_conf()
        self.assertIn('disk0_name="newdisk.img"', conf)

    def test_update_disk_absolute_path_sets_custom(self):
        out, rc = vm.update_disk_conf("testvm", 0, disk_name="/var/vm/.iso/new.iso")
        self.assertEqual(rc, 0)
        conf = self._read_conf()
        self.assertIn('disk0_dev="custom"', conf)

    def test_update_disk_explicit_dev(self):
        out, rc = vm.update_disk_conf("testvm", 0, dev="zvol")
        self.assertEqual(rc, 0)
        self.assertIn('disk0_dev="zvol"', self._read_conf())

    def test_update_disk_bad_type(self):
        out, rc = vm.update_disk_conf("testvm", 0, disk_type="floppy")
        self.assertEqual(rc, 1)

    def test_update_disk_bad_dev(self):
        out, rc = vm.update_disk_conf("testvm", 0, dev="nfs")
        self.assertEqual(rc, 1)

    def test_update_disk_out_of_range(self):
        out, rc = vm.update_disk_conf("testvm", 99)
        self.assertEqual(rc, 1)

    # ── remove_network_conf ───────────────────────────────────────────────

    def test_remove_network_removes_keys(self):
        out, rc = vm.remove_network_conf("testvm", 0)
        self.assertEqual(rc, 0)
        conf = self._read_conf()
        self.assertNotIn("network0_type", conf)

    def test_remove_network_out_of_range(self):
        out, rc = vm.remove_network_conf("testvm", 5)
        self.assertEqual(rc, 1)

    def test_remove_network_invalid_name(self):
        out, rc = vm.remove_network_conf("bad name!", 0)
        self.assertEqual(rc, 1)

    def test_remove_network_renumbers(self):
        # Write a conf with two networks
        with open(self._conf_path, "w") as f:
            f.write(
                'network0_type="virtio-net"\n'
                'network0_switch="default"\n'
                'network1_type="e1000"\n'
                'network1_switch="public"\n'
            )
        vm.remove_network_conf("testvm", 0)
        conf = self._read_conf()
        self.assertIn("network0_type", conf)
        self.assertIn("e1000", conf)
        self.assertNotIn("network1_type", conf)

    # ── update_network_conf ───────────────────────────────────────────────

    def test_update_network_switch(self):
        out, rc = vm.update_network_conf("testvm", 0, switch="public")
        self.assertEqual(rc, 0)
        self.assertIn('network0_switch="public"', self._read_conf())

    def test_update_network_nic_type(self):
        out, rc = vm.update_network_conf("testvm", 0, nic_type="e1000")
        self.assertEqual(rc, 0)
        self.assertIn('network0_type="e1000"', self._read_conf())

    def test_update_network_mac_set(self):
        out, rc = vm.update_network_conf("testvm", 0, mac="aa:bb:cc:dd:ee:ff")
        self.assertEqual(rc, 0)
        self.assertIn('network0_mac="aa:bb:cc:dd:ee:ff"', self._read_conf())

    def test_update_network_mac_clear(self):
        # Pass mac=None → mac key unchanged
        out, rc = vm.update_network_conf("testvm", 0, mac=None)
        self.assertEqual(rc, 0)
        # original mac still there
        self.assertIn("58:9c:fc:00:01:02", self._read_conf())

    def test_update_network_bad_switch(self):
        out, rc = vm.update_network_conf("testvm", 0, switch="bad name!")
        self.assertEqual(rc, 1)

    def test_update_network_bad_nic_type(self):
        out, rc = vm.update_network_conf("testvm", 0, nic_type="rtl8139")
        self.assertEqual(rc, 1)

    def test_update_network_out_of_range(self):
        out, rc = vm.update_network_conf("testvm", 9)
        self.assertEqual(rc, 1)

    # ── add_disk_conf (validation only — no real filesystem) ──────────────

    def test_add_disk_invalid_name(self):
        out, rc = vm.add_disk_conf("bad name!", "virtio-blk", "10G")
        self.assertEqual(rc, 1)

    def test_add_disk_invalid_type(self):
        out, rc = vm.add_disk_conf("testvm", "floppy", "10G")
        self.assertEqual(rc, 1)

    def test_add_disk_invalid_size(self):
        out, rc = vm.add_disk_conf("testvm", "virtio-blk", "not-a-size")
        self.assertEqual(rc, 1)

    def test_add_disk_invalid_dev(self):
        out, rc = vm.add_disk_conf("testvm", "virtio-blk", "10G", dev="nfs")
        self.assertEqual(rc, 1)

    # ── add_network_conf (validation only) ────────────────────────────────

    def test_add_network_invalid_name(self):
        out, rc = vm.add_network_conf("bad name!", "default")
        self.assertEqual(rc, 1)

    def test_add_network_invalid_switch(self):
        out, rc = vm.add_network_conf("testvm", "bad switch!")
        self.assertEqual(rc, 1)

    def test_add_network_invalid_nic_type(self):
        out, rc = vm.add_network_conf("testvm", "default", nic_type="rtl8139")
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
