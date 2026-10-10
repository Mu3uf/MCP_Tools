
import os
import traceback
from functools import lru_cache

import requests
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from supabase import create_client


# ============================================================
# CONFIGURATION
# ============================================================

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TMDB_API_KEY = os.getenv("TMDB_API_KEY")

if not SUPABASE_URL:
    raise ValueError("SUPABASE_URL is missing from .env")

if not SUPABASE_KEY:
    raise ValueError("SUPABASE_KEY is missing from .env")

if not TMDB_API_KEY:
    raise ValueError("TMDB_API_KEY is missing from .env")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

TMDB_SEARCH_URL = "https://api.themoviedb.org/3/search/movie"

BATCH_SIZE = 100
MAX_MOVIES = 50

EMPTY_STRINGS = {
    "", "null", "none", "n/a", "na", "nan",
    "undefined", "any", "unknown"
}


# ============================================================
# INPUT CLEANING
# ============================================================

def clean_text(value):
    if value is None:
        return None

    value = str(value).strip()

    if value.lower() in EMPTY_STRINGS:
        return None

    return value


def to_number(value):
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, str):
        value = value.strip()

        if value.lower() in EMPTY_STRINGS:
            return None

    try:
        number = float(value)
    except (ValueError, TypeError):
        return None

    if number != number or number in (
        float("inf"), float("-inf")
    ):
        return None

    return number


def clean_positive_int(value):
    number = to_number(value)

    if number is None or number <= 0:
        return None

    return int(number)


def clean_positive_float(value):
    number = to_number(value)

    if number is None or number <= 0:
        return None

    return number


# ============================================================
# GET WATCHED MOVIES
# Table: user_movie_actions
# Columns: user_id, imdb_id, watched
# ============================================================

def get_watched_imdb_ids(user_id: str) -> set[str]:
    if not user_id or not user_id.strip():
        raise ValueError("user_id is required")

    watched_ids = set()
    page_size = 500
    offset = 0

    while True:
        response = (
            supabase.table("user_movie_actions")
            .select("imdb_id")
            .eq("user_id", user_id)
            .eq("watched", True)
            .range(offset, offset + page_size - 1)
            .execute()
        )

        rows = response.data or []

        for row in rows:
            imdb_id = row.get("imdb_id")

            if imdb_id:
                watched_ids.add(str(imdb_id).strip())

        if len(rows) < page_size:
            break

        offset += len(rows)

    return watched_ids


# ============================================================
# FORMAT RESULTS
# ============================================================

def compact_for_llm(movies):
    lines = []

    for movie in movies:
        genres = movie.get("genres") or []

        if isinstance(genres, list):
            genres = ", ".join(str(g) for g in genres[:3])

        lines.append(
            f"{movie.get('title')} "
            f"({movie.get('release_year')}) "
            f"Rating: {movie.get('rating')} | "
            f"Genres: {genres}"
        )

    return (
        f"Found {len(movies)} unwatched movies.\n"
        + "\n".join(lines)
    )


# ============================================================
# TMDB POSTER LOOKUP
# ============================================================

@lru_cache(maxsize=2000)
def find_tmdb_poster(title: str, release_year: int = None):
    if not title:
        return None

    try:
        params = {
            "api_key": TMDB_API_KEY,
            "query": title,
            "include_adult": "false",
            "language": "en-US",
            "page": 1,
        }

        response = requests.get(
            TMDB_SEARCH_URL,
            params=params,
            timeout=10,
        )

        response.raise_for_status()
        results = response.json().get("results", [])

        if not results:
            return None

        if release_year is not None:
            for movie in results:
                release_date = movie.get("release_date")

                if not release_date:
                    continue

                try:
                    year = int(release_date[:4])
                except (ValueError, TypeError):
                    continue

                if year == release_year and movie.get("poster_path"):
                    return movie["poster_path"]

        for movie in results:
            if movie.get("poster_path"):
                return movie["poster_path"]

        return None

    except requests.RequestException as exc:
        print(f"[POSTER ERROR] {title}: {exc}")
        return None


# ============================================================
# BUILD SUPABASE MOVIE QUERY
# ============================================================

def build_movie_query(
    title,
    genre,
    actor,
    director,
    release_year,
    release_year_min,
    release_year_max,
    rating_min,
    rating_max,
    vote_count_min,
    offset,
):
    query = supabase.table("movies").select(
        "imdb_id,"
        "title,"
        "release_year,"
        "rating,"
        "vote_count,"
        "bayesian_score,"
        "genres,"
        "directors,"
        "actors,"
        "keywords,"
        "plot,"
        "poster_path,"
        "duration,"
        "certificate,"
        "country,"
        "language,"
        "status"
    )

    if title:
        query = query.ilike("title", f"%{title}%")

    if genre:
        query = query.contains("genres", [genre])

    if actor:
        query = query.contains("actors", [actor])

    if director:
        query = query.contains("directors", [director])

    if release_year is not None:
        query = query.eq("release_year", release_year)

    if release_year_min is not None:
        query = query.gte("release_year", release_year_min)

    if release_year_max is not None:
        query = query.lte("release_year", release_year_max)

    if rating_min is not None:
        query = query.gte("rating", rating_min)

    if rating_max is not None:
        query = query.lte("rating", rating_max)

    if vote_count_min is not None:
        query = query.gte("vote_count", vote_count_min)

    query = query.order("bayesian_score", desc=True)
    query = query.order("rating", desc=True)
    query = query.order("vote_count", desc=True)

    query = query.range(offset, offset + BATCH_SIZE - 1)

    return query


