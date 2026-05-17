from __future__ import annotations

import asyncio
import logging
import os
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Final, Mapping, Protocol

import aiohttp


TMDB_API_BASE_URL: Final[str] = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE_URL: Final[str] = "https://image.tmdb.org/t/p/original"
TMDB_LANGUAGE: Final[str] = "pt-BR"
TMDB_TIMEOUT_SECONDS: Final[int] = 10
DISCOVER_SORT_BY: Final[str] = "popularity.desc"
STRICT_MINIMUM_VOTE_COUNT: Final[int] = 5000
STRICT_MINIMUM_POPULARITY: Final[float] = 45.0
STRICT_MINIMUM_VOTE_AVERAGE: Final[float] = 6.0
POPULAR_MINIMUM_VOTE_COUNT: Final[int] = 3000
POPULAR_MINIMUM_POPULARITY: Final[float] = 30.0
POPULAR_MINIMUM_VOTE_AVERAGE: Final[float] = 5.8
FALLBACK_MINIMUM_VOTE_COUNT: Final[int] = 2000
FALLBACK_MINIMUM_POPULARITY: Final[float] = 20.0
FALLBACK_MINIMUM_VOTE_AVERAGE: Final[float] = 5.5
RATE_LIMIT_RETRY_ATTEMPTS: Final[int] = 3
DEFAULT_OVERVIEW: Final[str] = (
    "A sinopse não foi providenciada em português pelo banco de dados."
)

QueryValue = str | int | float


class SupportsBlacklistCheck(Protocol):
    async def is_blacklisted(self, guild_id: int, tmdb_id: int) -> bool:
        ...


@dataclass(frozen=True, slots=True)
class DiscoverFilterProfile:
    vote_count_gte: int
    popularity_gte: float
    vote_average_gte: float
    max_pages: int


DISCOVER_FILTER_PROFILES: Final[tuple[DiscoverFilterProfile, ...]] = (
    DiscoverFilterProfile(
        vote_count_gte=STRICT_MINIMUM_VOTE_COUNT,
        popularity_gte=STRICT_MINIMUM_POPULARITY,
        vote_average_gte=STRICT_MINIMUM_VOTE_AVERAGE,
        max_pages=12,
    ),
    DiscoverFilterProfile(
        vote_count_gte=POPULAR_MINIMUM_VOTE_COUNT,
        popularity_gte=POPULAR_MINIMUM_POPULARITY,
        vote_average_gte=POPULAR_MINIMUM_VOTE_AVERAGE,
        max_pages=24,
    ),
    DiscoverFilterProfile(
        vote_count_gte=FALLBACK_MINIMUM_VOTE_COUNT,
        popularity_gte=FALLBACK_MINIMUM_POPULARITY,
        vote_average_gte=FALLBACK_MINIMUM_VOTE_AVERAGE,
        max_pages=36,
    ),
)


@dataclass(frozen=True, slots=True)
class TMDBMovie:
    tmdb_id: int
    title: str
    overview: str
    genres: str
    director: str
    runtime: str
    poster_url: str
    release_date: str


