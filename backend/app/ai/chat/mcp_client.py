from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient


MCP_URL = "http://127.0.0.1:8000/mcp"
MCP_TIMEOUT_SECONDS = 180.0


async def get_mcp_tools() -> list[BaseTool]:
    client = MultiServerMCPClient(
        {
            "codejob": {
                "url": MCP_URL,
                "transport": "streamable_http",
                # The SDK default is 30s, which no tool needed until
                # propose_taxonomy_bulk_review started calling DeepSeek over
                # hundreds of pending records. It is a ceiling, not a delay:
                # every other tool still returns as fast as it always did.
                "timeout": MCP_TIMEOUT_SECONDS,
            }
        }
    )
    return await client.get_tools()
