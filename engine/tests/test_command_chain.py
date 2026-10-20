import unittest
from barnaby_engine.command_chain import CommandChain, split_chain

class TestCommandChain(unittest.TestCase):
    def test_initialization(self):
        chain = CommandChain(max_depth=5)
        self.assertFalse(chain.is_active)
        self.assertEqual(chain.max_depth, 5)
        self.assertEqual(chain.depth, 0)

    def test_start_end_chain(self):
        chain = CommandChain(max_depth=5)
        chain.start_chain()
        self.assertTrue(chain.is_active)
        chain.end_chain(success=True)
        self.assertFalse(chain.is_active)

    def test_add_command(self):
        chain = CommandChain(max_depth=5)
        chain.start_chain()
        chain.add_command("fix the bug")
        self.assertEqual(chain.depth, 1)
        chain.add_command("run tests")
        self.assertEqual(chain.depth, 2)
        chain.end_chain(success=True)

    def test_max_depth_limit(self):
        chain = CommandChain(max_depth=3)
        chain.start_chain()
        chain.add_command("cmd1")
        chain.add_command("cmd2")
        chain.add_command("cmd3")
        # Fourth command should be rejected
        result = chain.add_command("cmd4")
        self.assertFalse(result)
        self.assertEqual(chain.depth, 3)
        chain.end_chain(success=True)

    def test_chain_context(self):
        chain = CommandChain(max_depth=5)
        chain.start_chain()
        chain.add_command("fix the file")
        self.assertIn("fix the file", chain.get_context())
        chain.end_chain(success=True)

    def test_chain_state_reset(self):
        chain = CommandChain(max_depth=2)
        chain.start_chain()
        chain.add_command("cmd1")
        chain.add_command("cmd2")
        # Should not be able to add more at max depth
        self.assertFalse(chain.can_add_command())
        # End the chain
        chain.end_chain(success=True)
        # Verify depth is reset
        self.assertEqual(chain.depth, 0)
        # Can start a new chain
        chain.start_chain()
        self.assertTrue(chain.is_active)
        chain.end_chain(success=True)


class TestSplitChain(unittest.TestCase):
    def test_splits_on_explicit_sequencing(self):
        cases = {
            "run the tests, then fix anything failing": ["Run the tests", "Fix anything failing"],
            "run the tests and then fix anything failing": ["Run the tests", "Fix anything failing"],
            "run the tests then fix anything failing": ["Run the tests", "Fix anything failing"],
            "first add a test, then implement it, and finally run the suite":
                ["Add a test", "Implement it", "Run the suite"],
            "Run the tests. Then fix what fails. After that commit the changes.":
                ["Run the tests", "Fix what fails", "Commit the changes"],
            "refactor the cart, next, add tests": ["Refactor the cart", "Add tests"],
            "check the diff; then commit": ["Check the diff", "Commit"],
            "run the migration then if it fails roll back": ["Run the migration", "If it fails roll back"],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(split_chain(text), expected)

    def test_never_splits_single_instructions(self):
        for text in ("if the tests fail then revert the change",
                     "when it builds, then deploy to staging",
                     "open the next page component",
                     "go to the next step",
                     "explain what `a; then b` does",
                     "then run lint",
                     "and then?"):
            with self.subTest(text=text):
                self.assertEqual(len(split_chain(text)), 1)

    def test_empty(self):
        self.assertEqual(split_chain("   "), [])
