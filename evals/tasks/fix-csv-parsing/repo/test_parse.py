import unittest

from parse import parse_rows


class T(unittest.TestCase):
    def test_quoted(self):
        self.assertEqual(parse_rows('a,"b,c",d\n1,2,3'), [['a', 'b,c', 'd'], ['1', '2', '3']])
