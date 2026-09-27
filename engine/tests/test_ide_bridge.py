import unittest
from barnaby_engine.ide_bridge import IdeBridge

class TestIdeBridge(unittest.TestCase):
    def test_initialization(self):
        bridge = IdeBridge(port=8765)
        self.assertEqual(bridge.port, 8765)
        self.assertFalse(bridge.is_running)
        self.assertEqual(bridge.get_client_count(), 0)
    
    def test_get_client_count(self):
        bridge = IdeBridge(port=8765)
        self.assertEqual(bridge.get_client_count(), 0)
