
from app.tools.recommend_unliked_movies import run_search
# Replace this with a real user UUID from your database.
USER_ID = "456765de-e09d-4f6f-aeca-0c659de53276"

text, movies = run_search(
    user_id=USER_ID,
    genre="Action",
    rating_min=7,
    number_of_movies=10,
)

print("\n========== SEARCH RESULT ==========")
print(text)

print("\n========== MOVIES ==========")

for movie in movies:
    print(
        f"{movie.get('title')} | "
        f"IMDb: {movie.get('imdb_id')} | "
        f"Rating: {movie.get('rating')} | "
        f"Year: {movie.get('release_year')}"
    )

print(f"\nTotal returned: {len(movies)}")
