"""The shared state (JSON in the pb-state group) is written by two programs, the scheduler (bin/nay) and the
parent page (web/app.js). Both normalise it with parse_state, and a value one of them does not know is lost the next
time it writes. tests/fixtures/state-cases.json is the single list of cases; tests/state-contract.test.js runs the
same file against the page's parser."""
import importlib.machinery
import importlib.util
import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
loader = importlib.machinery.SourceFileLoader("nay_cli", os.path.join(ROOT, "bin", "nay"))
spec = importlib.util.spec_from_loader("nay_cli", loader)
nay = importlib.util.module_from_spec(spec)
loader.exec_module(nay)

with open(os.path.join(ROOT, "tests", "fixtures", "state-cases.json"), encoding="utf-8") as fh:
    CASES = json.load(fh)["cases"]


class StateContract(unittest.TestCase):
    def test_every_case(self):
        for case in CASES:
            with self.subTest(case["name"]):
                self.assertEqual(nay.parse_state(case["input"]), case["expected"])

    def test_normalising_twice_changes_nothing(self):
        for case in CASES:
            with self.subTest(case["name"]):
                once = nay.parse_state(case["input"])
                self.assertEqual(nay.parse_state(json.dumps(once)), once)

    def test_the_default_state_is_what_an_empty_description_gives(self):
        self.assertEqual(nay.parse_state(""), nay.DEFAULT_STATE)


if __name__ == "__main__":
    unittest.main()
