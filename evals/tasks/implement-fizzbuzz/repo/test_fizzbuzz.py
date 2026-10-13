import unittest

from fizzbuzz import fizzbuzz


class T(unittest.TestCase):
    def test_values(self):
        self.assertEqual(fizzbuzz(3), 'Fizz')
        self.assertEqual(fizzbuzz(5), 'Buzz')
        self.assertEqual(fizzbuzz(15), 'FizzBuzz')
        self.assertEqual(fizzbuzz(7), '7')
