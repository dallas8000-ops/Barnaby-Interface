"""Default-input selection. The device table mirrors a real PC whose Windows default
recording device is a Sound Blaster "What U Hear" loopback."""
import sys
import types
import unittest
from unittest import mock

from barnaby_engine.voice import MicRecorder, is_loopback, list_input_devices, pick_input_device

MME, WASAPI = 0, 2


def dev(name, hostapi=MME, inputs=2, rate=44100.0):
    return {"name": name, "hostapi": hostapi, "max_input_channels": inputs, "default_samplerate": rate}


DEVICES = [
    dev("Microsoft Sound Mapper - Input"),                        # 0
    dev('"What U Hear" (2- Sound Blaster'),                       # 1  <- Windows default
    dev("Microphone (HD Pro Webcam C920)"),                       # 2
    dev("Headset Microphone (HyperX Virt"),                       # 3
    dev("Speakers (Realtek)", inputs=0),                          # 4  output only
    dev('"What U Hear" (2- Sound Blaster Audigy 5/Rx)', WASAPI),  # 5
    dev("Microphone (HD Pro Webcam C920)", WASAPI),               # 6
    dev("Stereo Mix (Realtek USB Audio)"),                        # 7
]


class PickInputDeviceTest(unittest.TestCase):
    def test_loopback_names(self):
        for name in ('"What U Hear" (Sound Blaster)', "Stereo Mix (Realtek)", "Wave Out Mix", "WASAPI Loopback"):
            self.assertTrue(is_loopback(name), name)
        for name in ("Microphone (HD Pro Webcam C920)", "Headset Microphone (HyperX)", "Line In"):
            self.assertFalse(is_loopback(name), name)

    def test_real_default_is_kept(self):
        self.assertEqual(pick_input_device(DEVICES, 3), 3)

    def test_loopback_default_swaps_to_first_mic_on_same_api(self):
        self.assertEqual(pick_input_device(DEVICES, 1), 2)
        self.assertEqual(pick_input_device(DEVICES, 5), 6)  # stays on WASAPI

    def test_default_aliases_are_not_a_way_out(self):
        # Sound Mapper / Primary Sound Capture forward to the loopback default.
        devices = [dev("Microsoft Sound Mapper - Input"), dev("Stereo Mix"),
                   dev("Primary Sound Capture Driver"), dev("Line In")]
        self.assertEqual(pick_input_device(devices, 1), 3)

    def test_prefers_a_mic_over_other_real_inputs(self):
        devices = [dev("Stereo Mix"), dev("Line In"), dev("Microphone Array")]
        self.assertEqual(pick_input_device(devices, 0), 2)

    def test_falls_back_to_any_real_input_then_to_the_default(self):
        self.assertEqual(pick_input_device([dev("Stereo Mix"), dev("Line In")], 0), 1)
        self.assertEqual(pick_input_device([dev("Stereo Mix")], 0), 0)  # nothing better: don't go silent

    def test_no_or_invalid_default_leaves_it_to_portaudio(self):
        self.assertIsNone(pick_input_device(DEVICES, None))
        self.assertIsNone(pick_input_device(DEVICES, 4))   # output-only device
        self.assertIsNone(pick_input_device(DEVICES, 99))


def fake_sounddevice(default_in=1):
    sd = types.ModuleType("sounddevice")
    sd.default = types.SimpleNamespace(device=(default_in, 4))
    sd.query_devices = lambda device=None, kind=None: list(DEVICES) if device is None else DEVICES[device]
    sd.opened = []

    class InputStream:
        def __init__(self, **kw):
            sd.opened.append(kw)

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    sd.InputStream = InputStream
    return sd


class MicRecorderDeviceTest(unittest.TestCase):
    def test_unset_device_opens_the_real_mic_not_the_loopback(self):
        sd = fake_sounddevice(default_in=1)
        with mock.patch.dict(sys.modules, {"sounddevice": sd}), self.assertLogs("barnaby_engine.voice", "WARNING") as logs:
            MicRecorder(None).start_stream(lambda _b: None)
            MicRecorder_ = MicRecorder(None)
            MicRecorder_.start_stream(lambda _b: None)
            MicRecorder_.start_stream(lambda _b: None)
        self.assertEqual([o["device"] for o in sd.opened], [2, 2, 2])
        self.assertEqual(len(logs.records), 2)  # once per recorder, not per stream open
        self.assertIn("What U Hear", logs.output[0])

    def test_explicit_choice_is_respected_even_if_loopback(self):
        sd = fake_sounddevice(default_in=3)
        with mock.patch.dict(sys.modules, {"sounddevice": sd}):
            MicRecorder(7).start_stream(lambda _b: None)
        self.assertEqual(sd.opened[0]["device"], 7)

    def test_device_list_marks_default_auto_and_loopback(self):
        with mock.patch.dict(sys.modules, {"sounddevice": fake_sounddevice(default_in=1)}):
            items = {d["index"]: d for d in list_input_devices()}
        self.assertNotIn(4, items)
        self.assertTrue(items[1]["default"] and items[1]["loopback"] and not items[1]["auto"])
        self.assertTrue(items[2]["auto"] and not items[2]["loopback"])
        self.assertEqual(sum(d["auto"] for d in items.values()), 1)


if __name__ == "__main__":
    unittest.main()
