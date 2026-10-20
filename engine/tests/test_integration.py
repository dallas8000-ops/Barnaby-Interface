import unittest
import unittest.mock
from unittest.mock import Mock, patch
from barnaby_engine.config import Config, resolve_workflows_dir
from barnaby_engine.continuous import ContinuousMode
from barnaby_engine.command_chain import CommandChain
from barnaby_engine.ide_bridge import IdeBridge
from barnaby_engine.workflows import WorkflowEngine
from barnaby_engine.templates import TemplateManager
from tests.helpers import TempDataDir

class TestSubsystemIntegration(unittest.TestCase):
    def setUp(self):
        self._data = TempDataDir()
        self.data_dir = self._data.__enter__()
        self.addCleanup(self._data.__exit__)

    def test_subsystems_initialized(self):
        """Test that all subsystems can be initialized with config."""
        config = Config()
        
        continuous = ContinuousMode(timeout=config.continuous_mode_timeout)
        self.assertIsNotNone(continuous)
        
        chain = CommandChain(max_depth=config.command_chain_max_depth)
        self.assertIsNotNone(chain)
        
        ide_bridge = IdeBridge(port=config.ide_bridge_port)
        self.assertIsNotNone(ide_bridge)
        
        workflow_engine = WorkflowEngine(workflows_dir=str(resolve_workflows_dir(config.workflows_directory)))
        self.assertEqual(workflow_engine.workflows_dir, self.data_dir / "workflows")
        self.assertIsNotNone(workflow_engine)
        
        template_manager = TemplateManager()
        self.assertIsNotNone(template_manager)


class WorkflowsDirTest(unittest.TestCase):
    def setUp(self):
        self._data = TempDataDir()
        self.data_dir = self._data.__enter__()
        self.addCleanup(self._data.__exit__)

    def test_default_and_legacy_values_never_relative(self):
        import os
        from pathlib import Path
        self.assertEqual(resolve_workflows_dir(""), self.data_dir / "workflows")
        # The value earlier builds wrote to config.json must never yield a relative "%APPDATA%" folder.
        legacy = resolve_workflows_dir("%APPDATA%\\Barnaby\\workflows")
        self.assertTrue(legacy.is_absolute())
        self.assertNotIn("%", str(legacy))
        self.assertEqual(resolve_workflows_dir("relative/dir"), self.data_dir / "workflows")
        absolute = str(Path(self.data_dir, "custom").resolve())
        self.assertEqual(resolve_workflows_dir(absolute), Path(absolute))
        with unittest.mock.patch.dict(os.environ, {"BARNABY_WF_TEST": str(self.data_dir)}):
            var = "%BARNABY_WF_TEST%" if os.name == "nt" else "$BARNABY_WF_TEST"
            self.assertEqual(resolve_workflows_dir(var + os.sep + "wf"), self.data_dir / "wf")
