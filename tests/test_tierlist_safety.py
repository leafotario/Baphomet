from __future__ import annotations

import unittest
from dataclasses import dataclass, field

from cogs.tierlist_wikipedia.safety import (
    SAFETY_MODE_BALANCED,
    SAFETY_MODE_STRICT_PUBLIC,
    SAFETY_VERDICT_ALLOW,
    SAFETY_VERDICT_BLOCK,
    SAFETY_VERDICT_REVIEW,
    FileMetadataSafetyFilter,
    PageSafetyFilter,
    QuerySafetyFilter,
    SafetyConfig,
    SafetyDecisionEngine,
    SafetyPatternMatcher,
    VisualSafetyFilter,
    VisualSafetyResult,
)


@dataclass(frozen=True)
class FakeMetadata:
    canonicaltitle: str = "File:Example.jpg"
    object_name: str = ""
    image_description: str = ""
    extmetadata_categories: str = ""
    restrictions: str = ""
    categories: tuple[str, ...] = field(default_factory=tuple)
    commons_pageid: int | None = 123


class TierListSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.matcher = SafetyPatternMatcher()
        self.engine = SafetyDecisionEngine()
        self.query_filter = QuerySafetyFilter(self.matcher, self.engine)
        self.page_filter = PageSafetyFilter(self.matcher, self.engine)
        self.file_filter = FileMetadataSafetyFilter(self.matcher, self.engine)
        self.visual_filter = VisualSafetyFilter(self.engine)
        self.strict = SafetyConfig(mode=SAFETY_MODE_STRICT_PUBLIC)
        self.balanced = SafetyConfig(mode=SAFETY_MODE_BALANCED)

    def test_safe_query_allows_normal_term(self) -> None:
        decision = self.query_filter.evaluate("Minecraft", self.strict)
        self.assertEqual(decision.verdict, SAFETY_VERDICT_ALLOW)

    def test_explicit_query_blocks_before_search(self) -> None:
        decision = self.query_filter.evaluate("pornography", self.strict)
        self.assertEqual(decision.verdict, SAFETY_VERDICT_BLOCK)

    def test_lgbtq_identity_is_not_blocked_by_itself(self) -> None:
        decision = self.query_filter.evaluate("drag queen", self.strict)
        self.assertEqual(decision.verdict, SAFETY_VERDICT_ALLOW)

    def test_medical_anatomy_blocks_in_strict(self) -> None:
        decision = self.query_filter.evaluate("urology anatomy", self.strict)
        self.assertEqual(decision.verdict, SAFETY_VERDICT_BLOCK)

    def test_medical_anatomy_reviews_in_balanced(self) -> None:
        decision = self.query_filter.evaluate("urology anatomy", self.balanced)
        self.assertEqual(decision.verdict, SAFETY_VERDICT_REVIEW)

    def test_page_hard_category_blocks(self) -> None:
        decision = self.page_filter.evaluate(
            pageid=1,
            title="Example",
            description="",
            categories=("Category:Pornography",),
            config=self.strict,
        )
        self.assertEqual(decision.verdict, SAFETY_VERDICT_BLOCK)

    def test_file_metadata_hard_category_blocks(self) -> None:
        metadata = FakeMetadata(categories=("Category:Human genitalia",))
        decision = self.file_filter.evaluate(metadata, self.strict)
        self.assertEqual(decision.verdict, SAFETY_VERDICT_BLOCK)

    def test_file_extmetadata_sensitive_blocks(self) -> None:
        metadata = FakeMetadata(image_description="pornographic photograph")
        decision = self.file_filter.evaluate(metadata, self.strict)
        self.assertEqual(decision.verdict, SAFETY_VERDICT_BLOCK)

    def test_nude_art_blocks_in_strict(self) -> None:
        metadata = FakeMetadata(categories=("Category:Nude sculptures",))
        decision = self.file_filter.evaluate(metadata, self.strict)
        self.assertEqual(decision.verdict, SAFETY_VERDICT_BLOCK)

    def test_nude_art_reviews_in_balanced(self) -> None:
        metadata = FakeMetadata(categories=("Category:Nude sculptures",))
        decision = self.file_filter.evaluate(metadata, self.balanced)
        self.assertEqual(decision.verdict, SAFETY_VERDICT_REVIEW)

    def test_allowed_page_does_not_auto_allow_file(self) -> None:
        config = SafetyConfig(mode=SAFETY_MODE_STRICT_PUBLIC, allowed_pageids=(42,))
        page_decision = self.page_filter.evaluate(
            pageid=42,
            title="Allowed",
            description="",
            categories=("Category:Pornography",),
            config=config,
        )
        file_decision = self.file_filter.evaluate(
            FakeMetadata(categories=("Category:Pornography",)),
            config,
        )
        self.assertEqual(page_decision.verdict, SAFETY_VERDICT_ALLOW)
        self.assertEqual(file_decision.verdict, SAFETY_VERDICT_BLOCK)

    def test_visual_classifier_threshold_blocks(self) -> None:
        decision = self.visual_filter.evaluate(
            VisualSafetyResult(probabilities={"explicit_nudity": 0.21}),
            self.strict,
        )
        self.assertEqual(decision.verdict, SAFETY_VERDICT_BLOCK)


if __name__ == "__main__":
    unittest.main()
