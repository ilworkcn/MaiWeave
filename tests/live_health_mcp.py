"""BOT-02 本地聊天室验收用的只读 MCP，每次查询记录一行。"""

from pathlib import Path

from mcp.server.fastmcp import FastMCP

server = FastMCP("生活日程验收")


@server.tool()
def read_health_status() -> str:
    """查询测试用户的今日健康记录和睡眠准备情况。"""
    with Path(__file__).with_suffix(".calls").open("a", encoding="utf-8") as stream:
        stream.write("查询健康\n")
    return "测试记录：今天步行 3210 步。"


if __name__ == "__main__":
    server.run()
