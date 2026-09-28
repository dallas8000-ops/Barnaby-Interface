"""Tablet protocol additions and config keys."""
import base64
import json
import unittest

from pydantic import ValidationError

from barnaby_engine.config import Config
from barnaby_engine.protocol import MAX_CLIP_B64, Pair, parse_command, parse_pair
from barnaby_engine.server import permit


def cmd(type_, **kw):
    return parse_command(json.dumps({"type": type_, **kw}))


class TabletProtocolTest(unittest.TestCase):
    def test_audio_clip_bounds(self):
        pcm = base64.b64encode(b"\x00\x01" * 16000).decode()
        self.assertEqual(cmd("audio_clip", pcm_b64=pcm).pcm_b64, pcm)
        self.assertEqual(MAX_CLIP_B64, 4 * ((60 * 16000 * 2 + 2) // 3))
        for bad in ("", "a" * (MAX_CLIP_B64 + 4)):
            with self.subTest(n=len(bad)):
                with self.assertRaises(ValidationError):
                    cmd("audio_clip", pcm_b64=bad)

    def test_tablet_commands(self):
        self.assertEqual(cmd("tablet_pair_start").type, "tablet_pair_start")
        self.assertEqual(cmd("tablet_revoke", device_id="dev_abc-123").device_id, "dev_abc-123")
        for bad in ("../x", "a b", "", "x" * 65):
            with self.subTest(bad=bad):
                with self.assertRaises(ValidationError):
                    cmd("tablet_revoke", device_id=bad)

    def test_pair_frame_is_not_a_general_command(self):
        frame = json.dumps({"type": "pair", "code": "Ab3dEf9hJk2m", "name": "Galaxy Tab S10"})
        self.assertIsInstance(parse_pair(frame), Pair)
        with self.assertRaises(ValidationError):
            parse_command(frame)  # only the tablet listener accepts it, as a first frame
        with self.assertRaises(ValidationError):
            parse_pair(json.dumps({"type": "pair", "code": "x" * 65, "name": "t"}))
        with self.assertRaises(ValidationError):
            parse_pair(json.dumps({"type": "pair", "code": "abc", "name": "<script>" * 20}))

    def test_policy_for_tablet_commands(self):
        pcm = base64.b64encode(b"\x00\x00" * 100).decode()
        self.assertIsNone(permit("remote", cmd("audio_clip", pcm_b64=pcm)))
        self.assertIsNotNone(permit("desktop", cmd("audio_clip", pcm_b64=pcm)))
        for c in (cmd("tablet_pair_start"), cmd("tablet_revoke", device_id="d1"),
                  cmd("set_config", values={"tablet_access": False}),
                  cmd("set_config", values={"tablet_port": 9000})):
            with self.subTest(c=c.type):
                self.assertIsNotNone(permit("remote", c))
                self.assertIsNone(permit("desktop", c))


class TabletConfigTest(unittest.TestCase):
    def test_defaults_off(self):
        c = Config()
        self.assertFalse(c.tablet_access)
        self.assertEqual(c.tablet_port, 8766)

    def test_desktop_can_toggle_and_port_is_validated(self):
        c = Config()
        rejected = c.apply_ui_update({"tablet_access": True, "tablet_port": 9001})
        self.assertEqual(rejected, [])
        self.assertTrue(c.tablet_access)
        self.assertEqual(c.tablet_port, 9001)
        for bad in (80, 70000, "8766", True, 1023):
            with self.subTest(bad=bad):
                self.assertEqual(Config().apply_ui_update({"tablet_port": bad}), ["tablet_port"])
        self.assertEqual(Config().apply_ui_update({"tablet_access": "yes"}), ["tablet_access"])


if __name__ == "__main__":
    unittest.main()
