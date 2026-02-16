# Rich TUI for Live VM Monitoring

## Context
Epic `microvm-orchestrator-mcp-axp`: Users need real-time visibility into what subagents are doing inside microVMs without SSH or CLI tools. We're building a Rich-based TUI that launches alongside the MCP server, showing task status and live-streamed VM logs with easy keyboard navigation.

## Architecture

**Threading model**: MCP server runs in a daemon thread, TUI owns the main thread (required for terminal raw input and signal handling on macOS). Both share the same `Orchestrator` instance.

**Module structure**:
```
src/microvm_orchestrator/tui/
  __init__.py       # Exports start_tui()
  app.py            # TUIApp - main loop with Rich Live + Layout
  components.py     # TaskListPanel, LogViewerPanel (Rich renderables)
  input.py          # Non-blocking keyboard reader (cbreak mode)
  tail.py           # LogTailer - incremental file tailing with deque buffer
```

**Layout**:
```
+---------------------+-----------------------------+
| Task List (1/3)     | Log Viewer (2/3)            |
| # ID   Status Slot  | [serial.log content for     |
| > 1 abc RUNNING  2  |  selected task, auto-scroll]|
|   2 def PENDING  -  |                             |
+---------------------+-----------------------------+
| [q]uit [j/k]nav [1-9]select                       |
+---------------------------------------------------+
```

**Key bindings**: `j/k` or arrows to navigate tasks, `1-9` to jump, `q` to quit.

## Subtasks (atomic, verifiable)

### 1. Add Rich dependency + tui package skeleton
- Add `rich>=13.0` to `pyproject.toml` dependencies
- Create `tui/` package with empty module files
- **Verify**: `python -c "from microvm_orchestrator.tui import start_tui"` succeeds

### 2. Implement LogTailer (`tui/tail.py`)
- Incremental file tailing via `seek()` to last position
- `deque(maxlen=200)` bounds memory; `errors='replace'` for binary safety
- **Verify**: Unit test - write to temp file incrementally, assert `get_lines()` returns correct rolling content

### 3. Implement InputReader (`tui/input.py`)
- `cbreak` mode (not raw - preserves Ctrl+C), non-blocking `select()` reads
- Arrow key escape sequence parsing, terminal restore in `stop()`
- **Verify**: Unit test with mocked stdin; manual test script that prints detected keys

### 4. Implement Rich components (`tui/components.py`)
- `build_task_table(tasks, selected_index)` - Rich Table with status-colored rows, selected row highlighted
- `build_log_panel(lines, task_id)` - Rich Panel wrapping log content
- `build_status_bar()` - keybinding hints
- **Verify**: Unit test renders to `Console(file=StringIO())`, asserts expected task IDs/status in output

### 5. Implement TUIApp main loop (`tui/app.py`)
- `Rich.Live(layout, refresh_per_second=10, screen=True)` on alternate screen buffer
- Main loop: poll input -> update state -> components render automatically
- Reads `orchestrator._tasks` and `_processes` (GIL-safe read-only access)
- **Verify**: Run with mock Orchestrator, verify display renders, navigation works, `q` exits, terminal restored

### 6. Integrate TUI with MCP server startup
- Modify `server.py:run()` to accept `headless` param, start MCP in daemon thread, TUI on main
- Add `--no-tui` flag to `cli.py` `serve` command
- Suppress uvicorn logs to stderr when TUI active
- **Verify**: `microvm-orchestrator serve` shows TUI + MCP responds on 8765; `--no-tui` works headless; Ctrl+C exits cleanly

### 7. Pretty-print Claude JSONL log output
- Parse Claude's JSONL log format in LogTailer
- Render structured log entries with syntax highlighting and formatting
- **Verify**: Unit test with sample JSONL content, visual confirmation of formatted output

### 8. End-to-end integration test
- Start server, trigger `run_task` via MCP, observe TUI shows task PENDING->RUNNING, log streams, status updates on completion
- **Verify**: Visual confirmation of live log streaming and status transitions

## Files to modify
- `pyproject.toml` - add `rich` dependency
- `src/microvm_orchestrator/server.py` - thread MCP server, launch TUI
- `src/microvm_orchestrator/cli.py` - `--no-tui` flag

## Files to create
- `src/microvm_orchestrator/tui/__init__.py`
- `src/microvm_orchestrator/tui/app.py`
- `src/microvm_orchestrator/tui/components.py`
- `src/microvm_orchestrator/tui/input.py`
- `src/microvm_orchestrator/tui/tail.py`
- `tests/test_tui.py` (or `tests/test_tui_*.py` per component)

## Key design decisions
- **Rich over Textual**: User requested Rich specifically. Keyboard navigation handled manually via cbreak+select.
- **Daemon thread for MCP**: Clean exit - killing TUI (main thread) kills everything. No orphan server processes.
- **Read-only orchestrator access**: TUI never modifies state, just reads `_tasks`/`_processes`. GIL makes this safe.
- **Incremental log tailing**: Only reads new bytes per refresh cycle. Bounded deque prevents memory growth.

## Verification
1. Unit tests: `pytest tests/test_tui*.py`
2. Manual: `microvm-orchestrator serve` shows TUI, navigate tasks, view logs
3. Integration: Run a real task, verify live log streaming and status transitions
