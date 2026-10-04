import unittest

from textkit.words import (
    chunk,
    is_palindrome,
    most_common,
    slugify,
    title_case,
    truncate,
    word_count,
)


class WordCountTests(unittest.TestCase):
    def test_counts_words(self):
        self.assertEqual(word_count("one two three"), 3)

    def test_runs_of_whitespace_are_one_separator(self):
        self.assertEqual(word_count("a   b\n\tc"), 3)

    def test_empty(self):
        self.assertEqual(word_count(""), 0)
        self.assertEqual(word_count("   "), 0)


class SlugifyTests(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(slugify("  Hello, World!  "), "hello-world")

    def test_runs_become_one_dash(self):
        self.assertEqual(slugify("a -- b __ c"), "a-b-c")

    def test_digits_survive(self):
        self.assertEqual(slugify("Python 3.12"), "python-3-12")


class TruncateTests(unittest.TestCase):
    def test_short_text_is_unchanged(self):
        self.assertEqual(truncate("short", 10), "short")

    def test_text_exactly_at_the_limit_is_unchanged(self):
        self.assertEqual(truncate("12345", 5), "12345")

    def test_long_text_is_cut_with_suffix(self):
        self.assertEqual(truncate("hello world", 8), "hello w…")
        self.assertLessEqual(len(truncate("hello world", 8)), 8)

    def test_trailing_space_before_the_suffix_is_dropped(self):
        self.assertEqual(truncate("hello world", 7), "hello…")

    def test_limit_shorter_than_suffix(self):
        with self.assertRaises(ValueError):
            truncate("abc", 1, suffix="...")


class TitleCaseTests(unittest.TestCase):
    def test_small_words_stay_lower_case(self):
        self.assertEqual(title_case("war and peace of the world"), "War and Peace of the World")

    def test_first_word_is_always_capitalised(self):
        self.assertEqual(title_case("the lord of the rings"), "The Lord of the Rings")

    def test_rest_of_each_word_is_lowered(self):
        self.assertEqual(title_case("hELLO wORLD"), "Hello World")


class PalindromeTests(unittest.TestCase):
    def test_ignores_case(self):
        self.assertTrue(is_palindrome("Racecar"))

    def test_ignores_punctuation_and_spaces(self):
        self.assertTrue(is_palindrome("A man, a plan, a canal: Panama"))

    def test_not_a_palindrome(self):
        self.assertFalse(is_palindrome("hello"))

    def test_empty_is_a_palindrome(self):
        self.assertTrue(is_palindrome(""))


class ChunkTests(unittest.TestCase):
    def test_last_chunk_may_be_shorter(self):
        self.assertEqual(chunk([1, 2, 3, 4, 5], 2), [[1, 2], [3, 4], [5]])

    def test_size_one(self):
        self.assertEqual(chunk([1, 2, 3], 1), [[1], [2], [3]])

    def test_empty_list(self):
        self.assertEqual(chunk([], 3), [])

    def test_size_must_be_positive(self):
        with self.assertRaises(ValueError):
            chunk([1], 0)
        with self.assertRaises(ValueError):
            chunk([1], -2)


class MostCommonTests(unittest.TestCase):
    def test_ranks_by_count(self):
        self.assertEqual(most_common("a b b c c c"), [("c", 3), ("b", 2), ("a", 1)])

    def test_ties_are_alphabetical(self):
        self.assertEqual(most_common("b a b a c", 2), [("a", 2), ("b", 2)])

    def test_case_is_ignored_and_n_limits(self):
        self.assertEqual(most_common("Go go GO stop", 1), [("go", 3)])

    def test_no_words(self):
        self.assertEqual(most_common("123 !!!"), [])


if __name__ == "__main__":
    unittest.main()
