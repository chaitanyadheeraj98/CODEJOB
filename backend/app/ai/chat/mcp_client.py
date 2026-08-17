from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient


MCP_URL = "http://127.0.0.1:8000/mcp"


async def get_mcp_tools() -> list[BaseTool]:
    client = MultiServerMCPClient(
        {"codejob": {"url": MCP_URL, "transport": "streamable_http"}}
    )
    return await client.get_tools()
