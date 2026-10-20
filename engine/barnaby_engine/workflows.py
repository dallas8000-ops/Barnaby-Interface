import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from pathlib import Path

@dataclass
class WorkflowStep:
    """A single step in a workflow."""
    instruction: str
    agent: str = "claude"
    approval_required: bool = False
    timeout: int = 30  # seconds

@dataclass
class Workflow:
    """A workflow definition."""
    id: str
    name: str
    steps: List[Dict[str, Any]] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert workflow to dictionary for serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "steps": self.steps,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Workflow":
        """Create workflow from dictionary."""
        return cls(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            steps=data.get("steps", []),
        )

class WorkflowEngine:
    """
    Workflow execution engine.

    Loads, saves, and executes workflow definitions.
    """

    def __init__(self, workflows_dir: str):
        self.workflows_dir = Path(workflows_dir)
        self.workflows_dir.mkdir(parents=True, exist_ok=True)
        self._active_workflows: Dict[str, Workflow] = {}

    def save_workflow(self, workflow: Workflow) -> None:
        """
        Save a workflow to disk.

        Args:
            workflow: The workflow to save
        """
        workflow_path = self.workflows_dir / f"{workflow.id}.json"
        with open(workflow_path, "w") as f:
            json.dump(workflow.to_dict(), f, indent=2)

    def load_workflow(self, workflow_id: str) -> Optional[Workflow]:
        """
        Load a workflow from disk.

        Args:
            workflow_id: The workflow ID to load

        Returns:
            The loaded workflow, or None if not found
        """
        workflow_path = self.workflows_dir / f"{workflow_id}.json"
        if not workflow_path.exists():
            return None

        with open(workflow_path, "r") as f:
            data = json.load(f)
            return Workflow.from_dict(data)

    def list_workflows(self) -> List[Workflow]:
        """
        List all available workflows.

        Returns:
            List of workflow objects
        """
        workflows = []
        for workflow_file in sorted(self.workflows_dir.glob("*.json")):
            try:
                with open(workflow_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                workflows.append(Workflow.from_dict(data))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                # A hand-edited file with a typo must not hide every other workflow.
                logging.getLogger(__name__).warning("skipping unreadable workflow %s: %s", workflow_file.name, exc)
        return workflows

    def execute_workflow(self, workflow: Workflow, agent_callback) -> bool:
        """
        Execute a workflow step by step.

        Args:
            workflow: The workflow to execute
            agent_callback: Function to call for each step (instruction, agent) -> bool

        Returns:
            True if workflow completed successfully, False otherwise
        """
        for step_data in workflow.steps:
            step = WorkflowStep(**step_data)

            # Call agent to execute this step
            success = agent_callback(step.instruction, step.agent)

            if not success:
                return False

        return True

    def create_workflow(self, name: str, steps: List[Dict[str, Any]], description: str = "") -> Workflow:
        """
        Create a new workflow.

        Args:
            name: Workflow name
            steps: List of step definitions
            description: Workflow description

        Returns:
            The created workflow
        """
        workflow_id = str(uuid.uuid4())
        workflow = Workflow(
            id=workflow_id,
            name=name,
            steps=steps,
            description=description,
        )
        self.save_workflow(workflow)
        return workflow
