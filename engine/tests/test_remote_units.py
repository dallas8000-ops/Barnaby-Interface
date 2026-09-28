"""Tablet access building blocks: TLS material, device registry, pairing codes, lockout."""
import ipaddress
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

from cryptography import x509
from cryptography.x509.oid import ExtendedKeyUsageOID

from barnaby_engine.remote import DeviceRegistry, Lockout, Pairing, TlsMaterial, lan_addresses


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class TlsMaterialTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.ips = ["192.168.1.20"]
        self.tls = TlsMaterial(self.dir, hostname="GILLIOM-PC", ips=lambda: list(self.ips))

    def load(self, name):
        return x509.load_pem_x509_certificate((self.dir / name).read_bytes())

    def test_issues_ca_and_server_cert_for_current_addresses(self):
        cert_path, key_path = self.tls.ensure()
        server, ca = self.load("server.pem"), self.load("ca.pem")
        self.assertEqual(server.issuer, ca.subject)
        san = server.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        self.assertIn(ipaddress.ip_address("192.168.1.20"), san.get_values_for_type(x509.IPAddress))
        dns = san.get_values_for_type(x509.DNSName)
        self.assertIn("gilliom-pc", dns)
        self.assertIn("gilliom-pc.local", dns)
        eku = server.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        self.assertIn(ExtendedKeyUsageOID.SERVER_AUTH, eku)
        self.assertFalse(server.extensions.get_extension_for_class(x509.BasicConstraints).value.ca)
        self.assertTrue(key_path.exists())

    def test_ca_is_name_constrained_to_private_addresses(self):
        self.tls.ensure()
        ca = self.load("ca.pem")
        bc = ca.extensions.get_extension_for_class(x509.BasicConstraints)
        self.assertTrue(bc.value.ca)
        self.assertEqual(bc.value.path_length, 0)
        nc = ca.extensions.get_extension_for_class(x509.NameConstraints)
        self.assertTrue(nc.critical)
        permitted = nc.value.permitted_subtrees
        nets = {p.value for p in permitted if isinstance(p, x509.IPAddress)}
        self.assertIn(ipaddress.ip_network("192.168.0.0/16"), nets)
        self.assertIn(ipaddress.ip_network("10.0.0.0/8"), nets)
        self.assertNotIn(ipaddress.ip_network("0.0.0.0/0"), nets)
        dns = {p.value for p in permitted if isinstance(p, x509.DNSName)}
        self.assertEqual(dns, {"local", "localhost", "gilliom-pc"})

    def test_reuses_certs_and_reissues_only_server_cert_when_address_changes(self):
        self.tls.ensure()
        ca_before = (self.dir / "ca.pem").read_bytes()
        server_before = (self.dir / "server.pem").read_bytes()
        self.tls.ensure()
        self.assertEqual((self.dir / "server.pem").read_bytes(), server_before)
        self.ips[:] = ["10.0.0.7"]
        self.tls.ensure()
        self.assertEqual((self.dir / "ca.pem").read_bytes(), ca_before)  # tablet keeps trusting
        self.assertNotEqual((self.dir / "server.pem").read_bytes(), server_before)
        san = self.load("server.pem").extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        self.assertIn(ipaddress.ip_address("10.0.0.7"), san.get_values_for_type(x509.IPAddress))

    def test_server_names_always_fit_the_ca_constraints(self):
        # 192.0.2.x is "private" to Python but outside RFC 1918: it must never be named.
        self.ips[:] = ["192.168.1.20", "192.0.2.2", "100.64.0.5", "8.8.8.8", "fe80::1", "10.1.2.3", "junk"]
        self.tls.ensure()
        server = self.load("server.pem")
        nc = self.load("ca.pem").extensions.get_extension_for_class(x509.NameConstraints).value
        nets = [p.value for p in nc.permitted_subtrees if isinstance(p, x509.IPAddress)]
        dns_ok = [p.value for p in nc.permitted_subtrees if isinstance(p, x509.DNSName)]
        san = server.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        ips = san.get_values_for_type(x509.IPAddress)
        self.assertEqual({str(i) for i in ips}, {"127.0.0.1", "192.168.1.20", "10.1.2.3"})
        for ip in ips:
            self.assertTrue(any(ip in n for n in nets), ip)
        for name in san.get_values_for_type(x509.DNSName):
            self.assertTrue(any(name == d or name.endswith("." + d) for d in dns_ok), name)

    def test_ca_download_and_fingerprint(self):
        self.tls.ensure()
        self.assertTrue(self.tls.ca_pem().startswith(b"-----BEGIN CERTIFICATE-----"))
        fp = self.tls.ca_fingerprint()
        self.assertRegex(fp, r"^([0-9A-F]{2}:){31}[0-9A-F]{2}$")

    @unittest.skipIf(sys.platform == "win32", "POSIX permissions")
    def test_private_keys_are_owner_only(self):
        self.tls.ensure()
        for name in ("ca.key", "server.key"):
            self.assertEqual(stat.S_IMODE(os.stat(self.dir / name).st_mode), 0o600, name)


class DeviceRegistryTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "devices.json"
        self.clock = Clock()
        self.reg = DeviceRegistry(self.path, clock=self.clock)

    def test_pair_stores_only_a_hash(self):
        device_id, token = self.reg.pair("Galaxy Tab S10")
        self.assertGreaterEqual(len(token), 43)  # 256 bits, urlsafe base64
        raw = self.path.read_text()
        self.assertNotIn(token, raw)
        stored = json.loads(raw)["devices"][0]
        self.assertEqual(stored["id"], device_id)
        self.assertEqual(len(stored["token_sha256"]), 64)

    def test_authenticate_and_persist(self):
        device_id, token = self.reg.pair("Tab")
        self.assertEqual(self.reg.authenticate(token), device_id)
        self.assertIsNone(self.reg.authenticate(token + "x"))
        self.assertIsNone(self.reg.authenticate(""))
        reloaded = DeviceRegistry(self.path, clock=self.clock)
        self.assertEqual(reloaded.authenticate(token), device_id)

    def test_revoke(self):
        d1, t1 = self.reg.pair("A")
        d2, t2 = self.reg.pair("B")
        self.assertTrue(self.reg.revoke(d1))
        self.assertFalse(self.reg.revoke(d1))
        self.assertIsNone(self.reg.authenticate(t1))
        self.assertEqual(self.reg.authenticate(t2), d2)
        self.assertEqual([d["id"] for d in self.reg.list()], [d2])

    def test_listing_never_exposes_hashes(self):
        self.reg.pair("A")
        for d in self.reg.list():
            self.assertEqual(set(d), {"id", "name", "created", "last_seen"})

    def test_touch_updates_last_seen(self):
        device_id, token = self.reg.pair("A")
        self.clock.t += 3600
        self.reg.touch(device_id)
        self.assertEqual(self.reg.list()[0]["last_seen"], self.clock.t)

    def test_device_cap(self):
        for i in range(DeviceRegistry.MAX_DEVICES):
            self.reg.pair(f"t{i}")
        with self.assertRaises(ValueError):
            self.reg.pair("one too many")

    def test_corrupt_file_starts_empty_rather_than_crashing(self):
        self.path.write_text("{not json")
        self.assertEqual(DeviceRegistry(self.path, clock=self.clock).list(), [])

    @unittest.skipIf(sys.platform == "win32", "POSIX permissions")
    def test_file_is_owner_only(self):
        self.reg.pair("A")
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)


class PairingTest(unittest.TestCase):
    def test_single_use_and_expiry(self):
        clock = Clock()
        p = Pairing(clock=clock)
        self.assertFalse(p.consume("anything"))
        code, expires = p.start()
        self.assertGreaterEqual(len(code), 16)
        self.assertEqual(expires, clock.t + Pairing.TTL_S)
        self.assertFalse(p.consume(code + "x"))
        self.assertTrue(p.consume(code))
        self.assertFalse(p.consume(code))  # used up
        code2, _ = p.start()
        clock.t += Pairing.TTL_S + 1
        self.assertFalse(p.consume(code2))  # expired

    def test_new_code_replaces_old(self):
        p = Pairing(clock=Clock())
        old, _ = p.start()
        new, _ = p.start()
        self.assertFalse(p.consume(old))
        self.assertTrue(p.consume(new))


class LockoutTest(unittest.TestCase):
    def test_blocks_after_limit_then_expires(self):
        clock = Clock()
        lock = Lockout(clock=clock)
        for _ in range(Lockout.LIMIT - 1):
            lock.fail("192.168.1.50")
        self.assertFalse(lock.blocked("192.168.1.50"))
        lock.fail("192.168.1.50")
        self.assertTrue(lock.blocked("192.168.1.50"))
        self.assertFalse(lock.blocked("192.168.1.51"))
        clock.t += Lockout.BLOCK_S + 1
        self.assertFalse(lock.blocked("192.168.1.50"))

    def test_old_failures_age_out(self):
        clock = Clock()
        lock = Lockout(clock=clock)
        for _ in range(Lockout.LIMIT - 1):
            lock.fail("ip")
        clock.t += Lockout.WINDOW_S + 1
        lock.fail("ip")
        self.assertFalse(lock.blocked("ip"))


class LanAddressesTest(unittest.TestCase):
    def test_only_private_ipv4(self):
        for ip in lan_addresses():
            addr = ipaddress.ip_address(ip)
            self.assertTrue(any(addr in ipaddress.ip_network(n) for n in
                                ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")), ip)


if __name__ == "__main__":
    unittest.main()
