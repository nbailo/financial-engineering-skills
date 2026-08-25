"""The README states a number. This is what keeps it true."""
import re
import unittest
from pathlib import Path

EXAMPLE_ROOT = Path(__file__).resolve().parent.parent


class ReadmeIsCurrent(unittest.TestCase):
    def test_the_stated_case_count_is_the_real_one(self):
        suite = unittest.TestLoader().discover(start_dir=str(EXAMPLE_ROOT / "tests"),
                                               top_level_dir=str(EXAMPLE_ROOT))
        readme = (EXAMPLE_ROOT / "README.md").read_text(encoding="utf-8")
        stated = re.search(r"# (\d+) cases, standard library only", readme)
        self.assertIsNotNone(stated, "the README no longer states a case count")
        self.assertEqual(int(stated.group(1)), suite.countTestCases())

    def test_every_named_test_in_the_readme_exists(self):
        readme = (EXAMPLE_ROOT / "README.md").read_text(encoding="utf-8")
        sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((EXAMPLE_ROOT / "tests").glob("test_*.py")))
        for name in sorted(set(re.findall(r"`(test_[a-z0-9_]+)`", readme))):
            self.assertIn(f"def {name}(", sources, f"the README cites a missing {name}")


if __name__ == "__main__":
    unittest.main()
