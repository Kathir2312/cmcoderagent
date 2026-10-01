import unittest

from text_utils import shout, slugify


class TextTest(unittest.TestCase):
    def test_shout(self):
        self.assertEqual(shout("hi"), "HI!")

    def test_slugify(self):
        self.assertEqual(slugify("Hello, World!"), "hello-world")
        self.assertEqual(slugify("  Many   spaces "), "many-spaces")
        self.assertEqual(slugify("Version 2.0"), "version-2-0")


if __name__ == "__main__":
    unittest.main()
