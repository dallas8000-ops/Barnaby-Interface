import uuid
from typing import List, Optional

class CommandChain:
    """
    Command chaining system for executing multiple voice instructions sequentially.
    
    Maintains context across chained commands and enforces depth limits.
    """
    
    def __init__(self, max_depth: int = 5):
        self.max_depth = max_depth
        self.is_active = False
        self.depth = 0
        self.chain_id: Optional[str] = None
        self.commands: List[str] = []
    
    def start_chain(self) -> str:
        """
        Start a new command chain.
        
        Returns:
            The chain ID for this chain
        """
        if self.is_active:
            self.end_chain(success=False)  # End previous chain
        
        self.is_active = True
        self.depth = 0
        self.chain_id = str(uuid.uuid4())
        self.commands = []
        return self.chain_id
    
    def add_command(self, command: str) -> bool:
        """
        Add a command to the current chain.
        
        Args:
            command: The voice command text
            
        Returns:
            True if command was added, False if chain is inactive or at max depth
        """
        if not self.is_active:
            return False
        
        if self.depth >= self.max_depth:
            return False
        
        self.commands.append(command)
        self.depth += 1
        return True
    
    def end_chain(self, success: bool = True) -> None:
        """
        End the current command chain.
        
        Args:
            success: Whether the chain completed successfully
        """
        self.is_active = False
        # Keep chain_id and commands for history, but reset depth
        self.depth = 0
    
    def get_context(self) -> str:
        """
        Get the context string from all commands in the chain.
        
        Returns:
            Concatenated command history
        """
        return " ".join(self.commands)
    
    def get_chain_id(self) -> Optional[str]:
        """Get the current chain ID."""
        return self.chain_id
    
    def can_add_command(self) -> bool:
        """Check if another command can be added to the chain."""
        return self.is_active and self.depth < self.max_depth
