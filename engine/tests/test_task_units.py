"""Task board pure logic: lane validation, overlap, scheduling order and limits,
and the voice grammar for assigning tasks."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

from barnaby_engine.tasks import (LaneError, TaskRecord, lanes_overlap, parse_task_command, pick_next,
                                  resolve_lane_words, validate_lanes)


class _Project(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name) / "proj"
        for d in ("engine/barnaby_engine", "app/src", "api", "docs"):
            (self.ws / d).mkdir(parents=True)
        (self.ws / "README.md").write_text("x")

    def tearDown(self):
        self._tmp.cleanup()


class ValidateLanesTest(_Project):
    def test_normalizes_separators_dots_and_slashes(self):
        got = validate_lanes(str(self.ws), ["./engine/", "app\\src", " api "])
        self.assertEqual(got, ("engine", "app/src", "api"))

    def test_empty_means_whole_project(self):
        self.assertEqual(validate_lanes(str(self.ws), []), ())

    def test_drops_duplicates_and_lanes_inside_other_lanes(self):
        got = validate_lanes(str(self.ws), ["engine/barnaby_engine", "engine", "engine/"])
        self.assertEqual(got, ("engine",))

    def test_rejects_escapes_globs_and_absolute_paths(self):
        bad = ["..", "../other", "engine/../..", "/etc", "C:/Windows", "C:\\Windows", "eng*", "app/**",
               "a?b", "a[b]", "a{b}", "a(b)", "!engine", "~/x", "a\nb", "a\x00b", "$HOME", "`x`", "\"x\""]
        for lane in bad:
            with self.subTest(lane=lane), self.assertRaises(LaneError):
                validate_lanes(str(self.ws), [lane])

    def test_rejects_missing_folder_file_and_project_root(self):
        for lane in ("nope", "README.md", ".", "./", "engine/.."):
            with self.subTest(lane=lane), self.assertRaises(LaneError):
                validate_lanes(str(self.ws), [lane])

    def test_limits_count_and_length(self):
        with self.assertRaises(LaneError):
            validate_lanes(str(self.ws), ["api"] * 3 + [f"x{i}" for i in range(9)])
        with self.assertRaises(LaneError):
            validate_lanes(str(self.ws), ["a" * 201])

    @unittest.skipIf(sys.platform == "win32", "symlinks need admin on Windows")
    def test_link_outside_project_is_refused(self):
        outside = Path(self._tmp.name) / "outside"
        outside.mkdir()
        os.symlink(outside, self.ws / "sneaky")
        with self.assertRaises(LaneError):
            validate_lanes(str(self.ws), ["sneaky"])

    def test_error_message_names_the_lane(self):
        with self.assertRaises(LaneError) as ctx:
            validate_lanes(str(self.ws), ["nope"])
        self.assertIn("nope", str(ctx.exception))


class OverlapTest(unittest.TestCase):
    def test_overlap_rules(self):
        cases = [
            ((), ("api",), True),                    # whole project overlaps everything
            (("api",), ("api",), True),
            (("engine",), ("engine/x",), True),
            (("engine/x",), ("engine",), True),
            (("engine",), ("engine2",), False),      # prefix of the name only, not the path
            (("app", "api"), ("docs",), False),
            (("app", "api"), ("docs", "api/v1"), True),
            (("App",), ("app",), True),              # Windows paths are case-insensitive
        ]
        for a, b, want in cases:
            with self.subTest(a=a, b=b):
                self.assertEqual(lanes_overlap(a, b), want)


def rec(tid, engine="claude", lanes=(), status="queued", ws="/p"):
    return TaskRecord(id=tid, workspace=ws, engine=engine, title=tid, instruction="x",
                      lanes=tuple(lanes), status=status)


class PickNextTest(unittest.TestCase):
    def ids(self, tasks, limit=3):
        return [t.id for t in pick_next(tasks, limit)]

    def test_starts_in_order_up_to_the_limit(self):
        tasks = [rec("a", lanes=("app",)), rec("b", lanes=("api",)), rec("c", lanes=("docs",)),
                 rec("d", lanes=("engine",))]
        self.assertEqual(self.ids(tasks), ["a", "b", "c"])
        self.assertEqual(self.ids(tasks, limit=1), ["a"])

    def test_running_tasks_use_slots(self):
        tasks = [rec("r1", lanes=("app",), status="running"), rec("r2", lanes=("api",), status="running"),
                 rec("q", lanes=("docs",)), rec("q2", lanes=("engine",))]
        self.assertEqual(self.ids(tasks), ["q"])

    def test_overlapping_lane_waits_and_later_free_task_goes_first(self):
        tasks = [rec("r", lanes=("engine",), status="running"), rec("blocked", lanes=("engine/x",)),
                 rec("free", lanes=("app",))]
        self.assertEqual(self.ids(tasks), ["free"])

    def test_two_queued_tasks_on_the_same_lane_start_one_at_a_time_in_order(self):
        tasks = [rec("first", lanes=("api",)), rec("second", lanes=("api",))]
        self.assertEqual(self.ids(tasks), ["first"])

    def test_waiting_for_approval_holds_lanes_but_not_a_slot(self):
        tasks = [rec("w", lanes=("api",), status="needs_approval"), rec("same", lanes=("api",)),
                 rec("a", lanes=("app",)), rec("d", lanes=("docs",)), rec("e", lanes=("engine",))]
        self.assertEqual(self.ids(tasks), ["a", "d", "e"])

    def test_whole_project_task_needs_every_local_lane_free(self):
        self.assertEqual(self.ids([rec("r", lanes=("api",), status="running"), rec("all")]), [])
        self.assertEqual(self.ids([rec("all"), rec("x", lanes=("api",))]), ["all"])

    def test_devin_holds_no_local_lanes_but_uses_a_slot(self):
        tasks = [rec("r", lanes=("api",), status="running"), rec("dv", engine="devin", lanes=("api",)),
                 rec("dv2", engine="devin"), rec("c", lanes=("app",))]
        self.assertEqual(self.ids(tasks, limit=3), ["dv", "dv2"])
        tasks = [rec("dv", engine="devin", lanes=("api",), status="running"), rec("c", lanes=("api",))]
        self.assertEqual(self.ids(tasks), ["c"])

    def test_different_projects_never_block_each_other(self):
        tasks = [rec("r", lanes=("api",), status="running", ws="/one"), rec("q", lanes=("api",), ws="/two")]
        self.assertEqual(self.ids(tasks), ["q"])

    def test_finished_tasks_are_ignored(self):
        tasks = [rec(s, lanes=("api",), status=s) for s in ("done", "failed", "cancelled", "interrupted")]
        tasks.append(rec("q", lanes=("api",)))
        self.assertEqual(self.ids(tasks, limit=1), ["q"])


class VoiceGrammarTest(unittest.TestCase):
    def test_task_for_engine_in_lanes(self):
        cases = {
            "task for Devin in backend: add the orders endpoint": ("devin", ["backend"], "add the orders endpoint"),
            "New task for claude in frontend and api, fix the login form":
                ("claude", ["frontend", "api"], "fix the login form"),
            "start a task for cursor on the app folder: tidy styles": ("cursor", ["app"], "tidy styles"),
            "task for devon: write the README": ("devin", [], "write the README"),
            "Assign the backend to Claude: add caching": ("claude", ["backend"], "add caching"),
            "assign backend, api & docs to cursor - rename things": ("cursor", ["backend", "api", "docs"],
                                                                     "rename things"),
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_task_command(text), want)

    def test_ordinary_instructions_are_not_tasks(self):
        for text in ("add a task list to the page", "Devin, fix the build", "task: do it",
                     "task for bob in api: x", "assign the ticket to Claude", "task for claude in api:"):
            with self.subTest(text=text):
                self.assertIsNone(parse_task_command(text))


class ResolveLaneWordsTest(_Project):
    def test_saved_names_then_top_level_folders_then_paths(self):
        saved = {"Backend": ["engine"], "frontend": ["app/src"]}
        self.assertEqual(resolve_lane_words(str(self.ws), ["backend", "API", "docs"], saved),
                         ["engine", "api", "docs"])
        self.assertEqual(resolve_lane_words(str(self.ws), ["frontend"], saved), ["app/src"])
        self.assertEqual(resolve_lane_words(str(self.ws), ["engine/barnaby_engine"], {}),
                         ["engine/barnaby_engine"])

    def test_unknown_word_is_an_error_listing_choices(self):
        with self.assertRaises(LaneError) as ctx:
            resolve_lane_words(str(self.ws), ["billing"], {"backend": ["engine"]})
        self.assertIn("billing", str(ctx.exception))
        self.assertIn("backend", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
