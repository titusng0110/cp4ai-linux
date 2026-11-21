import unittest

from cp4ai.core import is_ordered_subsequence_after_stripping_whitespace


class OrderedSubsequenceTests(unittest.TestCase):
    def test_whitespace_is_ignored(self):
        self.assertTrue(
            is_ordered_subsequence_after_stripping_whitespace(
                "Helloworld", "Hello world"
            )
        )
        self.assertTrue(
            is_ordered_subsequence_after_stripping_whitespace("a\u00a0b", "a b")
        )
        self.assertTrue(
            is_ordered_subsequence_after_stripping_whitespace("ac", "a b c")
        )

    def test_order_matters(self):
        self.assertFalse(
            is_ordered_subsequence_after_stripping_whitespace(
                "worldhello", "Hello world"
            )
        )

    def test_case_and_punctuation_matter(self):
        self.assertFalse(
            is_ordered_subsequence_after_stripping_whitespace(
                "hello", "HELLO"
            )
        )
        self.assertFalse(
            is_ordered_subsequence_after_stripping_whitespace(
                "hello", "Hello, world"
            )
        )

    def test_empty_candidate(self):
        self.assertTrue(
            is_ordered_subsequence_after_stripping_whitespace("", "anything")
        )


if __name__ == "__main__":
    unittest.main()
