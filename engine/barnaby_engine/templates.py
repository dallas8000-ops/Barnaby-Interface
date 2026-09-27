from typing import List, Dict, Any, Optional

class TemplateManager:
    """
    Pre-built task templates for common development patterns.
    """
    
    BUILTIN_TEMPLATES = {
        "tdd-cycle": {
            "id": "tdd-cycle",
            "name": "TDD Cycle",
            "description": "Test-driven development workflow",
            "steps": [
                {"instruction": "Write a failing test for the feature", "agent": "claude"},
                {"instruction": "Implement the minimal code to make the test pass", "agent": "claude"},
                {"instruction": "Run the tests to verify they pass", "agent": "claude"},
                {"instruction": "Refactor the code if needed", "agent": "claude"},
            ],
        },
        "debugging": {
            "id": "debugging",
            "name": "Debugging Workflow",
            "description": "Systematic debugging process",
            "steps": [
                {"instruction": "Add logging to understand the issue", "agent": "claude"},
                {"instruction": "Reproduce the bug with the logging in place", "agent": "claude"},
                {"instruction": "Analyze the logs to identify the root cause", "agent": "claude"},
                {"instruction": "Fix the identified issue", "agent": "claude"},
                {"instruction": "Verify the fix resolves the bug", "agent": "claude"},
            ],
        },
        "code-review": {
            "id": "code-review",
            "name": "Code Review Checklist",
            "description": "Comprehensive code review workflow",
            "steps": [
                {"instruction": "Check code style and formatting", "agent": "claude"},
                {"instruction": "Review for security vulnerabilities", "agent": "claude"},
                {"instruction": "Check for performance issues", "agent": "claude"},
                {"instruction": "Verify test coverage", "agent": "claude"},
                {"instruction": "Review documentation completeness", "agent": "claude"},
            ],
        },
    }
    
    def __init__(self):
        self._templates = self.BUILTIN_TEMPLATES.copy()
    
    def list_templates(self) -> List[Dict[str, Any]]:
        """
        List all available templates.
        
        Returns:
            List of template dictionaries
        """
        return list(self._templates.values())
    
    def get_template(self, template_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific template by ID.
        
        Args:
            template_id: The template ID
            
        Returns:
            The template dictionary, or None if not found
        """
        return self._templates.get(template_id)
    
    def add_custom_template(self, template_id: str, template: Dict[str, Any]) -> None:
        """
        Add a custom template.
        
        Args:
            template_id: The template ID
            template: The template definition
        """
        self._templates[template_id] = template
    
    def remove_template(self, template_id: str) -> bool:
        """
        Remove a template.
        
        Args:
            template_id: The template ID to remove
            
        Returns:
            True if removed, False if not found
        """
        if template_id in self._templates and template_id not in self.BUILTIN_TEMPLATES:
            del self._templates[template_id]
            return True
        return False