class TMDBClient:
    def __init__(self) -> None:
        self.api_key = os.environ.get("TMDB_API_KEY")
        if not self.api_key:
            raise ValueError("TMDB_API_KEY nao foi configurada no ambiente.")

        self._timeout = aiohttp.ClientTimeout(total=TMDB_TIMEOUT_SECONDS)
        self._session: aiohttp.ClientSession | None = None
        self._session_lock = asyncio.Lock()

    async def __aenter__(self) -> TMDBClient:
        await self._ensure_session()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is not None and not self._session.closed:
            return self._session

        async with self._session_lock:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession(
                    timeout=self._timeout,
                    headers={"Accept": "application/json"},
                )

        return self._session

    async def _request_json(
        self,
        endpoint: str,
        params: Mapping[str, QueryValue] | None = None,
    ) -> dict[str, Any]:
        session = await self._ensure_session()
        url = f"{TMDB_API_BASE_URL}{endpoint}"
        query_params: dict[str, QueryValue] = dict(params or {})
        query_params["api_key"] = self.api_key
        query_params["language"] = TMDB_LANGUAGE

        for attempt in range(1, RATE_LIMIT_RETRY_ATTEMPTS + 1):
            try:
                async with session.get(url, params=query_params) as response:
                    if response.status == 429:
                        retry_after = self._retry_after_seconds(
                            response.headers.get("Retry-After")
                        )
                        logging.error(
                            "Rate limit do TMDB recebido em %s; tentativa=%s retry_after=%.2f.",
                            endpoint,
                            attempt,
                            retry_after,
                        )
                        if attempt >= RATE_LIMIT_RETRY_ATTEMPTS:
                            response.raise_for_status()
                        await asyncio.sleep(retry_after)
                        continue

                    response.raise_for_status()
                    payload = await response.json(content_type=None)
                    if not isinstance(payload, dict):
                        raise TypeError("Resposta JSON do TMDB nao retornou um objeto.")
                    return payload
            except (aiohttp.ClientError, asyncio.TimeoutError, TypeError):
                logging.error(
                    "Falha durante requisicao ao TMDB endpoint=%s.",
                    endpoint,
                    exc_info=True,
                )
                raise

        raise RuntimeError("Requisicao ao TMDB encerrada sem resposta valida.")

    @staticmethod
    def _retry_after_seconds(raw_retry_after: str | None) -> float:
        if not raw_retry_after:
            return 1.0

        try:
            return max(float(raw_retry_after), 0.0)
        except ValueError:
            pass

        try:
            retry_date = parsedate_to_datetime(raw_retry_after)
            if retry_date.tzinfo is None:
                retry_date = retry_date.replace(tzinfo=timezone.utc)
            return max((retry_date - datetime.now(timezone.utc)).total_seconds(), 0.0)
        except (TypeError, ValueError, IndexError, OverflowError):
            return 1.0

    async def get_random_valid_movie(
        self,
        guild_id: int,
        db_manager: SupportsBlacklistCheck,
    ) -> TMDBMovie:
        try:
            selected_movie: Mapping[str, Any] | None = None
            selected_profile: DiscoverFilterProfile | None = None

            for profile in DISCOVER_FILTER_PROFILES:
                selected_movie = await self._select_discover_movie(
                    guild_id,
                    db_manager,
                    profile,
                )
                if selected_movie is not None:
                    selected_profile = profile
                    break

            if selected_movie is None:
                raise RuntimeError(
                    "Nenhum filme popular elegivel encontrado fora da blacklist."
                )

            selected_id = self._coerce_positive_int(selected_movie.get("id"), fallback=0)
            if selected_id <= 0:
                raise ValueError("Filme selecionado sem id valido do TMDB.")

            if selected_profile is not None:
                logging.info(
                    "Filme do Dia selecionado no TMDB guild_id=%s tmdb_id=%s profile_vote_count_gte=%s profile_popularity_gte=%s.",
                    guild_id,
                    selected_id,
                    selected_profile.vote_count_gte,
                    selected_profile.popularity_gte,
                )

            details = await self._fetch_movie_details(selected_id)
            return self._build_movie_payload(selected_id, selected_movie, details)
        except Exception:
            logging.error(
                "Falha ao selecionar filme valido no TMDB guild_id=%s.",
                guild_id,
                exc_info=True,
            )
            raise

    async def _select_discover_movie(
        self,
        guild_id: int,
        db_manager: SupportsBlacklistCheck,
        profile: DiscoverFilterProfile,
    ) -> Mapping[str, Any] | None:
        first_page_payload = await self._fetch_discover_page(1, profile)
        total_pages = self._coerce_positive_int(
            first_page_payload.get("total_pages"),
            fallback=1,
        )
        max_page = min(total_pages, profile.max_pages)
        if max_page <= 0:
            return None

        page_numbers = list(range(1, max_page + 1))
        random.shuffle(page_numbers)
        candidates: list[Mapping[str, Any]] = []

        for page_number in page_numbers:
            page_payload = (
                first_page_payload
                if page_number == 1
                else await self._fetch_discover_page(page_number, profile)
            )
            movies = page_payload.get("results") or []
            if not isinstance(movies, list):
                continue

            random.shuffle(movies)
            for movie in movies:
                if not isinstance(movie, Mapping):
                    continue

                tmdb_id = self._coerce_positive_int(movie.get("id"), fallback=0)
                if tmdb_id <= 0:
                    continue

                if await db_manager.is_blacklisted(guild_id, tmdb_id):
                    continue

                candidates.append(movie)

        if not candidates:
            logging.info(
                "Nenhum candidato TMDB fora da blacklist para perfil guild_id=%s vote_count_gte=%s popularity_gte=%s max_pages=%s.",
                guild_id,
                profile.vote_count_gte,
                profile.popularity_gte,
                profile.max_pages,
            )
            return None

        return random.choice(candidates)

    async def _fetch_discover_page(
        self,
        page: int,
        profile: DiscoverFilterProfile,
    ) -> dict[str, Any]:
        return await self._request_json(
            "/discover/movie",
            params={
                "sort_by": DISCOVER_SORT_BY,
                "include_adult": "false",
                "include_video": "false",
                "vote_count.gte": profile.vote_count_gte,
                "popularity.gte": profile.popularity_gte,
                "vote_average.gte": profile.vote_average_gte,
                "page": page,
            },
        )

    async def _fetch_movie_details(self, tmdb_id: int) -> dict[str, Any]:
        return await self._request_json(
            f"/movie/{tmdb_id}?append_to_response=credits",
        )

    @staticmethod
    def _build_movie_payload(
        tmdb_id: int,
        discover_movie: Mapping[str, Any],
        details: Mapping[str, Any],
    ) -> TMDBMovie:
        title = str(details.get("title") or discover_movie.get("title") or "N/A")
        overview = str(details.get("overview") or DEFAULT_OVERVIEW)
        genres = TMDBClient._format_genres(details.get("genres"))
        director = TMDBClient._extract_director(details.get("credits"))
        runtime = TMDBClient._format_runtime(details.get("runtime"))
        release_date = str(
            details.get("release_date") or discover_movie.get("release_date") or "N/A"
        )
        poster_path = details.get("poster_path") or discover_movie.get("poster_path")
        poster_url = (
            f"{TMDB_IMAGE_BASE_URL}{poster_path}"
            if isinstance(poster_path, str) and poster_path
            else "N/A"
        )

        return TMDBMovie(
            tmdb_id=tmdb_id,
            title=title,
            overview=overview,
            genres=genres,
            director=director,
            runtime=runtime,
            poster_url=poster_url,
            release_date=release_date,
        )

    @staticmethod
    def _format_genres(raw_genres: object) -> str:
        if not isinstance(raw_genres, list):
            return "N/A"

        genre_names = [
            str(genre.get("name"))
            for genre in raw_genres
            if isinstance(genre, Mapping) and genre.get("name")
        ]
        return ", ".join(genre_names) if genre_names else "N/A"

    @staticmethod
    def _extract_director(raw_credits: object) -> str:
        if not isinstance(raw_credits, Mapping):
            return "N/A"

        crew = raw_credits.get("crew") or []
        if not isinstance(crew, list):
            return "N/A"

        for member in crew:
            if not isinstance(member, Mapping):
                continue
            if member.get("job") == "Director":
                return str(member.get("name") or "N/A")

        return "N/A"

    @staticmethod
    def _format_runtime(raw_runtime: object) -> str:
        runtime = TMDBClient._coerce_positive_int(raw_runtime, fallback=0)
        if runtime <= 0:
            return "N/A"

        hours, minutes = divmod(runtime, 60)
        return f"{hours}h {minutes}m"

    @staticmethod
    def _coerce_positive_int(value: object, *, fallback: int) -> int:
        if isinstance(value, bool):
            return fallback
        try:
            numeric_value = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return fallback
        return numeric_value if numeric_value > 0 else fallback


__all__ = [
    "DiscoverFilterProfile",
    "TMDBClient",
    "TMDBMovie",
]