# ============================================================
# SEARCH AND FILTER UNWATCHED MOVIES
# ============================================================

def run_search(
    user_id,
    title=None,
    genre=None,
    actor=None,
    director=None,
    release_year=None,
    release_year_min=None,
    release_year_max=None,
    rating_min=None,
    rating_max=None,
    vote_count_min=None,
    number_of_movies=10,
):
    title = clean_text(title)
    genre = clean_text(genre)
    actor = clean_text(actor)
    director = clean_text(director)

    release_year = clean_positive_int(release_year)
    release_year_min = clean_positive_int(release_year_min)
    release_year_max = clean_positive_int(release_year_max)

    rating_min = clean_positive_float(rating_min)
    rating_max = clean_positive_float(rating_max)

    vote_count_min = clean_positive_int(vote_count_min)
    number_of_movies = clean_positive_int(number_of_movies)

    if number_of_movies is None:
        number_of_movies = 10

    if number_of_movies > MAX_MOVIES:
        return (
            f"Maximum number_of_movies is {MAX_MOVIES}.",
            [],
        )

    if not user_id or not str(user_id).strip():
        return "A valid user_id is required.", []

    user_id = str(user_id).strip()

    # Retrieve watched movies first.
    # If this fails, do not return unfiltered recommendations.
    try:
        watched_ids = get_watched_imdb_ids(user_id)
    except Exception as exc:
        print(f"[WATCHED ERROR] {exc}")
        traceback.print_exc()

        return (
            "ERROR: Could not retrieve watched movies. "
            "No recommendations returned.",
            [],
        )

    selected_movies = []
    selected_ids = set()
    offset = 0
    scanned = 0

    try:
        while len(selected_movies) < number_of_movies:
            query = build_movie_query(
                title=title,
                genre=genre,
                actor=actor,
                director=director,
                release_year=release_year,
                release_year_min=release_year_min,
                release_year_max=release_year_max,
                rating_min=rating_min,
                rating_max=rating_max,
                vote_count_min=vote_count_min,
                offset=offset,
            )

            response = query.execute()
            batch = response.data or []

            print(
                f"[SEARCH] Offset: {offset}, "
                f"Batch size: {len(batch)}"
            )

            if not batch:
                break

            scanned += len(batch)

            for movie in batch:
                imdb_id = movie.get("imdb_id")

                # Cannot reliably check watched status without an ID.
                if not imdb_id:
                    continue

                imdb_id = str(imdb_id).strip()

                # Exclude movies already watched by this user.
                if imdb_id in watched_ids:
                    continue

                # Avoid duplicate results.
                if imdb_id in selected_ids:
                    continue

                selected_ids.add(imdb_id)
                selected_movies.append(movie)

                if len(selected_movies) >= number_of_movies:
                    break

            # Move to the next database page.
            offset += len(batch)

            if len(batch) < BATCH_SIZE:
                break

    except Exception as exc:
        print(f"[SEARCH ERROR] {exc}")
        traceback.print_exc()

        return (
            "ERROR: Movie search failed. "
            "Check Supabase configuration and permissions.",
            [],
        )

    print(
        f"[RESULT] Scanned={scanned}, "
        f"Watched={len(watched_ids)}, "
        f"Returned={len(selected_movies)}"
    )

    if not selected_movies:
        return (
            "No unwatched movies matched the requested filters.",
            [],
        )

    # Add missing posters from TMDB.
    for movie in selected_movies:
        if not movie.get("poster_path"):
            movie["poster_path"] = find_tmdb_poster(
                movie.get("title"),
                movie.get("release_year"),
            )

    return compact_for_llm(selected_movies), selected_movies


# ============================================================
# MCP TOOL
# ============================================================

def register_tools(mcp: FastMCP):

    @mcp.tool()
    def recommend_unwatched_movies(
        user_id: str,
        title: str = None,
        genre: str = None,
        actor: str = None,
        director: str = None,
        release_year: int = None,
        release_year_min: int = None,
        release_year_max: int = None,
        rating_min: float = None,
        rating_max: float = None,
        vote_count_min: int = None,
        number_of_movies: int = 10,
    ) -> dict:
        """
        Recommend movies from Supabase that the user has not watched.

        Supports title, genre, actor, director, release year,
        release-year range, rating range, minimum vote count,
        and requested number of movies.

        Results preserve the original ranking:
        bayesian_score, rating, and vote_count.

        The tool fetches additional pages until it obtains the
        requested number of unwatched movies or exhausts results.

        user_id must be supplied by a trusted authenticated backend.
        """

        text, movies = run_search(
            user_id=user_id,
            title=title,
            genre=genre,
            actor=actor,
            director=director,
            release_year=release_year,
            release_year_min=release_year_min,
            release_year_max=release_year_max,
            rating_min=rating_min,
            rating_max=rating_max,
            vote_count_min=vote_count_min,
            number_of_movies=number_of_movies,
        )

        return {
            "success": bool(movies),
            "count": len(movies),
            "message": text,
            "movies": movies,
        }

    return mcp
