import unittest

from duration import parse_duration


class DurationTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_duration("1h30m"), 90)
        self.assertEqual(parse_duration("45m"), 45)
        self.assertEqual(parse_duration("2h"), 120)


if __name__ == "__main__":
    unittest.main()
