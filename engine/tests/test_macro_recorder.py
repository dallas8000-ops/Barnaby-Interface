import unittest
from barnaby_engine.macro_recorder import MacroRecorder

class TestMacroRecorder(unittest.TestCase):
    def test_initialization(self):
        recorder = MacroRecorder()
        self.assertFalse(recorder.is_recording)
        self.assertEqual(len(recorder.recorded_steps), 0)
    
    def test_start_stop_recording(self):
        recorder = MacroRecorder()
        recorder.start_recording("test-macro")
        self.assertTrue(recorder.is_recording)
        recorder.stop_recording()
        self.assertFalse(recorder.is_recording)
    
    def test_record_step(self):
        recorder = MacroRecorder()
        recorder.start_recording("test-macro")
        recorder.record_step("fix the bug", "claude", True)
        self.assertEqual(len(recorder.recorded_steps), 1)
        recorder.stop_recording()
    
    def test_replay_macro(self):
        recorder = MacroRecorder()
        recorder.start_recording("test-macro")
        recorder.record_step("step 1", "claude", True)
        recorder.record_step("step 2", "claude", True)
        macro = recorder.stop_recording()
        
        executed = []
        def callback(instruction, agent):
            executed.append(instruction)
            return True
        
        recorder.replay_macro(macro, callback)
        self.assertEqual(len(executed), 2)
    
    def test_replay_with_failure(self):
        recorder = MacroRecorder()
        recorder.start_recording("test-macro")
        recorder.record_step("step 1", "claude", True)
        recorder.record_step("step 2", "claude", True)
        macro = recorder.stop_recording()
        
        executed = []
        def callback(instruction, agent):
            executed.append(instruction)
            return instruction == "step 1"  # Fail on step 2
        
        result = recorder.replay_macro(macro, callback)
        self.assertFalse(result)
        self.assertEqual(len(executed), 2)  # Both steps attempted
    
    def test_get_recording_state(self):
        recorder = MacroRecorder()
        state = recorder.get_recording_state()
        self.assertFalse(state["is_recording"])
        
        recorder.start_recording("test")
        recorder.record_step("step 1", "claude", True)
        state = recorder.get_recording_state()
        self.assertTrue(state["is_recording"])
        self.assertEqual(state["step_count"], 1)
        recorder.stop_recording()
