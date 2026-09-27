import unittest
from unittest.mock import Mock, patch
from barnaby_engine.config import Config
from barnaby_engine.wake_word import WakeWordDetector
from barnaby_engine.continuous import ContinuousMode
from barnaby_engine.command_chain import CommandChain
from barnaby_engine.ide_bridge import IdeBridge
from barnaby_engine.workflows import WorkflowEngine
from barnaby_engine.macro_recorder import MacroRecorder
from barnaby_engine.templates import TemplateManager

class TestSubsystemIntegration(unittest.TestCase):
    def test_subsystems_initialized(self):
        """Test that all subsystems can be initialized with config."""
        config = Config()
        
        wake_word = WakeWordDetector(
            keyword=config.wake_word_keyword,
            sensitivity=config.wake_word_sensitivity,
        )
        self.assertIsNotNone(wake_word)
        
        continuous = ContinuousMode(timeout=config.continuous_mode_timeout)
        self.assertIsNotNone(continuous)
        
        chain = CommandChain(max_depth=config.command_chain_max_depth)
        self.assertIsNotNone(chain)
        
        ide_bridge = IdeBridge(port=config.ide_bridge_port)
        self.assertIsNotNone(ide_bridge)
        
        workflow_engine = WorkflowEngine(workflows_dir=config.workflows_directory)
        self.assertIsNotNone(workflow_engine)
        
        macro_recorder = MacroRecorder()
        self.assertIsNotNone(macro_recorder)
        
        template_manager = TemplateManager()
        self.assertIsNotNone(template_manager)
