import asyncio

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from app.config import settings


MCP_URL = "http://127.0.0.1:8000/mcp"
_tools: list[BaseTool] | None = None
_lock = asyncio.Lock()


async def get_mcp_tools() -> list[BaseTool]:
    global _tools
    # Feature flags determine the tool set at import; a restart is its invalidation.
    if _tools is None:
        async with _lock:
            if _tools is None:
                _tools = await _load_tools()
    return _tools


def tools_cached() -> bool:
    return _tools is not None


async def _load_tools() -> list[BaseTool]:
    client = MultiServerMCPClient(
        {
            "codejob": {
                "url": MCP_URL,
                "transport": "streamable_http",
                # The SDK default is 30s, which no tool needed until
                # propose_taxonomy_bulk_review started calling DeepSeek over
                # hundreds of pending records. It is a ceiling, not a delay:
                # every other tool still returns as fast as it always did.
                "timeout": settings.chat_tool_timeout_seconds,
            }
        }
    )
    return await client.get_tools()
