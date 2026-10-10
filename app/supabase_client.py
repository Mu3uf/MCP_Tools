
import os

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Missing SUPABASE_URL or SUPABASE_KEY in .env")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def get_watched_imdb_ids(user_id: str) -> set[str]:
    """Get all IMDb IDs watched by a specific user."""

    if not user_id or not user_id.strip():
        raise ValueError("A real user_id is required")

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
