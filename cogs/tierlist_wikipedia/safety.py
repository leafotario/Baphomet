from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping, Protocol


LOGGER = logging.getLogger("baphomet.tierlist.wikipedia.safety")

SAFETY_VERDICT_ALLOW = "allow"
SAFETY_VERDICT_REVIEW = "review"
SAFETY_VERDICT_BLOCK = "block"

SAFETY_MODE_STRICT_PUBLIC = "strict_public"
SAFETY_MODE_BALANCED = "balanced"
SAFETY_MODE_OFF = "off"

SAFETY_CONFIDENCE_LOW = "low"
SAFETY_CONFIDENCE_MEDIUM = "medium"
SAFETY_CONFIDENCE_HIGH = "high"

SAFETY_PUBLIC_BLOCK_MESSAGE = (
    "Não posso usar essa imagem na tierlist porque ela pode ser sensível para um servidor público."
)
SAFETY_PUBLIC_REVIEW_MESSAGE = "Essa imagem precisa de revisão da moderação antes de entrar na tierlist."
SAFETY_PUBLIC_NO_SAFE_RESULTS_MESSAGE = (
    "Encontrei resultados, mas eles parecem sensíveis demais para usar em uma tierlist pública."
)

DEFAULT_HARD_BLOCK_TERMS = (
    "porn",
    "porno",
    "pornografia",
    "pornography",
    "pornographic",
    "xxx",
    "sex tape",
    "sexual intercourse",
    "sexual activity",
    "oral sex",
    "anal sex",
    "masturbation",
    "masturbacao",
    "masturbação",
    "fetish",
    "fetiche",
    "bdsm",
    "hentai",
)

DEFAULT_SUSPICIOUS_TERMS = (
    "nude",
    "nudes",
    "nudity",
    "nudez",
    "nu artistico",
    "nu artístico",
    "nua artistica",
    "nua artística",
    "naked",
    "erotic",
    "erotica",
    "erotico",
    "erótico",
    "erotica",
    "sensual",
    "pin up",
    "pinup",
    "nude art",
    "nude painting",
    "nude sculpture",
)

DEFAULT_MEDICAL_ANATOMY_TERMS = (
    "genitalia",
    "genitals",
    "genital",
    "genitais",
    "penis",
    "vagina",
    "vulva",
    "clitoris",
    "clitóris",
    "testicle",
    "testicles",
    "testiculo",
    "testículo",
    "breast",
    "breasts",
    "mama",
    "mamas",
    "nipple",
    "nipple",
    "mamilo",
    "mamilos",
    "urology",
    "urologia",
    "gynecology",
    "ginecologia",
    "obstetrics",
    "obstetricia",
    "obstetrícia",
    "childbirth",
    "parto",
    "sex education",
    "educacao sexual",
    "educação sexual",
    "reproductive system",
    "sistema reprodutor",
)

DEFAULT_SAFE_IDENTITY_TERMS = (
    "gay",
    "lesbian",
    "lesbica",
    "lésbica",
    "bisexual",
    "bissexual",
    "trans",
    "transgender",
    "transgenero",
    "transgênero",
    "transexual",
    "queer",
    "lgbt",
    "lgbtq",
    "lgbtqia",
    "drag queen",
    "drag king",
)

DEFAULT_ALLOWLIST_TERMS: tuple[str, ...] = ()

DEFAULT_HARD_BLOCK_CATEGORY_PATTERNS = (
    "pornography",
    "pornographic",
    "pornografia",
    "explicit nudity",
    "explicit nude",
    "sexual activity",
    "sexual acts",
    "sexual intercourse",
    "erotic images",
    "erotic photographs",
    "fetish",
    "fetiche",
    "human genitalia",
    "genitalia",
    "nude people",
    "naked people",
)

DEFAULT_REVIEW_CATEGORY_PATTERNS = (
    "nude art",
    "nude paintings",
    "nude sculptures",
    "nude statues",
    "nude women",
    "nude men",
    "anatomy",
    "human anatomy",
    "reproductive system",
    "sex education",
    "medical images",
    "breastfeeding",
    "childbirth",
    "urology",
    "gynecology",
    "gynaecology",
    "mitologia com nudez",
    "mythological nudity",
)

DEFAULT_STRUCTURED_HARD_QIDS: tuple[str, ...] = ()
DEFAULT_STRUCTURED_REVIEW_QIDS: tuple[str, ...] = ()

VISUAL_STRICT_THRESHOLDS = {
    "sexual_activity": 0.10,
    "explicit_nudity": 0.20,
    "partial_nudity": 0.25,
    "suggestive": 0.50,
    "medical_anatomy": 0.30,
}


