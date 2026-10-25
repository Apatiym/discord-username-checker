"""Offline unit tests.

Nothing here touches the network or the real `config.json`: the config path and the
VPN cache path are redirected into a temporary directory in `setUp`. That matters —
an earlier version of the test suite overwrote a real config once.

Run with:

    python -m unittest discover -s tests -v
"""

import json
import os
import re
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import creds  # noqa: E402
import ds_username as ds  # noqa: E402
import net  # noqa: E402
import ui  # noqa: E402

ANSI = re.compile(r"\x1b\[[0-9;]*m")


class Base(unittest.TestCase):
    """Isolated config + VPN cache for every test."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="duck-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg_path = os.path.join(self.tmp, "config.json")
        self.cache_path = os.path.join(self.tmp, "vpns.json")
        self._patches = [
            mock.patch.object(ds, "CONFIG_FILE", self.cfg_path),
            mock.patch.object(net, "CACHE", self.cache_path),
        ]
        for patch in self._patches:
            patch.start()
            self.addCleanup(patch.stop)

    def make_dir(self, *parts):
        path = os.path.join(self.tmp, *parts)
        os.makedirs(path, exist_ok=True)
        return path

    def make_client_dir(self, name, root=None):
        """A folder that looks like real Discord data (has leveldb)."""
        root = root or self.tmp
        data = os.path.join(root, name)
        os.makedirs(os.path.join(data, "Local Storage", "leveldb"), exist_ok=True)
        return data


class ConfigTests(Base):
    def test_defaults_when_file_missing(self):
        cfg = ds.load_config()
        self.assertEqual(cfg["mode"], "safe")
        self.assertEqual(cfg["workers"], 1)
        self.assertEqual(cfg["vpn"]["size"], 12)
        self.assertFalse(cfg["use_ip"])

    def test_defaults_are_not_shared(self):
        cfg = ds.load_config()
        cfg["vpn"]["size"] = 3
        self.assertEqual(ds.DEFAULTS["vpn"]["size"], 12)

    def test_corrupt_file_falls_back_to_defaults(self):
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            f.write("{ this is not json")
        cfg = ds.load_config()
        self.assertEqual(cfg["mode"], "safe")

    def test_non_dict_json_falls_back(self):
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            json.dump([1, 2, 3], f)
        self.assertEqual(ds.load_config()["workers"], 1)

    def test_save_roundtrip(self):
        cfg = ds.load_config()
        cfg["token"] = "abc"
        cfg["workers"] = 4
        ds.save_config(cfg)
        with open(self.cfg_path, "r", encoding="utf-8") as f:
            on_disk = json.load(f)
        self.assertEqual(on_disk["token"], "abc")
        self.assertEqual(on_disk["workers"], 4)

    def test_save_leaves_no_temp_file(self):
        ds.save_config(ds.load_config())
        self.assertFalse(os.path.exists(self.cfg_path + ".tmp"))

    def test_unknown_keys_survive(self):
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            json.dump({"mode": "fast", "custom": 7}, f)
        cfg = ds.load_config()
        self.assertEqual(cfg["mode"], "fast")
        self.assertEqual(cfg["custom"], 7)


class LockTests(Base):
    def test_expired_locks_are_dropped(self):
        cfg = {"locks": {"token": time.time() - 5, "ip": time.time() + 60}}
        fresh = ds.locks(cfg)
        self.assertEqual(list(fresh), ["ip"])
        self.assertNotIn("token", cfg["locks"])

    def test_unknown_channel_is_dropped(self):
        cfg = {"locks": {"vpn": time.time() + 60}}
        self.assertEqual(ds.locks(cfg), {})

    def test_non_numeric_is_dropped(self):
        cfg = {"locks": {"token": "soon"}}
        self.assertEqual(ds.locks(cfg), {})

    def test_lock_left_per_channel(self):
        cfg = {"locks": {"token": time.time() + 10, "ip": time.time() + 100}}
        self.assertAlmostEqual(ds.lock_left(cfg, "token"), 10, delta=1)
        self.assertAlmostEqual(ds.lock_left(cfg), 100, delta=1)

    def test_set_lock_adds_margin_and_persists(self):
        cfg = ds.load_config()
        ds.set_lock(cfg, 60, "ip")
        self.assertAlmostEqual(ds.lock_left(cfg, "ip"), 60 + ds.LOCK_MARGIN, delta=2)
        with open(self.cfg_path, "r", encoding="utf-8") as f:
            self.assertIn("ip", json.load(f)["locks"])

    def test_set_lock_keeps_other_channel(self):
        cfg = ds.load_config()
        ds.set_lock(cfg, 10, "token")
        ds.set_lock(cfg, 10, "ip")
        self.assertEqual(sorted(ds.locks(cfg)), ["ip", "token"])


class ChannelTests(Base):
    def test_account_channel_preferred(self):
        self.assertEqual(ds.pick_channel({}, "tok"), "token")

    def test_nothing_when_account_locked_and_ip_disabled(self):
        cfg = {"locks": {"token": time.time() + 60}}
        self.assertIsNone(ds.pick_channel(cfg, "tok"))

    def test_ip_channel_without_token(self):
        self.assertEqual(ds.pick_channel({}, None), "ip")

    def test_ip_channel_allowed_when_account_locked(self):
        cfg = {"locks": {"token": time.time() + 60}}
        self.assertEqual(ds.pick_channel(cfg, "tok", allow_ip=True), "ip")

    def test_none_when_both_locked(self):
        cfg = {"locks": {"token": time.time() + 60, "ip": time.time() + 60}}
        self.assertIsNone(ds.pick_channel(cfg, "tok", allow_ip=True))


class PacerTests(Base):
    def test_safe_mode_has_a_floor(self):
        pacer = ds.Pacer(1.0)
        for _ in range(300):
            pacer.ok()
        self.assertAlmostEqual(pacer.delay, 0.4, places=3)

    def test_fast_mode_accelerates_to_zero(self):
        pacer = ds.Pacer(0.0)
        pacer.ok()
        self.assertEqual(pacer.delay, 0.0)

    def test_success_shaves_a_step(self):
        pacer = ds.Pacer(1.0)
        pacer.ok()
        self.assertAlmostEqual(pacer.delay, 1.0 - ds.Pacer.STEP, places=6)

    def test_soft_limit_multiplies(self):
        pacer = ds.Pacer(1.0)
        pacer.hit(1)
        self.assertAlmostEqual(pacer.delay, 2.2, places=6)

    def test_hard_limit_does_not_touch_delay(self):
        pacer = ds.Pacer(1.0)
        pacer.hit(30)
        self.assertEqual(pacer.delay, 1.0)

    def test_delay_is_capped(self):
        pacer = ds.Pacer(1.0)
        pacer.delay = 3.5
        pacer.hit(2)
        self.assertEqual(pacer.delay, ds.Pacer.MAX)

    def test_label_has_seconds_and_rate(self):
        label = ds.Pacer(2.0).label()
        self.assertIn("2.00s", label)
        self.assertIn("/s", label)


class GateTests(Base):
    def test_short_pause_is_silent(self):
        self.assertFalse(ds.Gate().pause(2))

    def test_long_pause_is_announced(self):
        self.assertTrue(ds.Gate().pause(10))

    def test_pause_never_shortens(self):
        gate = ds.Gate()
        gate.pause(10)
        until = gate.until
        gate.pause(1)
        self.assertEqual(gate.until, until)


class StatsTests(Base):
    def test_counters(self):
        stats = ds.Stats()
        stats.add(ds.TAKEN)
        stats.add(ds.FREE, "lume")
        stats.add(ds.INVALID)
        stats.add(ds.ERROR)
        stats.add(ds.LIMITED)
        self.assertEqual(stats.taken, 1)
        self.assertEqual(stats.free, ["lume"])
        self.assertEqual(stats.invalid, 1)
        self.assertEqual(stats.errors, 1)
        self.assertEqual(stats.limits, 1)

    def test_note_lock_keeps_the_longest(self):
        stats = ds.Stats()
        stats.note_lock(10)
        first = stats.locked_until
        stats.note_lock(1)
        self.assertEqual(stats.locked_until, first)

    def test_fmt_left(self):
        self.assertEqual(ds.fmt_left(59), "59s")
        self.assertEqual(ds.fmt_left(61), "1m 01s")
        self.assertEqual(ds.fmt_left(-5), "0s")


class NameTests(Base):
    def test_length_and_uniqueness(self):
        names = ds.gen_unique(50, 4, "abcdef")
        self.assertEqual(len(names), 50)
        self.assertEqual(len(set(names)), 50)
        self.assertTrue(all(len(n) == 4 for n in names))

    def test_count_is_capped_by_the_space(self):
        names = ds.gen_unique(5000, 3, "ab")
        self.assertEqual(len(names), 8)

    def test_zero_count(self):
        self.assertEqual(ds.gen_unique(0, 3, "ab"), [])


class PathTests(Base):
    def test_has_leveldb(self):
        data = self.make_client_dir("discord")
        self.assertTrue(ds.has_leveldb(data))
        self.assertFalse(ds.has_leveldb(self.make_dir("empty")))
        self.assertFalse(ds.has_leveldb(""))

    def test_normalize_accepts_the_leveldb_folder(self):
        data = self.make_client_dir("discord")
        self.assertEqual(
            ds.normalize_path(os.path.join(data, "Local Storage", "leveldb")), data
        )

    def test_normalize_accepts_local_state(self):
        data = self.make_client_dir("discord")
        local_state = os.path.join(data, "Local State")
        with open(local_state, "w", encoding="utf-8") as f:
            f.write("{}")
        self.assertEqual(ds.normalize_path(local_state), data)

    def test_normalize_strips_quotes(self):
        data = self.make_client_dir("discord")
        self.assertEqual(ds.normalize_path(f'"{data}"'), data)

    def test_normalize_expands_variables(self):
        data = self.make_client_dir("discord")
        with mock.patch.dict(os.environ, {"APPDATA": self.tmp}):
            self.assertEqual(ds.normalize_path(r"%APPDATA%\discord"), data)

    def test_install_folder_falls_back_to_roaming(self):
        local = self.make_dir("Local", "Discord")
        roaming = self.make_dir("Roaming")
        data = self.make_client_dir("discord", root=roaming)
        with mock.patch.dict(os.environ, {"APPDATA": roaming}):
            self.assertEqual(ds.normalize_path(local), data)

    def test_install_folder_case_is_ignored(self):
        local = self.make_dir("Local", "Discord")
        roaming = self.make_dir("Roaming")
        data = self.make_client_dir("Discord", root=roaming)
        with mock.patch.dict(os.environ, {"APPDATA": roaming}):
            self.assertEqual(ds.normalize_path(local), data)

    def test_junk_returns_none(self):
        self.assertIsNone(ds.normalize_path(""))
        self.assertIsNone(ds.normalize_path("   "))
        self.assertIsNone(ds.normalize_path(os.path.join(self.tmp, "nope")))

    def test_expand_path_strips_quotes_and_spaces(self):
        self.assertEqual(ds.expand_path("  'C:\\a\\b'  "), os.path.normpath("C:\\a\\b"))
        self.assertEqual(ds.expand_path(""), "")

    def test_find_discord_prefers_stable(self):
        roaming = self.make_dir("Roaming")
        self.make_client_dir("discordptb", root=roaming)
        stable = self.make_client_dir("discord", root=roaming)
        with mock.patch.dict(os.environ, {"APPDATA": roaming}):
            self.assertEqual(ds.find_discord(), stable)

    def test_find_discord_returns_none(self):
        with mock.patch.dict(os.environ, {"APPDATA": self.make_dir("Roaming")}):
            self.assertIsNone(ds.find_discord())

    def test_autodetect_rewrites_a_bad_stored_path(self):
        data = self.make_client_dir("discord")
        cfg = {"discord_path": os.path.join(self.tmp, "stale")}
        with mock.patch.dict(os.environ, {"APPDATA": self.tmp}):
            self.assertTrue(ds.autodetect(cfg))
        self.assertEqual(os.path.normpath(cfg["discord_path"]), os.path.normpath(data))


class UriTests(unittest.TestCase):
    VLESS = (
        "vless://11111111-2222-3333-4444-555555555555@example.com:443"
        "?security=tls&sni=cdn.example.com&fp=chrome&type=ws&path=%2Fws&host=cdn.example.com#Node"
    )

    def test_vless_tls(self):
        node = net.parse_uri(self.VLESS)
        self.assertEqual(node["proto"], "vless")
        self.assertEqual(node["server"], "example.com")
        self.assertEqual(node["port"], 443)
        self.assertEqual(node["name"], "Node")
        self.assertTrue(node["tls"]["enabled"])
        self.assertEqual(node["tls"]["server_name"], "cdn.example.com")
        self.assertEqual(node["tls"]["utls"]["fingerprint"], "chrome")
        self.assertEqual(node["transport"]["type"], "ws")
        self.assertEqual(node["transport"]["path"], "/ws")

    def test_vless_reality_without_key_is_rejected(self):
        uri = "vless://uuid@example.com:443?security=reality&sni=a.com"
        self.assertIsNone(net.parse_uri(uri))

    def test_vless_reality_with_key(self):
        uri = "vless://uuid@example.com:443?security=reality&pbk=PUB&sid=ab&sni=a.com"
        node = net.parse_uri(uri)
        self.assertTrue(node["tls"]["reality"]["enabled"])
        self.assertEqual(node["tls"]["reality"]["public_key"], "PUB")
        self.assertEqual(node["tls"]["reality"]["short_id"], "ab")

    def test_vless_ipv6_host(self):
        node = net.parse_uri("vless://uuid@[2001:db8::1]:8443?security=none")
        self.assertEqual(node["server"], "2001:db8::1")
        self.assertEqual(node["port"], 8443)
        self.assertNotIn("tls", node)

    def test_trojan(self):
        node = net.parse_uri("trojan://secret@example.com:443?sni=a.com#T")
        self.assertEqual(node["proto"], "trojan")
        self.assertEqual(node["password"], "secret")
        self.assertTrue(node["tls"]["enabled"])
        self.assertEqual(node["tls"]["server_name"], "a.com")

    def test_hysteria2_with_obfs(self):
        uri = "hysteria2://pw@example.com:8443?sni=a.com&obfs=salamander&obfs-password=x#H"
        node = net.parse_uri(uri)
        self.assertEqual(node["proto"], "hysteria2")
        self.assertEqual(node["obfs"], {"type": "salamander", "password": "x"})

    def test_unsupported_scheme(self):
        self.assertIsNone(net.parse_uri("ss://whatever"))
        self.assertIsNone(net.parse_uri("not a uri"))
        self.assertIsNone(net.parse_uri("vless://broken"))

    def test_subscription_dedupes_and_skips_comments(self):
        text = "\n".join([
            "# comment",
            "",
            self.VLESS,
            self.VLESS.replace("#Node", "#Other"),
            "trojan://secret@example.com:443#T",
        ])
        nodes = net.parse_subscription(text)
        self.assertEqual(len(nodes), 2)

    def test_node_key_is_hashable(self):
        node = net.parse_uri(self.VLESS)
        self.assertEqual(len({net._node_key(node)}), 1)


class SingBoxTests(unittest.TestCase):
    def test_build_config_maps_port_to_outbound(self):
        a = net.parse_uri("trojan://a@one.example:443#A")
        b = net.parse_uri("trojan://b@two.example:8443#B")
        config = net.build_config([a, b], [20001, 20002])

        self.assertEqual([i["listen_port"] for i in config["inbounds"]], [20001, 20002])
        self.assertEqual([o["tag"] for o in config["outbounds"]], ["out-0", "out-1", "direct"])
        self.assertEqual(config["route"]["final"], "direct")
        self.assertEqual(
            config["route"]["rules"],
            [
                {"inbound": ["in-0"], "outbound": "out-0"},
                {"inbound": ["in-1"], "outbound": "out-1"},
            ],
        )
        self.assertEqual(config["outbounds"][0]["server"], "one.example")
        self.assertEqual(config["outbounds"][1]["server_port"], 8443)

    def test_outbound_keeps_tls_and_transport(self):
        node = net.parse_uri(UriTests.VLESS)
        out = net.outbound(node, "out-0")
        self.assertEqual(out["type"], "vless")
        self.assertEqual(out["uuid"], "11111111-2222-3333-4444-555555555555")
        self.assertIn("tls", out)
        self.assertIn("transport", out)


class HistoryTests(Base):
    def node(self, host):
        return net.parse_uri(f"trojan://pw@{host}:443#n")

    def test_good_history_merges_instead_of_replacing(self):
        net.save_good([self.node("a.example")])
        net.save_good([self.node("b.example")])
        self.assertEqual(len(net.load_good()), 2)

    def test_expired_history_is_forgotten(self):
        old = time.time() - net.GOOD_TTL - 60
        key = net._key_str(net._node_key(self.node("a.example")))
        with open(self.cache_path, "w", encoding="utf-8") as f:
            json.dump({"good": {key: old}}, f)
        self.assertEqual(net.load_good(), [])

    def test_order_by_history_puts_known_nodes_first(self):
        good = self.node("good.example")
        cold = self.node("cold.example")
        net.save_good([good])
        ordered = net.order_by_history([cold, good])
        self.assertEqual(ordered[0]["server"], "good.example")

    def test_missing_cache_is_not_an_error(self):
        self.assertEqual(net.load_good(), [])
        self.assertEqual(net.order_by_history([self.node("a.example")])[0]["server"], "a.example")


class TokenTests(Base):
    PLAIN = b"MTAwMDAwMDAwMDAwMDAwMDAw.GHIJKL.abcdefghijklmnopqrstuvwxyz01234"

    def test_plain_token_is_found(self):
        data = self.make_client_dir("discord")
        with open(os.path.join(data, "Local Storage", "leveldb", "000001.log"), "wb") as f:
            f.write(b'\x00\x01token\x00' + self.PLAIN + b'\x00\x02')
        found = creds.extract_tokens(data)
        self.assertEqual(found[0][0], self.PLAIN.decode())
        self.assertEqual(found[0][1], "plain text")

    def test_encrypted_blob_without_key_is_skipped(self):
        data = self.make_client_dir("discord")
        with open(os.path.join(data, "Local Storage", "leveldb", "000002.log"), "wb") as f:
            f.write(b"dQw4w9WgXcQ:" + b"A" * 60)
        self.assertEqual(creds.extract_tokens(data), [])

    def test_missing_folder_is_empty(self):
        self.assertEqual(creds.extract_tokens(os.path.join(self.tmp, "nope")), [])

    def test_data_dirs_requires_leveldb(self):
        roaming = self.make_dir("Roaming")
        self.make_client_dir("discord", root=roaming)
        self.make_dir("Roaming", "discordptb")  # no leveldb -> ignored
        with mock.patch.dict(os.environ, {"APPDATA": roaming, "LOCALAPPDATA": self.tmp}):
            dirs = creds.data_dirs()
        self.assertEqual([os.path.basename(d) for d in dirs], ["discord"])

    def test_master_key_missing_local_state(self):
        self.assertIsNone(creds.master_key(self.make_client_dir("discord")))

    def test_master_key_without_encrypted_key(self):
        data = self.make_client_dir("discord")
        with open(os.path.join(data, "Local State"), "w", encoding="utf-8") as f:
            json.dump({"os_crypt": {}}, f)
        self.assertIsNone(creds.master_key(data))

    def test_dpapi_rejects_garbage(self):
        with self.assertRaises(OSError):
            creds.dpapi_unprotect(b"definitely not dpapi data")


class UiTests(unittest.TestCase):
    def test_paint_preserves_visible_text(self):
        self.assertEqual(ANSI.sub("", ui.paint("abc")), "abc")

    def test_paint_does_not_colour_spaces(self):
        self.assertEqual(ANSI.sub("", ui.paint("a b")), "a b")

    def test_paint_keeps_every_line(self):
        painted = ANSI.sub("", ui.paint("one\ntwo"))
        self.assertEqual(painted, "one\ntwo")

    def test_sample_endpoints(self):
        self.assertEqual(ui.sample(0.0), ui.STOPS[0])
        self.assertEqual(ui.sample(1.0), ui.STOPS[-1])
        self.assertEqual(ui.sample(-1), ui.STOPS[0])

    def test_sample_is_monotonic_in_red(self):
        # the croissant gradient goes from dark brown to pale cream
        self.assertLess(ui.sample(0.0)[0], ui.sample(1.0)[0])

    def test_muted_and_accent_wrap_text(self):
        self.assertIn("hi", ANSI.sub("", ui.muted("hi")))
        self.assertIn("1", ANSI.sub("", ui.accent("1")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
