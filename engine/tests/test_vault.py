"""Key vault: validation, name allowlist, and that errors never carry the key."""
import os
import sys
import unittest
from unittest import mock

from barnaby_engine.vault import EnvCredentials, MemoryCredentials, Vault, VaultError, valid_key

KEY = "apk_user_" + "Ab3/+=" * 20


class VaultTest(unittest.TestCase):
    def setUp(self):
        self.backend = MemoryCredentials()
        self.vault = Vault(self.backend)

    def test_roundtrip_and_status(self):
        self.assertEqual(self.vault.status(), {"devin": False})
        self.vault.set("devin", f"  {KEY}\n")  # pasted with stray whitespace
        self.assertEqual(self.backend.items["Barnaby-devin"], ("devin", KEY))
        self.assertEqual(self.vault.get("devin"), KEY)
        self.assertEqual(self.vault.status(), {"devin": True})
        self.vault.delete("devin")
        self.assertIsNone(self.vault.get("devin"))
        self.vault.delete("devin")  # idempotent

    def test_empty_stored_value_counts_as_unset(self):
        # What a failed `cmdkey /pass` leaves behind.
        self.backend.items["Barnaby-devin"] = ("devin", "")
        self.assertFalse(self.vault.has("devin"))

    def test_rejects_non_keys_without_echoing_them(self):
        for bad in ("short", "two words here and more", "a" * 513, "key\twith\ttab-and-long-enough",
                    "café-café-café-café"):
            with self.subTest(bad=bad[:12]):
                with self.assertRaises(VaultError) as ctx:
                    self.vault.set("devin", bad)
                self.assertNotIn(bad, str(ctx.exception))
        self.assertEqual(self.backend.items, {})

    def test_unknown_names_refused(self):
        for op in (lambda: self.vault.get("openai"), lambda: self.vault.set("../x", KEY),
                   lambda: self.vault.delete("Barnaby-devin")):
            with self.assertRaises(VaultError):
                op()

    def test_valid_key(self):
        self.assertTrue(valid_key(KEY))
        self.assertTrue(valid_key("cog_" + "x" * 40))
        self.assertFalse(valid_key(""))

    def test_has_swallows_backend_errors(self):
        class Broken(MemoryCredentials):
            def read(self, target):
                raise VaultError("boom")
        self.assertFalse(Vault(Broken()).has("devin"))


class EnvFallbackTest(unittest.TestCase):
    def test_reads_env_and_refuses_writes(self):
        vault = Vault(EnvCredentials())
        with mock.patch.dict(os.environ, {"BARNABY_SECRET_DEVIN": KEY}):
            self.assertEqual(vault.get("devin"), KEY)
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(vault.get("devin"))
        with self.assertRaises(VaultError):
            vault.set("devin", KEY)


@unittest.skipUnless(sys.platform == "win32", "Windows Credential Manager")
class WindowsCredentialsTest(unittest.TestCase):
    """Real CredWrite/CredRead/CredDelete against a throwaway target."""

    def test_roundtrip(self):
        from barnaby_engine.vault import WindowsCredentials
        creds, target = WindowsCredentials(), "Barnaby-unittest-selftest"
        try:
            self.assertIsNone(creds.read(target))
            creds.write(target, "unittest", KEY)
            self.assertEqual(creds.read(target), KEY)
        finally:
            creds.delete(target)
        self.assertIsNone(creds.read(target))
        creds.delete(target)  # deleting a missing entry is not an error


if __name__ == "__main__":
    unittest.main()
