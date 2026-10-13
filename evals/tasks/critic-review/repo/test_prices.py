import unittest

from prices import discount, with_tax


class PricesTest(unittest.TestCase):
    def test_discount(self):
        self.assertAlmostEqual(discount(100), 90)

    def test_with_tax(self):
        self.assertAlmostEqual(with_tax(100), 120)


if __name__ == "__main__":
    unittest.main()
