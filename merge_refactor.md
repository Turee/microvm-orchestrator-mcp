# Replace Auto-Merge with Branch-Based Workflow

## Context

Currently, when a task VM completes successfully, the orchestrator automatically merges task commits back into the original repo's HEAD using fast-forward or rebase strategies (`core/git.py:merge_task_commits`). This removes the user's ability to review changes before they land on the main branch.

The change replaces auto-merge with creating a named branch (`task/<task-id>`) in the original repo. Users can then review and merge at their discretion (via `git merge`, PR, etc.).

## Approach

Remove the merge logic entirely (not keep as option). The branch workflow is strictly more capable -- users who want auto-merge can just run `git merge task/<id>`.

## Changes

### 1. `src/microvm_orchestrator/core/git.py` -- Core logic
- **Remove:** `MergeResult` dataclass, `merge_task_commits()` function (~90 lines of ff/rebase/conflict logic)
- **Add:** `BranchResult` dataclass with fields: `branch`, `commits`, `created`, `reason`
- **Add:** `create_task_branch()` -- fetches task commits into `refs/heads/<branch_name>` in original repo (making it a visible branch). No merge attempt.
- **Rename:** `cleanup_task_ref()` -> `cleanup_task_branch()` (deletes branch by name instead of ref)

### 2. `src/microvm_orchestrator/core/task.py` -- Task model
- Add `branch_name: Optional[str] = None` field (serialized in save/load)
- Rename `merge_result_path` -> `branch_result_path` (file: `branch-result.json`)
- Rename `get_merge_result()` -> `get_branch_result()`

### 3. `src/microvm_orchestrator/core/events.py` -- Events
- Rename `TaskEvent.merge_result` -> `branch_result`
- Update `to_dict()` and `create_completed_event()` signature

### 4. `src/microvm_orchestrator/tools.py` -- Orchestrator
- Update imports: `merge_task_commits` -> `create_task_branch`, `cleanup_task_ref` -> `cleanup_task_branch`
- `_on_task_exit()`: Call `create_task_branch()` instead of `merge_task_commits()`, pass `task.branch_name`
- `get_task_info()`: Return `branch_result` instead of `merge_result`
- `cleanup_task()`: Use `cleanup_task_branch()`, rename param `delete_ref` -> `delete_branch`
- `run_task()`: Accept optional `branch: str` param, store on task

### 5. `src/microvm_orchestrator/server.py` -- MCP tools
- `run_task`: Add `branch` parameter (optional, defaults to `task/<task-id>`)
- `cleanup_task`: Rename `delete_ref` -> `delete_branch`
- Update docstrings

### 6. Tests (all files)
- `tests/conftest.py`: Update fixtures (`merge_result_success` -> `branch_result_success`, `mock_orchestrator_deps` patches)
- `tests/test_git.py`: Remove merge tests, add `TestCreateTaskBranch`, `TestBranchResult`, `TestCleanupTaskBranch`
- `tests/test_tools.py`: Patch `create_task_branch` instead of `merge_task_commits`
- `tests/test_events.py`: `merge_result=` -> `branch_result=`
- `tests/test_task.py`: Rename method/property tests
- `tests/test_integration.py`: Update patch target
- `tests/test_e2e.py`: Check branch exists instead of checking file merged into working tree
- `tests/test_nix_develop.py`: Check `branch_result` instead of `merge_result`

### 7. `README.md`
- Replace merge documentation with branch workflow docs
- Update response examples and parameter docs

## Execution Order

1. `core/git.py` (foundation)
2. `core/task.py` (model)
3. `core/events.py` (events)
4. `tools.py` (orchestrator)
5. `server.py` (MCP interface)
6. `tests/conftest.py` then all test files
7. `README.md`

## Verification

1. Run `python -m pytest tests/` -- all tests pass
2. If a VM environment is available: run a task via MCP and verify:
   - Task branch appears in `git branch` output
   - Branch contains the expected commits
   - Original repo HEAD is unchanged
   - `get_task_info` returns `branch_result` with correct branch name
   - `cleanup_task(delete_branch=True)` removes the branch
