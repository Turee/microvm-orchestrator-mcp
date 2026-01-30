"""MCP Server implementation using the official MCP SDK."""

from __future__ import annotations

import asyncio
import os
from typing import Optional

from mcp.server.fastmcp import FastMCP

from .tools import Orchestrator, ToolError


# Create server instance using FastMCP for decorator-based tool registration
# stateless_http=True allows server restarts without breaking Claude Code sessions
mcp = FastMCP("microvm-orchestrator", host="127.0.0.1", port=8765, stateless_http=True)

# Global orchestrator instance (initialized on first tool call)
_orchestrator: Optional[Orchestrator] = None


def get_orchestrator() -> Orchestrator:
    """Get or create the orchestrator instance."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator()
    return _orchestrator


@mcp.tool()
async def run_task(description: str, slot: int = 1) -> dict:
    """Start a new task in an isolated microVM.

    Args:
        description: Task description/instructions for Claude in the VM.
            If the task involves running Docker containers, include
            instructions to use --network=host (required for networking
            to work correctly inside the microVM).
        slot: Slot number for persistent storage (1-N). Different slots
            for parallel tasks.

    Returns:
        {"task_id": str}
    """
    try:
        orchestrator = get_orchestrator()
        return await orchestrator.run_task(description, slot)
    except ToolError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_task_info(task_id: str) -> dict:
    """Get information about a task including status, result, and merge result.

    Args:
        task_id: Task ID returned by run_task

    Returns:
        Task information including status, result, etc.
    """
    try:
        orchestrator = get_orchestrator()
        return orchestrator.get_task_info(task_id)
    except ToolError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_task_logs(task_id: str) -> dict:
    """Get path to task's serial console log file. Use shell tools (tail -f, cat) to read.

    Args:
        task_id: Task ID

    Returns:
        {"log_path": str}
    """
    try:
        orchestrator = get_orchestrator()
        return orchestrator.get_task_logs(task_id)
    except ToolError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def wait_next_event(timeout_ms: int = 30000) -> dict:
    """Block until any task completes or fails. Returns event with result and merge info. Use long timeout for long-running tasks.

    This is an async operation that can be cancelled.

    Args:
        timeout_ms: Timeout in milliseconds

    Returns:
        Event information or {"timeout": true}
    """
    try:
        orchestrator = get_orchestrator()
        return await orchestrator.wait_next_event(timeout_ms)
    except ToolError as e:
        return {"error": str(e)}
    except asyncio.CancelledError:
        return {"cancelled": True}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def cleanup_task(task_id: str, delete_ref: bool = False) -> dict:
    """Clean up task directory and optionally delete git ref.

    Args:
        task_id: Task ID
        delete_ref: Whether to delete refs/tasks/<task_id>

    Returns:
        {"success": bool}
    """
    try:
        orchestrator = get_orchestrator()
        return await orchestrator.cleanup_task(task_id, delete_ref)
    except ToolError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}


def run():
    """Entry point for running the MCP server."""
    print(f"Working directory: {os.getcwd()}")
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    run()
