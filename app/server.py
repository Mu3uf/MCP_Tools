
import os
from mcp.server.fastmcp import FastMCP

from app.tools.recommend_unliked_movies import register_tools

PORT = int(os.environ.get("PORT", "8000"))

mcp = FastMCP(
    "Movie Recommendation MCP",
    host="0.0.0.0",
    port=PORT,
)

register_tools(mcp)

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
