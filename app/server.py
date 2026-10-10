from mcp.server.fastmcp import FastMCP

from app.config import validate_config
from app.tools.recommend_unwatched_movies import register_tools

validate_config()

mcp = FastMCP(
    "Movie Recommendation MCP",
    host="0.0.0.0",
    port=8000,
)

register_tools(mcp)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")