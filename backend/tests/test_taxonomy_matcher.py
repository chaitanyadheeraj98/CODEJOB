import unittest

from app.taxonomy_matcher import AliasMatcher


class AliasMatcherTests(unittest.TestCase):
    def test_finds_overlapping_aliases_with_word_boundaries(self) -> None:
        matcher = AliasMatcher(["java", "java spring", "spring"])

        matches = matcher.find("java spring and javascript")

        self.assertEqual(
            [(match.alias, match.start, match.end) for match in matches],
            [("java", 0, 4), ("java spring", 0, 11), ("spring", 5, 11)],
        )

    def test_does_not_match_inside_a_larger_token(self) -> None:
        matcher = AliasMatcher(["java", "sql"])

        self.assertEqual(matcher.find("javascript nosql"), [])


if __name__ == "__main__":
    unittest.main()
