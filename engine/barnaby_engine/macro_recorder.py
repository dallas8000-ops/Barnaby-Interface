import uuid
from dataclasses import dataclass, field
from typing import List, Callable, Optional
from datetime import datetime

@dataclass
class MacroStep:
    """A single step in a recorded macro."""
    instruction: str
    agent: str
    success: bool
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

@dataclass
class Macro:
    """A recorded macro."""
    id: str
    name: str
    steps: List[MacroStep] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

class MacroRecorder:
    """
    Macro recording and replay system.
    
    Records sequences of Barnaby operations as reusable macros.
    """
    
    def __init__(self):
        self.is_recording = False
        self.current_macro_id: Optional[str] = None
        self.current_macro_name: Optional[str] = None
        self.recorded_steps: List[MacroStep] = []
    
    def start_recording(self, name: str) -> str:
        """
        Start recording a new macro.
        
        Args:
            name: Name for the macro
            
        Returns:
            The macro ID
        """
        if self.is_recording:
            self.stop_recording()  # End previous recording
        
        self.is_recording = True
        self.current_macro_id = str(uuid.uuid4())
        self.current_macro_name = name
        self.recorded_steps = []
        return self.current_macro_id
    
    def stop_recording(self) -> Optional[Macro]:
        """
        Stop recording and return the macro.
        
        Returns:
            The recorded macro, or None if not recording
        """
        if not self.is_recording:
            return None
        
        self.is_recording = False
        
        if not self.recorded_steps:
            return None
        
        macro = Macro(
            id=self.current_macro_id or "",
            name=self.current_macro_name or "",
            steps=self.recorded_steps.copy(),
        )
        
        self.current_macro_id = None
        self.current_macro_name = None
        self.recorded_steps = []
        
        return macro
    
    def record_step(self, instruction: str, agent: str, success: bool) -> None:
        """
        Record a step in the current macro.
        
        Args:
            instruction: The instruction that was executed
            agent: The agent that executed it
            success: Whether the step succeeded
        """
        if not self.is_recording:
            return
        
        step = MacroStep(
            instruction=instruction,
            agent=agent,
            success=success,
        )
        self.recorded_steps.append(step)
    
    def replay_macro(self, macro: Macro, callback: Callable[[str, str], bool]) -> bool:
        """
        Replay a macro.
        
        Args:
            macro: The macro to replay
            callback: Function to call for each step (instruction, agent) -> bool
            
        Returns:
            True if macro completed successfully, False otherwise
        """
        for step in macro.steps:
            success = callback(step.instruction, step.agent)
            if not success:
                return False
        
        return True
    
    def get_recording_state(self) -> dict:
        """
        Get the current recording state.
        
        Returns:
            Dictionary with recording state info
        """
        return {
            "is_recording": self.is_recording,
            "macro_id": self.current_macro_id,
            "macro_name": self.current_macro_name,
            "step_count": len(self.recorded_steps),
        }
