import unittest
import json
import tempfile
import os
from barnaby_engine.workflows import WorkflowEngine, Workflow

class TestWorkflowEngine(unittest.TestCase):
    def test_initialization(self):
        engine = WorkflowEngine(workflows_dir=tempfile.mkdtemp())
        self.assertIsNotNone(engine)
    
    def test_create_workflow(self):
        workflow = Workflow(
            id="test-1",
            name="Test Workflow",
            steps=[
                {"instruction": "write test", "agent": "claude"},
                {"instruction": "implement", "agent": "claude"},
            ]
        )
        self.assertEqual(workflow.name, "Test Workflow")
        self.assertEqual(len(workflow.steps), 2)
    
    def test_execute_workflow(self):
        engine = WorkflowEngine(workflows_dir=tempfile.mkdtemp())
        workflow = Workflow(
            id="test-1",
            name="Test Workflow",
            steps=[{"instruction": "echo hello", "agent": "claude"}]
        )
        # In real test, would mock agent execution
        # engine.execute_workflow(workflow)
    
    def test_save_load_workflow(self):
        temp_dir = tempfile.mkdtemp()
        engine = WorkflowEngine(workflows_dir=temp_dir)
        workflow = Workflow(
            id="test-1",
            name="Test Workflow",
            steps=[{"instruction": "test", "agent": "claude"}]
        )
        engine.save_workflow(workflow)
        loaded = engine.load_workflow("test-1")
        self.assertEqual(loaded.name, "Test Workflow")
    
    def test_execute_workflow_steps(self):
        engine = WorkflowEngine(workflows_dir=tempfile.mkdtemp())
        workflow = Workflow(
            id="test-1",
            name="Test Workflow",
            steps=[
                {"instruction": "step 1", "agent": "claude"},
                {"instruction": "step 2", "agent": "claude"},
            ]
        )
        
        executed_steps = []
        def mock_callback(instruction, agent):
            executed_steps.append((instruction, agent))
            return True
        
        result = engine.execute_workflow(workflow, mock_callback)
        self.assertTrue(result)
        self.assertEqual(len(executed_steps), 2)
        self.assertEqual(executed_steps[0], ("step 1", "claude"))
    
    def test_list_workflows(self):
        temp_dir = tempfile.mkdtemp()
        engine = WorkflowEngine(workflows_dir=temp_dir)
        
        workflow1 = Workflow(id="test-1", name="Workflow 1", steps=[])
        workflow2 = Workflow(id="test-2", name="Workflow 2", steps=[])
        
        engine.save_workflow(workflow1)
        engine.save_workflow(workflow2)
        
        workflows = engine.list_workflows()
        self.assertEqual(len(workflows), 2)
