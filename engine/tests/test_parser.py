import unittest

from barnaby_engine.agent import (SessionStarted, StreamParser, TextDelta, ToolEnd,
                                  ToolStart, TurnResult)
from tests.helpers import load_fixture


class StreamParserRealOutputTest(unittest.TestCase):
    """Fixture is trimmed from a real `claude -p --output-format stream-json` run
    where Bash was denied, so these assertions pin the actual wire format."""

    def setUp(self):
        parser = StreamParser()
        self.events = [e for obj in load_fixture("denied_bash.jsonl") for e in parser.feed(obj)]

    def test_session_started_first(self):
        self.assertIsInstance(self.events[0], SessionStarted)
        self.assertEqual(self.events[0].session_id, "0cb78c70-3072-55f8-ad03-91da1569e1a9")

    def test_tool_start_has_complete_input_once(self):
        starts = [e for e in self.events if isinstance(e, ToolStart)]
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0].name, "Bash")
        self.assertEqual(starts[0].input["command"], "echo hello > x.txt")

    def test_tool_end_reports_failure(self):
        ends = [e for e in self.events if isinstance(e, ToolEnd)]
        self.assertEqual(len(ends), 1)
        self.assertFalse(ends[0].ok)
        self.assertIn("needs approval", ends[0].preview)

    def test_streamed_text_not_duplicated_by_aggregate_message(self):
        text = "".join(e.text for e in self.events if isinstance(e, TextDelta))
        self.assertEqual(text.count("sandbox blocked"), 1)

    def test_result_carries_denials(self):
        result = self.events[-1]
        self.assertIsInstance(result, TurnResult)
        self.assertFalse(result.is_error)
        self.assertEqual(len(result.denials), 1)
        self.assertEqual(result.denials[0].tool_name, "Bash")
        self.assertEqual(result.denials[0].tool_input["command"], "echo hello > x.txt")


class StreamParserEdgeCasesTest(unittest.TestCase):
    def test_non_streaming_mode_emits_aggregate_text(self):
        p = StreamParser()
        out = p.feed({"type": "assistant", "message": {"id": "m1", "content": [{"type": "text", "text": "Hi."}]}})
        self.assertEqual(out, [TextDelta("Hi.")])

    def test_separate_messages_get_paragraph_break(self):
        p = StreamParser()
        out = []
        for mid, text in (("m1", "I'll run the tests first."), ("m2", "All pass.")):
            out += p.feed({"type": "stream_event", "event": {"type": "message_start", "message": {"id": mid}}})
            out += p.feed({"type": "stream_event", "event": {"type": "content_block_delta",
                                                             "delta": {"type": "text_delta", "text": text[:5]}}})
            out += p.feed({"type": "stream_event", "event": {"type": "content_block_delta",
                                                             "delta": {"type": "text_delta", "text": text[5:]}}})
        self.assertEqual("".join(e.text for e in out), "I'll run the tests first.\n\nAll pass.")

    def test_subagent_traffic_ignored(self):
        p = StreamParser()
        self.assertEqual(p.feed({"type": "assistant", "parent_tool_use_id": "t0",
                                 "message": {"id": "m", "content": [{"type": "text", "text": "inner"}]}}), [])
        self.assertEqual(p.feed({"type": "stream_event", "parent_tool_use_id": "t0",
                                 "event": {"type": "content_block_delta",
                                           "delta": {"type": "text_delta", "text": "x"}}}), [])

    def test_error_result(self):
        out = StreamParser().feed({"type": "result", "subtype": "error_max_turns", "is_error": True,
                                   "session_id": None, "result": ""})
        self.assertTrue(out[0].is_error)

    def test_tool_result_list_content_and_truncation(self):
        out = StreamParser().feed({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t", "content": [{"type": "text", "text": "y" * 2000}]}]}})
        self.assertTrue(out[0].ok)
        self.assertLessEqual(len(out[0].preview), 601)

    def test_malformed_shapes_do_not_raise(self):
        p = StreamParser()
        for obj in ({}, {"type": "assistant"}, {"type": "user", "message": {"content": ["str"]}},
                    {"type": "result", "permission_denials": ["bad"]}, {"type": "stream_event", "event": None}):
            p.feed(obj)


if __name__ == "__main__":
    unittest.main()