def normalize_safety_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = re.sub(r"(?is)<br\s*/?>", " ", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_file_title(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if text.casefold().startswith("file:"):
        text = text[5:]
    return normalize_safety_text(text)


def _dedupe(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = re.sub(r"\s+", " ", str(value or "").strip())
        key = normalize_safety_text(cleaned)
        if not cleaned or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return tuple(result)


def _dedupe_int(values: tuple[int, ...] | list[int]) -> tuple[int, ...]:
    seen: set[int] = set()
    result: list[int] = []
    for value in values:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed <= 0 or parsed in seen:
            continue
        seen.add(parsed)
        result.append(parsed)
    return tuple(result)


class SafetyPatternMatcher:
    def matched(self, texts: tuple[Any, ...] | list[Any], patterns: tuple[str, ...]) -> tuple[str, ...]:
        normalized_parts = [normalize_safety_text(text) for text in texts if str(text or "").strip()]
        combined = " ".join(part for part in normalized_parts if part)
        if not combined:
            return tuple()

        padded = f" {combined} "
        matches: list[str] = []
        seen: set[str] = set()
        for pattern in patterns:
            normalized_pattern = normalize_safety_text(pattern)
            if not normalized_pattern or normalized_pattern in seen:
                continue
            if f" {normalized_pattern} " in padded:
                matches.append(pattern)
                seen.add(normalized_pattern)
        return tuple(matches)


@dataclass(frozen=True)
class SafetyDecision:
    verdict: str = SAFETY_VERDICT_ALLOW
    score: int = 0
    reasons: tuple[str, ...] = field(default_factory=tuple)
    public_message: str = ""
    mod_message: str = ""
    sources_triggered: tuple[str, ...] = field(default_factory=tuple)
    confidence: str = SAFETY_CONFIDENCE_MEDIUM
    review_id: str | None = None

    @property
    def allowed(self) -> bool:
        return self.verdict == SAFETY_VERDICT_ALLOW

    @property
    def blocked(self) -> bool:
        return self.verdict == SAFETY_VERDICT_BLOCK

    @property
    def needs_review(self) -> bool:
        return self.verdict == SAFETY_VERDICT_REVIEW


@dataclass(frozen=True)
class SafetyGuildConfig:
    guild_id: int | None = None
    mode: str = SAFETY_MODE_STRICT_PUBLIC
    custom_hard_block_terms: tuple[str, ...] = field(default_factory=tuple)
    custom_allowlist_terms: tuple[str, ...] = field(default_factory=tuple)
    allowed_pageids: tuple[int, ...] = field(default_factory=tuple)
    allowed_file_titles: tuple[str, ...] = field(default_factory=tuple)
    mod_role_ids: tuple[int, ...] = field(default_factory=tuple)
    log_channel_id: int | None = None


@dataclass(frozen=True)
class SafetyConfig:
    mode: str = SAFETY_MODE_STRICT_PUBLIC
    hard_block_terms: tuple[str, ...] = DEFAULT_HARD_BLOCK_TERMS
    suspicious_terms: tuple[str, ...] = DEFAULT_SUSPICIOUS_TERMS
    medical_anatomy_terms: tuple[str, ...] = DEFAULT_MEDICAL_ANATOMY_TERMS
    safe_identity_terms: tuple[str, ...] = DEFAULT_SAFE_IDENTITY_TERMS
    allowlist_terms: tuple[str, ...] = DEFAULT_ALLOWLIST_TERMS
    hard_block_category_patterns: tuple[str, ...] = DEFAULT_HARD_BLOCK_CATEGORY_PATTERNS
    review_category_patterns: tuple[str, ...] = DEFAULT_REVIEW_CATEGORY_PATTERNS
    structured_hard_qids: tuple[str, ...] = DEFAULT_STRUCTURED_HARD_QIDS
    structured_review_qids: tuple[str, ...] = DEFAULT_STRUCTURED_REVIEW_QIDS
    allowed_pageids: tuple[int, ...] = field(default_factory=tuple)
    allowed_file_titles: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_guild_config(cls, guild_config: SafetyGuildConfig) -> "SafetyConfig":
        mode = guild_config.mode if guild_config.mode in {
            SAFETY_MODE_STRICT_PUBLIC,
            SAFETY_MODE_BALANCED,
            SAFETY_MODE_OFF,
        } else SAFETY_MODE_STRICT_PUBLIC
        return cls(
            mode=mode,
            hard_block_terms=_dedupe(DEFAULT_HARD_BLOCK_TERMS + guild_config.custom_hard_block_terms),
            suspicious_terms=DEFAULT_SUSPICIOUS_TERMS,
            medical_anatomy_terms=DEFAULT_MEDICAL_ANATOMY_TERMS,
            safe_identity_terms=DEFAULT_SAFE_IDENTITY_TERMS,
            allowlist_terms=_dedupe(DEFAULT_ALLOWLIST_TERMS + guild_config.custom_allowlist_terms),
            allowed_pageids=_dedupe_int(guild_config.allowed_pageids),
            allowed_file_titles=_dedupe(guild_config.allowed_file_titles),
        )


class SafetyConfigStore:
    def __init__(self, path: str | Path = "data/tierlist_safety.json") -> None:
        self.path = Path(path)
        self._configs: dict[int, SafetyGuildConfig] = {}
        self._loaded = False
        self._lock = asyncio.Lock()

    async def get_guild_config(self, guild_id: int | None) -> SafetyGuildConfig:
        if guild_id is None:
            return SafetyGuildConfig(guild_id=None)
        await self._ensure_loaded()
        return self._configs.get(int(guild_id), SafetyGuildConfig(guild_id=int(guild_id)))

    async def set_mode(self, guild_id: int, mode: str) -> SafetyGuildConfig:
        if mode not in {SAFETY_MODE_STRICT_PUBLIC, SAFETY_MODE_BALANCED, SAFETY_MODE_OFF}:
            raise ValueError("invalid safety mode")
        async with self._lock:
            await self._ensure_loaded_unlocked()
            current = self._configs.get(guild_id, SafetyGuildConfig(guild_id=guild_id))
            updated = replace(current, mode=mode)
            self._configs[guild_id] = updated
            await self._save_unlocked()
            return updated

    async def add_custom_hard_block_term(self, guild_id: int, term: str) -> SafetyGuildConfig:
        return await self._update_terms(guild_id, "custom_hard_block_terms", term, add=True)

    async def remove_custom_hard_block_term(self, guild_id: int, term: str) -> SafetyGuildConfig:
        return await self._update_terms(guild_id, "custom_hard_block_terms", term, add=False)

    async def add_custom_allowlist_term(self, guild_id: int, term: str) -> SafetyGuildConfig:
        return await self._update_terms(guild_id, "custom_allowlist_terms", term, add=True)

    async def add_allowed_pageid(self, guild_id: int, pageid: int) -> SafetyGuildConfig:
        async with self._lock:
            await self._ensure_loaded_unlocked()
            current = self._configs.get(guild_id, SafetyGuildConfig(guild_id=guild_id))
            values = _dedupe_int(list(current.allowed_pageids) + [pageid])
            updated = replace(current, allowed_pageids=values)
            self._configs[guild_id] = updated
            await self._save_unlocked()
            return updated

    async def add_allowed_file_title(self, guild_id: int, file_title: str) -> SafetyGuildConfig:
        async with self._lock:
            await self._ensure_loaded_unlocked()
            current = self._configs.get(guild_id, SafetyGuildConfig(guild_id=guild_id))
            values = _dedupe(list(current.allowed_file_titles) + [self._clean_file_title(file_title)])
            updated = replace(current, allowed_file_titles=values)
            self._configs[guild_id] = updated
            await self._save_unlocked()
            return updated

    async def _update_terms(self, guild_id: int, field_name: str, term: str, *, add: bool) -> SafetyGuildConfig:
        async with self._lock:
            await self._ensure_loaded_unlocked()
            current = self._configs.get(guild_id, SafetyGuildConfig(guild_id=guild_id))
            values = list(getattr(current, field_name))
            normalized_term = normalize_safety_text(term)
            if add:
                values.append(term)
            else:
                values = [value for value in values if normalize_safety_text(value) != normalized_term]
            updated = replace(current, **{field_name: _dedupe(values)})
            self._configs[guild_id] = updated
            await self._save_unlocked()
            return updated

    async def _ensure_loaded(self) -> None:
        async with self._lock:
            await self._ensure_loaded_unlocked()

    async def _ensure_loaded_unlocked(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("Não consegui ler config tierlist safety: %s", exc)
            return
        guilds = payload.get("guilds") if isinstance(payload, dict) else None
        if not isinstance(guilds, dict):
            return
        for raw_guild_id, raw_config in guilds.items():
            if not isinstance(raw_config, dict):
                continue
            try:
                guild_id = int(raw_guild_id)
            except (TypeError, ValueError):
                continue
            self._configs[guild_id] = SafetyGuildConfig(
                guild_id=guild_id,
                mode=str(raw_config.get("mode") or SAFETY_MODE_STRICT_PUBLIC),
                custom_hard_block_terms=_dedupe(list(raw_config.get("custom_hard_block_terms") or [])),
                custom_allowlist_terms=_dedupe(list(raw_config.get("custom_allowlist_terms") or [])),
                allowed_pageids=_dedupe_int(list(raw_config.get("allowed_pageids") or [])),
                allowed_file_titles=_dedupe(list(raw_config.get("allowed_file_titles") or [])),
                mod_role_ids=_dedupe_int(list(raw_config.get("mod_role_ids") or [])),
                log_channel_id=self._optional_int(raw_config.get("log_channel_id")),
            )

    async def _save_unlocked(self) -> None:
        payload = {
            "guilds": {
                str(guild_id): {
                    "mode": config.mode,
                    "custom_hard_block_terms": list(config.custom_hard_block_terms),
                    "custom_allowlist_terms": list(config.custom_allowlist_terms),
                    "allowed_pageids": list(config.allowed_pageids),
                    "allowed_file_titles": list(config.allowed_file_titles),
                    "mod_role_ids": list(config.mod_role_ids),
                    "log_channel_id": config.log_channel_id,
                }
                for guild_id, config in sorted(self._configs.items())
            }
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _clean_file_title(self, file_title: str) -> str:
        value = re.sub(r"\s+", " ", (file_title or "").strip())
        if value and not value.casefold().startswith("file:"):
            value = f"File:{value}"
        return value

    def _optional_int(self, value: Any) -> int | None:
        try:
            parsed = int(value)
            return parsed if parsed > 0 else None
        except (TypeError, ValueError):
            return None


class SafetyCache:
    def __init__(
        self,
        *,
        allow_ttl_seconds: int = 12 * 60 * 60,
        review_ttl_seconds: int = 24 * 60 * 60,
        block_ttl_seconds: int = 14 * 24 * 60 * 60,
    ) -> None:
        self.allow_ttl_seconds = allow_ttl_seconds
        self.review_ttl_seconds = review_ttl_seconds
        self.block_ttl_seconds = block_ttl_seconds
        self._values: dict[str, tuple[float, SafetyDecision]] = {}
        self._lock = asyncio.Lock()

    async def get(self, scope: str, guild_id: int | None, key: str) -> SafetyDecision | None:
        cache_key = self._cache_key(scope, guild_id, key)
        async with self._lock:
            cached = self._values.get(cache_key)
            if cached is None:
                return None
            expires_at, decision = cached
            if expires_at < time.monotonic():
                self._values.pop(cache_key, None)
                return None
            return decision

    async def set(self, scope: str, guild_id: int | None, key: str, decision: SafetyDecision) -> None:
        ttl = self._ttl_for(decision)
        if ttl <= 0:
            return
        cache_key = self._cache_key(scope, guild_id, key)
        async with self._lock:
            self._values[cache_key] = (time.monotonic() + ttl, decision)

    async def clear_all(self) -> None:
        async with self._lock:
            self._values.clear()

    def _ttl_for(self, decision: SafetyDecision) -> int:
        if decision.verdict == SAFETY_VERDICT_BLOCK:
            return self.block_ttl_seconds
        if decision.verdict == SAFETY_VERDICT_REVIEW:
            return self.review_ttl_seconds
        return self.allow_ttl_seconds

    def _cache_key(self, scope: str, guild_id: int | None, key: str) -> str:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return f"{scope}:{guild_id or 0}:{digest}"


class SafetyDecisionEngine:
    def make(
        self,
        verdict: str,
        *,
        score: int,
        reason: str,
        source: str,
        confidence: str = SAFETY_CONFIDENCE_MEDIUM,
    ) -> SafetyDecision:
        if verdict == SAFETY_VERDICT_ALLOW:
            public_message = ""
        elif verdict == SAFETY_VERDICT_REVIEW:
            public_message = SAFETY_PUBLIC_REVIEW_MESSAGE
        else:
            public_message = SAFETY_PUBLIC_BLOCK_MESSAGE
        score = max(0, min(int(score), 100))
        return SafetyDecision(
            verdict=verdict,
            score=score,
            reasons=(reason,),
            public_message=public_message,
            mod_message=reason,
            sources_triggered=(source,),
            confidence=confidence,
        )

    def combine(self, decisions: tuple[SafetyDecision, ...], config: SafetyConfig) -> SafetyDecision:
        if not decisions:
            return SafetyDecision()
        ranked = sorted(
            decisions,
            key=lambda decision: (
                2 if decision.verdict == SAFETY_VERDICT_BLOCK else 1 if decision.verdict == SAFETY_VERDICT_REVIEW else 0,
                decision.score,
            ),
            reverse=True,
        )
        primary = ranked[0]
        reasons: list[str] = []
        sources: list[str] = []
        for decision in ranked:
            reasons.extend(decision.reasons)
            sources.extend(decision.sources_triggered)
        combined = replace(
            primary,
            reasons=tuple(dict.fromkeys(reasons)),
            sources_triggered=tuple(dict.fromkeys(sources)),
            mod_message="; ".join(dict.fromkeys(reasons)),
        )
        return self.finalize(combined, config)

    def finalize(self, decision: SafetyDecision, config: SafetyConfig) -> SafetyDecision:
        if config.mode == SAFETY_MODE_OFF:
            return SafetyDecision(
                verdict=SAFETY_VERDICT_ALLOW,
                score=0,
                reasons=("Safety mode OFF for this guild.",),
                sources_triggered=("config",),
                confidence=SAFETY_CONFIDENCE_LOW,
            )
        if config.mode == SAFETY_MODE_STRICT_PUBLIC and decision.verdict == SAFETY_VERDICT_REVIEW:
            reasons = decision.reasons + ("Server mode STRICT_PUBLIC escalated review to block.",)
            return replace(
                decision,
                verdict=SAFETY_VERDICT_BLOCK,
                score=max(decision.score, 80),
                reasons=reasons,
                public_message=SAFETY_PUBLIC_BLOCK_MESSAGE,
                mod_message="; ".join(reasons),
            )
        return decision


class QuerySafetyFilter:
    def __init__(self, matcher: SafetyPatternMatcher, engine: SafetyDecisionEngine) -> None:
        self.matcher = matcher
        self.engine = engine

    def evaluate(self, raw_term: str, config: SafetyConfig) -> SafetyDecision:
        if config.mode == SAFETY_MODE_OFF:
            return self.engine.finalize(SafetyDecision(), config)

        texts = (raw_term,)
        allow_matches = self.matcher.matched(texts, config.allowlist_terms)
        if allow_matches:
            return self.engine.make(
                SAFETY_VERDICT_ALLOW,
                score=0,
                reason=f"Query allowlist matched: {', '.join(allow_matches)}",
                source="query",
                confidence=SAFETY_CONFIDENCE_MEDIUM,
            )

        decisions: list[SafetyDecision] = []
        hard_matches = self.matcher.matched(texts, config.hard_block_terms)
        if hard_matches:
            decisions.append(
                self.engine.make(
                    SAFETY_VERDICT_BLOCK,
                    score=95,
                    reason=f"Query matched hard-block term: {', '.join(hard_matches)}",
                    source="query",
                    confidence=SAFETY_CONFIDENCE_HIGH,
                )
            )

        suspicious_matches = self.matcher.matched(texts, config.suspicious_terms)
        if suspicious_matches:
            decisions.append(
                self.engine.make(
                    SAFETY_VERDICT_REVIEW,
                    score=70,
                    reason=f"Query matched suspicious term: {', '.join(suspicious_matches)}",
                    source="query",
                    confidence=SAFETY_CONFIDENCE_MEDIUM,
                )
            )

        medical_matches = self.matcher.matched(texts, config.medical_anatomy_terms)
        if medical_matches:
            decisions.append(
                self.engine.make(
                    SAFETY_VERDICT_REVIEW,
                    score=78,
                    reason=f"Query matched medical/anatomy term: {', '.join(medical_matches)}",
                    source="query",
                    confidence=SAFETY_CONFIDENCE_MEDIUM,
                )
            )

        if decisions:
            return self.engine.combine(tuple(decisions), config)

        identity_matches = self.matcher.matched(texts, config.safe_identity_terms)
        if identity_matches:
            return self.engine.make(
                SAFETY_VERDICT_ALLOW,
                score=0,
                reason=f"Query matched safe identity term only: {', '.join(identity_matches)}",
                source="query",
                confidence=SAFETY_CONFIDENCE_HIGH,
            )

        return SafetyDecision(confidence=SAFETY_CONFIDENCE_HIGH)


class PageSafetyFilter:
    def __init__(self, matcher: SafetyPatternMatcher, engine: SafetyDecisionEngine) -> None:
        self.matcher = matcher
        self.engine = engine

    def evaluate(self, *, pageid: int, title: str, description: str, categories: tuple[str, ...], config: SafetyConfig) -> SafetyDecision:
        if config.mode == SAFETY_MODE_OFF:
            return self.engine.finalize(SafetyDecision(), config)

        if pageid in set(config.allowed_pageids):
            return self.engine.make(
                SAFETY_VERDICT_ALLOW,
                score=0,
                reason=f"Page allowlist matched pageid: {pageid}",
                source="page",
                confidence=SAFETY_CONFIDENCE_MEDIUM,
            )

        text_fields = (title, description)
        category_fields = categories
        decisions: list[SafetyDecision] = []

        text_hard = self.matcher.matched(text_fields, config.hard_block_terms + config.hard_block_category_patterns)
        if text_hard:
            decisions.append(
                self.engine.make(
                    SAFETY_VERDICT_BLOCK,
                    score=92,
                    reason=f"Page title/description matched hard-block pattern: {', '.join(text_hard)}",
                    source="page",
                    confidence=SAFETY_CONFIDENCE_HIGH,
                )
            )

        category_hard = self.matcher.matched(category_fields, config.hard_block_category_patterns)
        if category_hard:
            decisions.append(
                self.engine.make(
                    SAFETY_VERDICT_BLOCK,
                    score=95,
                    reason=f"Page category matched hard-block pattern: {', '.join(category_hard)}",
                    source="categories",
                    confidence=SAFETY_CONFIDENCE_HIGH,
                )
            )

        text_review = self.matcher.matched(
            text_fields,
            config.suspicious_terms + config.medical_anatomy_terms + config.review_category_patterns,
        )
        if text_review:
            decisions.append(
                self.engine.make(
                    SAFETY_VERDICT_REVIEW,
                    score=72,
                    reason=f"Page title/description matched review pattern: {', '.join(text_review)}",
                    source="page",
                    confidence=SAFETY_CONFIDENCE_MEDIUM,
                )
            )

        category_review = self.matcher.matched(category_fields, config.review_category_patterns)
        if category_review:
            decisions.append(
                self.engine.make(
                    SAFETY_VERDICT_REVIEW,
                    score=78,
                    reason=f"Page category matched review pattern: {', '.join(category_review)}",
                    source="categories",
                    confidence=SAFETY_CONFIDENCE_MEDIUM,
                )
            )

        if decisions:
            return self.engine.combine(tuple(decisions), config)
        return SafetyDecision(confidence=SAFETY_CONFIDENCE_HIGH)


class FileMetadataSafetyFilter:
    def __init__(self, matcher: SafetyPatternMatcher, engine: SafetyDecisionEngine) -> None:
        self.matcher = matcher
        self.engine = engine

    def evaluate(self, metadata: Any, config: SafetyConfig) -> SafetyDecision:
        if config.mode == SAFETY_MODE_OFF:
            return self.engine.finalize(SafetyDecision(), config)

        file_title = str(getattr(metadata, "canonicaltitle", "") or "")
        normalized_file = normalize_file_title(file_title)
        allowed_files = {normalize_file_title(value) for value in config.allowed_file_titles}
        if normalized_file and normalized_file in allowed_files:
            return self.engine.make(
                SAFETY_VERDICT_ALLOW,
                score=0,
                reason=f"File allowlist matched: {file_title}",
                source="file_metadata",
                confidence=SAFETY_CONFIDENCE_MEDIUM,
            )

        categories = tuple(str(value) for value in getattr(metadata, "categories", tuple()) or tuple())
        extmetadata_categories = str(getattr(metadata, "extmetadata_categories", "") or "")
        text_fields = (
            file_title,
            getattr(metadata, "object_name", ""),
            getattr(metadata, "image_description", ""),
            extmetadata_categories,
            getattr(metadata, "restrictions", ""),
        )

        decisions: list[SafetyDecision] = []
        hard_text = self.matcher.matched(text_fields, config.hard_block_terms + config.hard_block_category_patterns)
        if hard_text:
            decisions.append(
                self.engine.make(
                    SAFETY_VERDICT_BLOCK,
                    score=95,
                    reason=f"File metadata matched hard-block pattern: {', '.join(hard_text)}",
                    source="extmetadata",
                    confidence=SAFETY_CONFIDENCE_HIGH,
                )
            )

        hard_category = self.matcher.matched(categories, config.hard_block_category_patterns)
        if hard_category:
            decisions.append(
                self.engine.make(
                    SAFETY_VERDICT_BLOCK,
                    score=96,
                    reason=f"File category matched hard-block pattern: {', '.join(hard_category)}",
                    source="categories",
                    confidence=SAFETY_CONFIDENCE_HIGH,
                )
            )

        review_text = self.matcher.matched(
            text_fields,
            config.suspicious_terms + config.medical_anatomy_terms + config.review_category_patterns,
        )
        if review_text:
            decisions.append(
                self.engine.make(
                    SAFETY_VERDICT_REVIEW,
                    score=78,
                    reason=f"File metadata matched review pattern: {', '.join(review_text)}",
                    source="extmetadata",
                    confidence=SAFETY_CONFIDENCE_MEDIUM,
                )
            )

        review_category = self.matcher.matched(categories, config.review_category_patterns)
        if review_category:
            decisions.append(
                self.engine.make(
                    SAFETY_VERDICT_REVIEW,
                    score=82,
                    reason=f"File category matched review pattern: {', '.join(review_category)}",
                    source="categories",
                    confidence=SAFETY_CONFIDENCE_HIGH,
                )
            )

        if decisions:
            return self.engine.combine(tuple(decisions), config)
        return SafetyDecision(confidence=SAFETY_CONFIDENCE_HIGH)


class StructuredDataSafetyFilter:
    def __init__(
        self,
        matcher: SafetyPatternMatcher,
        engine: SafetyDecisionEngine,
        http_client: Any | None = None,
    ) -> None:
        self.matcher = matcher
        self.engine = engine
        self.http_client = http_client

    async def evaluate(self, metadata: Any, config: SafetyConfig) -> SafetyDecision:
        if config.mode == SAFETY_MODE_OFF or self.http_client is None:
            return self.engine.finalize(SafetyDecision(), config)

        commons_pageid = getattr(metadata, "commons_pageid", None)
        if not commons_pageid:
            return SafetyDecision(confidence=SAFETY_CONFIDENCE_LOW)

        try:
            qids = await self._fetch_depicts_qids(int(commons_pageid))
        except Exception as exc:
            LOGGER.warning("Structured Data indisponível para M%s: %s", commons_pageid, exc)
            return SafetyDecision(
                verdict=SAFETY_VERDICT_ALLOW,
                score=5,
                reasons=("Structured Data unavailable; other safety layers already ran.",),
                sources_triggered=("structured_data",),
                confidence=SAFETY_CONFIDENCE_LOW,
            )

        if not qids:
            return SafetyDecision(confidence=SAFETY_CONFIDENCE_LOW)

        decisions: list[SafetyDecision] = []
        hard_qids = tuple(qid for qid in qids if qid in set(config.structured_hard_qids))
        if hard_qids:
            decisions.append(
                self.engine.make(
                    SAFETY_VERDICT_BLOCK,
                    score=96,
                    reason=f"Structured Data depicts matched hard-block QID: {', '.join(hard_qids)}",
                    source="structured_data",
                    confidence=SAFETY_CONFIDENCE_HIGH,
                )
            )

        review_qids = tuple(qid for qid in qids if qid in set(config.structured_review_qids))
        if review_qids:
            decisions.append(
                self.engine.make(
                    SAFETY_VERDICT_REVIEW,
                    score=82,
                    reason=f"Structured Data depicts matched review QID: {', '.join(review_qids)}",
                    source="structured_data",
                    confidence=SAFETY_CONFIDENCE_HIGH,
                )
            )

        labels = await self._fetch_qid_labels(qids)
        hard_labels = self.matcher.matched(labels, config.hard_block_terms + config.hard_block_category_patterns)
        if hard_labels:
            decisions.append(
                self.engine.make(
                    SAFETY_VERDICT_BLOCK,
                    score=94,
                    reason=f"Structured Data label matched hard-block pattern: {', '.join(hard_labels)}",
                    source="structured_data",
                    confidence=SAFETY_CONFIDENCE_HIGH,
                )
            )

        review_labels = self.matcher.matched(
            labels,
            config.suspicious_terms + config.medical_anatomy_terms + config.review_category_patterns,
        )
        if review_labels:
            decisions.append(
                self.engine.make(
                    SAFETY_VERDICT_REVIEW,
                    score=80,
                    reason=f"Structured Data label matched review pattern: {', '.join(review_labels)}",
                    source="structured_data",
                    confidence=SAFETY_CONFIDENCE_MEDIUM,
                )
            )

        if decisions:
            return self.engine.combine(tuple(decisions), config)
        return SafetyDecision(confidence=SAFETY_CONFIDENCE_MEDIUM)

    async def _fetch_depicts_qids(self, commons_pageid: int) -> tuple[str, ...]:
        entity_id = f"M{commons_pageid}"
        params = {
            "action": "wbgetentities",
            "ids": entity_id,
            "props": "claims",
            "format": "json",
            "formatversion": "2",
        }
        payload = await self.http_client.get_json("commons", params)
        entity = payload.get("entities", {}).get(entity_id, {}) if isinstance(payload, dict) else {}
        claims = entity.get("claims", {}) if isinstance(entity, dict) else {}
        depicts_claims = claims.get("P180", []) if isinstance(claims, dict) else []
        qids: list[str] = []
        for claim in depicts_claims:
            if not isinstance(claim, dict):
                continue
            value = (
                claim.get("mainsnak", {})
                .get("datavalue", {})
                .get("value", {})
            )
            qid = value.get("id") if isinstance(value, dict) else None
            if isinstance(qid, str) and re.fullmatch(r"Q\d+", qid):
                qids.append(qid)
        return tuple(dict.fromkeys(qids))

    async def _fetch_qid_labels(self, qids: tuple[str, ...]) -> tuple[str, ...]:
        if not qids:
            return tuple()
        try:
            params = {
                "action": "wbgetentities",
                "ids": "|".join(qids[:50]),
                "props": "labels",
                "languages": "pt|en",
                "format": "json",
                "formatversion": "2",
            }
            payload = await self.http_client.get_json("wikidata", params)
        except Exception as exc:
            LOGGER.warning("Não consegui consultar labels Wikidata: %s", exc)
            return tuple()
        entities = payload.get("entities", {}) if isinstance(payload, dict) else {}
        labels: list[str] = []
        for entity in entities.values():
            if not isinstance(entity, dict):
                continue
            raw_labels = entity.get("labels", {})
            if not isinstance(raw_labels, dict):
                continue
            for language in ("pt", "en"):
                label = raw_labels.get(language, {})
                if isinstance(label, dict) and label.get("value"):
                    labels.append(str(label["value"]))
        return tuple(dict.fromkeys(labels))


@dataclass(frozen=True)
class VisualSafetyResult:
    probabilities: Mapping[str, float] = field(default_factory=dict)
    labels: tuple[str, ...] = field(default_factory=tuple)


class VisualSafetyClassifier(Protocol):
    async def classify(self, image_bytes: bytes) -> VisualSafetyResult:
        ...


class VisualSafetyFilter:
    def __init__(self, engine: SafetyDecisionEngine) -> None:
        self.engine = engine

    def evaluate(self, result: VisualSafetyResult, config: SafetyConfig) -> SafetyDecision:
        if config.mode == SAFETY_MODE_OFF:
            return self.engine.finalize(SafetyDecision(), config)

        probabilities = {str(key): float(value) for key, value in result.probabilities.items()}
        decisions: list[SafetyDecision] = []
        for label in ("sexual_activity", "explicit_nudity"):
            value = probabilities.get(label, 0.0)
            if value >= VISUAL_STRICT_THRESHOLDS[label]:
                decisions.append(
                    self.engine.make(
                        SAFETY_VERDICT_BLOCK,
                        score=max(90, int(value * 100)),
                        reason=f"Visual classifier exceeded hard threshold: {label}={value:.2f}",
                        source="visual",
                        confidence=SAFETY_CONFIDENCE_MEDIUM,
                    )
                )

        for label in ("partial_nudity", "suggestive", "medical_anatomy"):
            value = probabilities.get(label, 0.0)
            if value >= VISUAL_STRICT_THRESHOLDS[label]:
                decisions.append(
                    self.engine.make(
                        SAFETY_VERDICT_REVIEW,
                        score=max(70, int(value * 100)),
                        reason=f"Visual classifier exceeded review threshold: {label}={value:.2f}",
                        source="visual",
                        confidence=SAFETY_CONFIDENCE_MEDIUM,
                    )
                )

        label_matches = tuple(label for label in result.labels if label in {
            "suggestive",
            "partial_nudity",
            "explicit_nudity",
            "sexual_activity",
            "medical_anatomy",
        })
        if label_matches:
            decisions.append(
                self.engine.make(
                    SAFETY_VERDICT_REVIEW,
                    score=72,
                    reason=f"Visual classifier returned sensitive label: {', '.join(label_matches)}",
                    source="visual",
                    confidence=SAFETY_CONFIDENCE_MEDIUM,
                )
            )

        if decisions:
            return self.engine.combine(tuple(decisions), config)
        return SafetyDecision(confidence=SAFETY_CONFIDENCE_MEDIUM)


@dataclass(frozen=True)
class ModReviewItem:
    review_id: str
    guild_id: int | None
    user_id: int | None
    term: str
    pageid: int | None
    page_title: str
    file_title: str
    verdict: str
    score: int
    reasons: tuple[str, ...]
    created_at: float


class ModReviewQueue:
    def __init__(self) -> None:
        self._items: dict[str, ModReviewItem] = {}
        self._lock = asyncio.Lock()

    async def enqueue(self, context: Mapping[str, Any], decision: SafetyDecision) -> SafetyDecision:
        if decision.verdict != SAFETY_VERDICT_REVIEW:
            return decision
        source = "|".join(
            str(context.get(key) or "")
            for key in ("guild_id", "user_id", "term", "pageid", "file_title")
        )
        digest = hashlib.sha256(f"{source}|{time.time()}".encode("utf-8")).hexdigest()[:10]
        review_id = f"wr-{digest}"
        item = ModReviewItem(
            review_id=review_id,
            guild_id=self._optional_int(context.get("guild_id")),
            user_id=self._optional_int(context.get("user_id")),
            term=str(context.get("term") or ""),
            pageid=self._optional_int(context.get("pageid")),
            page_title=str(context.get("page_title") or ""),
            file_title=str(context.get("file_title") or ""),
            verdict=decision.verdict,
            score=decision.score,
            reasons=decision.reasons,
            created_at=time.time(),
        )
        async with self._lock:
            self._items[review_id] = item
        return replace(decision, review_id=review_id)

    async def list_items(self, guild_id: int | None, *, limit: int = 10) -> tuple[ModReviewItem, ...]:
        async with self._lock:
            items = [
                item
                for item in self._items.values()
                if guild_id is None or item.guild_id == guild_id
            ]
        items.sort(key=lambda item: item.created_at)
        return tuple(items[:limit])

    async def pop(self, review_id: str, guild_id: int | None = None) -> ModReviewItem | None:
        async with self._lock:
            item = self._items.get(review_id)
            if item is None:
                return None
            if guild_id is not None and item.guild_id != guild_id:
                return None
            return self._items.pop(review_id)

    def _optional_int(self, value: Any) -> int | None:
        try:
            parsed = int(value)
            return parsed if parsed > 0 else None
        except (TypeError, ValueError):
            return None


class SafetyAuditLogger:
    def log_decision(
        self,
        decision: SafetyDecision,
        *,
        guild_id: int | None,
        user_id: int | None,
        term: str = "",
        pageid: int | None = None,
        page_title: str = "",
        file_title: str = "",
        mode: str = SAFETY_MODE_STRICT_PUBLIC,
        from_cache: bool = False,
    ) -> None:
        if decision.verdict == SAFETY_VERDICT_ALLOW and decision.score <= 0:
            return
        log_method = LOGGER.warning if decision.verdict != SAFETY_VERDICT_ALLOW else LOGGER.info
        log_method(
            "Tierlist safety decision verdict=%s score=%s confidence=%s mode=%s guild=%s user=%s "
            "term=%r pageid=%s page=%r file=%r sources=%s cached=%s reasons=%s",
            decision.verdict,
            decision.score,
            decision.confidence,
            mode,
            guild_id,
            user_id,
            term,
            pageid,
            page_title,
            file_title,
            ",".join(decision.sources_triggered),
            from_cache,
            " | ".join(decision.reasons),
        )

    def log_admin_action(self, *, guild_id: int, user_id: int, action: str, detail: str) -> None:
        LOGGER.warning(
            "Tierlist safety admin action guild=%s user=%s action=%s detail=%s",
            guild_id,
            user_id,
            action,
            detail,
        )


class SafetyPipeline:
    def __init__(
        self,
        *,
        http_client: Any | None = None,
        config_store: SafetyConfigStore | None = None,
        cache: SafetyCache | None = None,
        visual_classifier: VisualSafetyClassifier | None = None,
        review_queue: ModReviewQueue | None = None,
        audit_logger: SafetyAuditLogger | None = None,
    ) -> None:
        self.config_store = config_store or SafetyConfigStore()
        self.cache = cache or SafetyCache()
        self.visual_classifier = visual_classifier
        self.review_queue = review_queue or ModReviewQueue()
        self.audit_logger = audit_logger or SafetyAuditLogger()
        self.matcher = SafetyPatternMatcher()
        self.engine = SafetyDecisionEngine()
        self.query_filter = QuerySafetyFilter(self.matcher, self.engine)
        self.page_filter = PageSafetyFilter(self.matcher, self.engine)
        self.file_metadata_filter = FileMetadataSafetyFilter(self.matcher, self.engine)
        self.structured_data_filter = StructuredDataSafetyFilter(self.matcher, self.engine, http_client)
        self.visual_filter = VisualSafetyFilter(self.engine)

    @property
    def has_visual_classifier(self) -> bool:
        return self.visual_classifier is not None

    async def get_config(self, guild_id: int | None) -> SafetyConfig:
        guild_config = await self.config_store.get_guild_config(guild_id)
        return SafetyConfig.from_guild_config(guild_config)

    async def evaluate_query(
        self,
        raw_term: str,
        *,
        guild_id: int | None = None,
        user_id: int | None = None,
    ) -> SafetyDecision:
        config = await self.get_config(guild_id)
        cache_key = f"{config.mode}:{normalize_safety_text(raw_term)}"
        cached = await self.cache.get("query", guild_id, cache_key)
        if cached is not None:
            self.audit_logger.log_decision(cached, guild_id=guild_id, user_id=user_id, term=raw_term, mode=config.mode, from_cache=True)
            return cached

        decision = self.query_filter.evaluate(raw_term, config)
        decision = await self._maybe_enqueue_review(decision, guild_id=guild_id, user_id=user_id, term=raw_term)
        await self.cache.set("query", guild_id, cache_key, decision)
        self.audit_logger.log_decision(decision, guild_id=guild_id, user_id=user_id, term=raw_term, mode=config.mode)
        return decision

    async def evaluate_page(
        self,
        *,
        pageid: int,
        title: str,
        description: str,
        categories: tuple[str, ...],
        guild_id: int | None = None,
        user_id: int | None = None,
        term: str = "",
    ) -> SafetyDecision:
        config = await self.get_config(guild_id)
        category_digest = hashlib.sha256("|".join(categories).encode("utf-8")).hexdigest()[:16]
        cache_key = f"{config.mode}:{pageid}:{normalize_safety_text(title)}:{category_digest}"
        cached = await self.cache.get("page", guild_id, cache_key)
        if cached is not None:
            self.audit_logger.log_decision(
                cached,
                guild_id=guild_id,
                user_id=user_id,
                term=term,
                pageid=pageid,
                page_title=title,
                mode=config.mode,
                from_cache=True,
            )
            return cached

        decision = self.page_filter.evaluate(
            pageid=pageid,
            title=title,
            description=description,
            categories=categories,
            config=config,
        )
        decision = await self._maybe_enqueue_review(
            decision,
            guild_id=guild_id,
            user_id=user_id,
            term=term,
            pageid=pageid,
            page_title=title,
        )
        await self.cache.set("page", guild_id, cache_key, decision)
        self.audit_logger.log_decision(
            decision,
            guild_id=guild_id,
            user_id=user_id,
            term=term,
            pageid=pageid,
            page_title=title,
            mode=config.mode,
        )
        return decision

    async def evaluate_file_metadata(
        self,
        metadata: Any,
        *,
        guild_id: int | None = None,
        user_id: int | None = None,
        term: str = "",
        pageid: int | None = None,
        page_title: str = "",
    ) -> SafetyDecision:
        config = await self.get_config(guild_id)
        file_title = str(getattr(metadata, "canonicaltitle", "") or "")
        cache_key = f"{config.mode}:{normalize_file_title(file_title)}:{getattr(metadata, 'commons_pageid', '')}"
        cached = await self.cache.get("file", guild_id, cache_key)
        if cached is not None:
            self.audit_logger.log_decision(
                cached,
                guild_id=guild_id,
                user_id=user_id,
                term=term,
                pageid=pageid,
                page_title=page_title,
                file_title=file_title,
                mode=config.mode,
                from_cache=True,
            )
            return cached

        decision = self.file_metadata_filter.evaluate(metadata, config)
        decision = await self._maybe_enqueue_review(
            decision,
            guild_id=guild_id,
            user_id=user_id,
            term=term,
            pageid=pageid,
            page_title=page_title,
            file_title=file_title,
        )
        await self.cache.set("file", guild_id, cache_key, decision)
        self.audit_logger.log_decision(
            decision,
            guild_id=guild_id,
            user_id=user_id,
            term=term,
            pageid=pageid,
            page_title=page_title,
            file_title=file_title,
            mode=config.mode,
        )
        return decision

    async def evaluate_structured_data(
        self,
        metadata: Any,
        *,
        guild_id: int | None = None,
        user_id: int | None = None,
        term: str = "",
        pageid: int | None = None,
        page_title: str = "",
    ) -> SafetyDecision:
        config = await self.get_config(guild_id)
        commons_pageid = getattr(metadata, "commons_pageid", None)
        cache_key = f"{config.mode}:{commons_pageid or 0}"
        cached = await self.cache.get("structured", guild_id, cache_key)
        if cached is not None:
            return cached
        decision = await self.structured_data_filter.evaluate(metadata, config)
        decision = await self._maybe_enqueue_review(
            decision,
            guild_id=guild_id,
            user_id=user_id,
            term=term,
            pageid=pageid,
            page_title=page_title,
            file_title=str(getattr(metadata, "canonicaltitle", "") or ""),
        )
        await self.cache.set("structured", guild_id, cache_key, decision)
        self.audit_logger.log_decision(
            decision,
            guild_id=guild_id,
            user_id=user_id,
            term=term,
            pageid=pageid,
            page_title=page_title,
            file_title=str(getattr(metadata, "canonicaltitle", "") or ""),
            mode=config.mode,
        )
        return decision

    async def evaluate_visual(
        self,
        image_bytes: bytes,
        *,
        metadata: Any,
        guild_id: int | None = None,
        user_id: int | None = None,
        term: str = "",
        pageid: int | None = None,
        page_title: str = "",
    ) -> SafetyDecision:
        config = await self.get_config(guild_id)
        if self.visual_classifier is None:
            return SafetyDecision(confidence=SAFETY_CONFIDENCE_LOW)
        image_digest = hashlib.sha256(image_bytes).hexdigest()
        cache_key = f"{config.mode}:{image_digest}"
        cached = await self.cache.get("visual", guild_id, cache_key)
        if cached is not None:
            self.audit_logger.log_decision(
                cached,
                guild_id=guild_id,
                user_id=user_id,
                term=term,
                pageid=pageid,
                page_title=page_title,
                file_title=str(getattr(metadata, "canonicaltitle", "") or ""),
                mode=config.mode,
                from_cache=True,
            )
            return cached
        result = await self.visual_classifier.classify(image_bytes)
        decision = self.visual_filter.evaluate(result, config)
        decision = await self._maybe_enqueue_review(
            decision,
            guild_id=guild_id,
            user_id=user_id,
            term=term,
            pageid=pageid,
            page_title=page_title,
            file_title=str(getattr(metadata, "canonicaltitle", "") or ""),
        )
        await self.cache.set("visual", guild_id, cache_key, decision)
        self.audit_logger.log_decision(
            decision,
            guild_id=guild_id,
            user_id=user_id,
            term=term,
            pageid=pageid,
            page_title=page_title,
            file_title=str(getattr(metadata, "canonicaltitle", "") or ""),
            mode=config.mode,
        )
        return decision

    async def evaluate_unavailable_source(
        self,
        *,
        source: str,
        guild_id: int | None = None,
        user_id: int | None = None,
        term: str = "",
        pageid: int | None = None,
        page_title: str = "",
        file_title: str = "",
    ) -> SafetyDecision:
        config = await self.get_config(guild_id)
        decision = self.engine.make(
            SAFETY_VERDICT_REVIEW,
            score=72,
            reason=f"Safety source unavailable: {source}",
            source=source,
            confidence=SAFETY_CONFIDENCE_LOW,
        )
        decision = self.engine.finalize(decision, config)
        decision = await self._maybe_enqueue_review(
            decision,
            guild_id=guild_id,
            user_id=user_id,
            term=term,
            pageid=pageid,
            page_title=page_title,
            file_title=file_title,
        )
        self.audit_logger.log_decision(
            decision,
            guild_id=guild_id,
            user_id=user_id,
            term=term,
            pageid=pageid,
            page_title=page_title,
            file_title=file_title,
            mode=config.mode,
        )
        return decision

    async def _maybe_enqueue_review(self, decision: SafetyDecision, **context: Any) -> SafetyDecision:
        if decision.verdict != SAFETY_VERDICT_REVIEW:
            return decision
        return await self.review_queue.enqueue(context, decision)
