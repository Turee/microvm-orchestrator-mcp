# Test Plan: microvm-orchestrator-mcp

## Philosophy

- **Atomic**: Each test verifies ONE specific behavior
- **Fast**: Unit tests mock external dependencies (nix, git, VM processes)
- **Real**: Integration tests spawn actual microVMs for end-to-end verification

## Directory Structure

```
tests/
├── conftest.py              # Shared fixtures
├── test_task.py             # Task state machine (~15 tests)
├── test_events.py           # Event queue async (~12 tests)
├── test_git.py              # Git operations (~20 tests)
├── test_vm.py               # VM process (~15 tests)
├── test_tools.py            # Orchestrator (~15 tests)
├── test_server.py           # MCP tools (~10 tests)
└── fixtures/
    └── mocks.py             # Subprocess/PTY mock helpers
```

## Module Test Specifications

### Task State Machine (`core/task.py`)

**States:** PENDING → RUNNING → COMPLETED|FAILED

| Test | Description |
|------|-------------|
| `test_initial_state_pending` | New task starts in PENDING |
| `test_transition_pending_to_running` | Valid transition |
| `test_transition_running_to_completed` | Exit code 0 |
| `test_transition_running_to_failed` | Exit code non-zero |
| `test_invalid_transition_completed_to_running` | Raises error |
| `test_invalid_transition_failed_to_running` | Raises error |
| `test_thread_safe_state_updates` | Concurrent mark_running() |
| `test_save_load_roundtrip` | JSON persistence |
| `test_timestamps_set_correctly` | created_at, started_at, completed_at |
| `test_path_properties` | task_dir, repo_path, log_path |

**Mocking:** `Path.write_text()`, `uuid.uuid4()`, `datetime.now()`

### Event Queue (`core/events.py`)

| Test | Description |
|------|-------------|
| `test_emit_and_pop` | Basic FIFO behavior |
| `test_wait_returns_event` | Sync wait succeeds |
| `test_wait_timeout` | Returns None after timeout |
| `test_wait_async_returns_event` | Async version |
| `test_wait_async_timeout` | Async timeout handling |
| `test_wait_async_cancellation` | CancelledError propagates |
| `test_thread_safe_emission` | Multiple threads emitting |
| `test_multiple_waiters` | Two consumers |
| `test_create_completed_event` | Factory function |
| `test_create_failed_event` | Factory function |

**Mocking:** `asyncio.get_running_loop()`, `time.monotonic()`

### Git Operations (`core/git.py`)

| Test | Description |
|------|-------------|
| `test_setup_isolated_repo_success` | Creates clone with remotes |
| `test_setup_isolated_repo_fetch_failure` | Falls back to archive |
| `test_merge_fast_forward` | Simple merge path |
| `test_merge_rebase_required` | Non-ff merge |
| `test_merge_conflict_detected` | Conflict preserved at ref |
| `test_merge_no_new_commits` | Early return |
| `test_cleanup_task_ref` | Removes refs/tasks/<id> |
| `test_run_git_success` | subprocess wrapper |
| `test_run_git_failure` | Non-zero exit |
| `test_get_current_ref` | rev-parse HEAD |
| `test_get_current_branch` | symbolic-ref |

**Mocking:** `subprocess.run()` returning `CompletedProcess`

### VM Process (`core/vm.py`)

| Test | Description |
|------|-------------|
| `test_build_vm_success` | nix-build returns path |
| `test_build_vm_failure` | nix-build fails |
| `test_find_runner` | Locates microvm-run script |
| `test_write_task_files` | task.md, start-ref, task-id |
| `test_api_key_file_permissions` | 0o600 chmod |
| `test_vmprocess_start` | Spawns process with PTY |
| `test_vmprocess_monitor_logs` | Writes to serial.log |
| `test_vmprocess_stop` | Terminates cleanly |
| `test_vmprocess_on_exit_callback` | Fires with exit code |
| `test_prepare_vm_env` | Correct env vars |
| `test_ensure_slot_initialized` | Creates slot dirs |

**Mocking:** `subprocess.Popen`, `pty.openpty()`, `os.read()`, `select.select()`

### Orchestrator (`tools.py`)

| Test | Description |
|------|-------------|
| `test_run_task_creates_task` | Returns task_id |
| `test_run_task_detects_git_root` | Finds .git directory |
| `test_run_task_api_key_from_env` | Uses ANTHROPIC_API_KEY |
| `test_run_task_marks_failed_on_error` | Exception handling |
| `test_get_task_info_running` | Status while VM active |
| `test_get_task_info_completed` | Includes result.json |
| `test_wait_next_event_returns` | Event received |
| `test_wait_next_event_no_tasks` | Early return |
| `test_cleanup_task_removes_files` | Directory deleted |
| `test_cleanup_task_deletes_ref` | Git ref removed |
| `test_concurrent_tasks` | Multiple slots |

**Mocking:** VMProcess, git module functions

### MCP Server (`server.py`)

| Test | Description |
|------|-------------|
| `test_run_task_returns_dict` | Correct format |
| `test_run_task_error_format` | {"error": str} |
| `test_get_task_info_returns_dict` | Correct format |
| `test_wait_next_event_timeout` | {"timeout": true} |
| `test_wait_next_event_cancelled` | {"cancelled": true} |
| `test_cleanup_task_success` | {"deleted": true} |

**Mocking:** `get_orchestrator()` singleton

## Success Criteria

- [ ] All unit tests pass in < 30 seconds
- [ ] Code coverage > 80% on core modules
- [ ] No test interdependencies
- [ ] Integration tests pass (separate pytest mark)
