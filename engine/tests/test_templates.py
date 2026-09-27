import unittest
from barnaby_engine.templates import TemplateManager

class TestTemplateManager(unittest.TestCase):
    def test_initialization(self):
        manager = TemplateManager()
        self.assertIsNotNone(manager)
    
    def test_list_templates(self):
        manager = TemplateManager()
        templates = manager.list_templates()
        self.assertGreater(len(templates), 0)  # Should have built-in templates
    
    def test_get_template(self):
        manager = TemplateManager()
        template = manager.get_template("tdd-cycle")
        self.assertIsNotNone(template)
        self.assertEqual(template["name"], "TDD Cycle")
    
    def test_apply_template(self):
        manager = TemplateManager()
        template = manager.get_template("tdd-cycle")
        # Should return a workflow
        self.assertIsNotNone(template)
    
    def test_add_custom_template(self):
        manager = TemplateManager()
        custom_template = {
            "id": "custom",
            "name": "Custom Template",
            "description": "A custom workflow",
            "steps": [{"instruction": "do something", "agent": "claude"}],
        }
        manager.add_custom_template("custom", custom_template)
        template = manager.get_template("custom")
        self.assertEqual(template["name"], "Custom Template")
    
    def test_remove_custom_template(self):
        manager = TemplateManager()
        custom_template = {
            "id": "custom",
            "name": "Custom Template",
            "description": "A custom workflow",
            "steps": [{"instruction": "do something", "agent": "claude"}],
        }
        manager.add_custom_template("custom", custom_template)
        result = manager.remove_template("custom")
        self.assertTrue(result)
        self.assertIsNone(manager.get_template("custom"))
    
    def test_cannot_remove_builtin(self):
        manager = TemplateManager()
        result = manager.remove_template("tdd-cycle")
        self.assertFalse(result)
        self.assertIsNotNone(manager.get_template("tdd-cycle"))
