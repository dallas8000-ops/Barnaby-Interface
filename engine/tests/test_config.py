import unittest
from barnaby_engine.config import Config, validate_config

class TestConfigValidation(unittest.TestCase):
    def test_valid_config(self):
        config = Config()
        errors = validate_config(config)
        self.assertEqual(errors, [])
    
    def test_invalid_wake_word_sensitivity(self):
        config = Config(wake_word_sensitivity=1.5)
        errors = validate_config(config)
        self.assertIn("wake_word_sensitivity", errors[0])
    
    def test_invalid_continuous_timeout(self):
        config = Config(continuous_mode_timeout=0.1)
        errors = validate_config(config)
        self.assertIn("continuous_mode_timeout", errors[0])
    
    def test_invalid_chain_depth(self):
        config = Config(command_chain_max_depth=25)
        errors = validate_config(config)
        self.assertIn("command_chain_max_depth", errors[0])
    
    def test_invalid_port(self):
        config = Config(ide_bridge_port=100)
        errors = validate_config(config)
        self.assertIn("ide_bridge_port", errors[0])
