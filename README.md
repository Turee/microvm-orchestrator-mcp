# microvm-orchestrator-mcp

MCP server for orchestrating parallel execution of development tasks in isolated microVM Claude instances.

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Target Repository Requirements](#target-repository-requirements)
- [Installation](#installation)
- [Running the Server](#running-the-server)
- [CLI Reference](#cli-reference)
- [MCP Tools Reference](#mcp-tools-reference)
- [Parallel Execution](#parallel-execution)
- [File Locations](#file-locations)
- [Result Formats](#result-formats)
- [Manual Conflict Resolution](#manual-conflict-resolution)
- [Troubleshooting](#troubleshooting)
- [Performance & Limitations](#performance--limitations)
- [Security Considerations](#security-considerations)
- [Development](#development)
- [License](#license)

## Features

- **Parallel Task Execution**: Run multiple Claude instances in isolated NixOS microVMs simultaneously
- **Full Agent Autonomy**: Claude runs with `--dangerously-skip-permissions` for unrestricted development
- **Git Isolation**: Each task runs in an isolated git repository clone to prevent conflicts
- **Automatic Merging**: Commits are rebased and merged back to your branch after task completion
- **Docker/Podman Support**: Rootless Podman with Docker CLI compatibility inside VMs
- **Rosetta 2 Support**: Run x86_64 binaries on Apple Silicon via transparent translation
- **Persistent Storage**: Container images and Nix store cached across tasks via slots
- **Multi-Repo Support**: Register multiple repositories and run tasks against any of them from a single server
- **Automatic Slot Assignment**: Slots assigned automatically with repo affinity for cache reuse

## Architecture

```
┌───────────────────────────────────────────────────────────────────┐
│ Host (macOS)                                                      │
│                                                                   │
│  ┌──────────────────────────────────────────────────────────────┐ │
│  │ MCP Server (127.0.0.1:8765)                                  │ │
│  │   • RepoRegistry - CLI-managed repo allowlist                │ │
│  │   • SlotManager  - automatic slot assignment w/ affinity     │ │
│  │   • Orchestrator - manages task lifecycle                    │ │
│  │   • Event queue  - async completion notifications            │ │
│  │   • Git isolation - clones target repo per task              │ │
│  └────────────────────────┬─────────────────────────────────────┘ │
│                           │ spawns via nix-build + vfkit          │
│   Registered repos:       ▼                                      │
│   ┌──────────┐  ┌──────────────────┐  ┌──────────────────┐       │
│   │ project-a│  │  MicroVM Slot 1  │  │  MicroVM Slot 2  │ ...   │
│   │ project-b│  │  ┌────────────┐  │  │  ┌────────────┐  │       │
│   │ project-c│  │  │ NixOS      │  │  │  │ NixOS      │  │       │
│   └──────────┘  │  │ Claude Code│  │  │  │ Claude Code│  │       │
│        │        │  │ nix develop│  │  │  │ nix develop│  │       │
│        │        │  │ Podman     │  │  │  │ Podman     │  │       │
│        └───────►│  └────────────┘  │  │  └────────────┘  │       │
│     clone to    │  --skip-perms    │  │  --skip-perms    │       │
│     /workspace  └──────────────────┘  └──────────────────┘       │
│                                                                   │
│  Mounts per VM:                                                   │
│  • /workspace/repo - isolated git clone of target repo            │
│  • /nix/store (RO) - host Nix store                               │
│  • /nix/.rw-store - writable overlay (sparse, 30GB max)           │
│  • /var - persistent slot storage                                 │
│  • /var/lib/containers - Podman image cache                       │
└───────────────────────────────────────────────────────────────────┘
```

### Task Lifecycle

1. **Create**: `run_task()` resolves the repo alias, clones it to `.microvm/tasks/<id>/repo/`
2. **Slot**: `SlotManager` assigns a slot automatically (prefers same slot for same repo)
3. **Boot**: NixOS microVM starts with repo mounted at `/workspace/repo`
4. **Execute**: `nix develop` loads your flake, then Claude Code runs the task
5. **Commit**: Claude commits changes to the isolated repo
6. **Merge**: Orchestrator rebases commits onto your branch automatically
7. **Cleanup**: VM shuts down, slot is released, task directory can be removed

## Prerequisites

- **macOS on Apple Silicon** (vfkit hypervisor requirement)
- **Nix with flakes enabled**:
  ```bash
  # Verify flakes are enabled
  nix --version  # Should show 2.4+
  grep experimental-features ~/.config/nix-darwin/nix.conf  # Should include "flakes"
  ```
- **nix-darwin with Linux builder** (for building aarch64-linux VMs)
- **Python 3.13+**
- **Rosetta 2** (optional, for x86_64 binary support):
  ```bash
  softwareupdate --install-rosetta
  ```

## Quick Start

```bash
# 1. Register your project (must have flake.nix and be a git repo)
microvm-orchestrator allow /path/to/your/project

# 2. Start the MCP server
microvm-orchestrator serve

# 3. Configure Claude Code (see Installation section)

# 4. In Claude Code, use the MCP tools:
#    "Use run_task to add unit tests for the auth module in your-project"
```

## Target Repository Requirements

Your repository must contain a `flake.nix` at the root with a `devShells.default` output.
This defines the development environment for Claude Code running inside the microVM.

### Example flake.nix

```nix
{
  description = "Project Development Environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
      in
      {
        devShells.default = pkgs.mkShell {
          buildInputs = with pkgs; [
            # Add your tools here
            nodejs_22
            bun
            jdk21
            # etc.
          ];

          shellHook = ''
            echo "Development environment loaded"
          '';
        };
      }
    );
}
```

### How It Works

1. The orchestrator **clones your repository** to an isolated working directory
2. The microVM boots and mounts the **cloned repo** at `/workspace/repo`
3. The task runner executes: `nix develop . --command <claude-code>`
4. Nix fetches inputs, builds devShell, and launches Claude with your tools available
5. Claude can modify `flake.nix` to add dependencies, then `nix develop` again
6. Changes are committed to the clone and merged back after task completion

### Dynamic Dependencies

Claude can add tools during a task by editing `flake.nix`. For example, to add
PostgreSQL client tools, Claude would add `pkgs.postgresql` to `buildInputs`
and re-enter the shell with `nix develop`.

## Installation

### Install the CLI

**Using uv (recommended):**
```bash
uv tool install git+https://github.com/anthropics/microvm-orchestrator-mcp
```

**Using pipx:**
```bash
pipx install git+https://github.com/anthropics/microvm-orchestrator-mcp
```

**From source (for development):**
```bash
git clone https://github.com/anthropics/microvm-orchestrator-mcp
cd microvm-orchestrator-mcp
uv sync
```
When running from source, prefix commands with `uv run` (e.g. `uv run microvm-orchestrator serve`).

### Configure Claude Code

Add to your MCP configuration (`~/.config/claude-code/mcp.json`):

```json
{
  "microvm": {
    "type": "http",
    "url": "http://127.0.0.1:8765/mcp"
  }
}
```

The server uses HTTP transport (not stdio) so you can cancel MCP queries without restarting the server — important for long-running VM tasks.

### Register Repositories

Before running tasks, register repositories via the CLI:

```bash
# Register a repo (alias defaults to directory name)
microvm-orchestrator allow /path/to/your/project

# Register with a custom alias
microvm-orchestrator allow /path/to/your/project --alias myproject

# Verify registration
microvm-orchestrator list
```

## Running the Server

```bash
microvm-orchestrator serve
```

The server listens on `http://127.0.0.1:8765` and serves all registered repositories.

## CLI Reference

### `microvm-orchestrator allow [PATH] [--alias ALIAS]`

Register a repository for use with microvm tasks.

- `PATH`: Path to a git repository (default: current directory)
- `--alias`, `-a`: Custom alias for the repo (default: directory name)

```bash
microvm-orchestrator allow /path/to/project
# Registered: project

microvm-orchestrator allow /path/to/project --alias myapp
# Registered: myapp
```

### `microvm-orchestrator list`

List all registered repositories.

```bash
microvm-orchestrator list
#   myapp: /path/to/project
#   backend: /path/to/backend
```

### `microvm-orchestrator remove ALIAS`

Remove a repository from the allowlist.

```bash
microvm-orchestrator remove myapp
# Removed: myapp
```

### `microvm-orchestrator serve`

Start the MCP server. Serves all registered repositories.

## MCP Tools Reference

### run_task

Start a new task in an isolated microVM.

```
run_task(description: str, repo: str) -> {"task_id": str}
```

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `description` | str | Full task instructions for Claude in the VM. Include all context needed. |
| `repo` | str | Repository alias (registered via CLI). Use `list_repos()` to see available repos. |

**Returns:** `{"task_id": "abc123"}` or `{"error": "message"}`

**Example:**
```python
run_task(
    description="Add unit tests for the auth module. Follow existing test patterns in tests/.",
    repo="myproject"
)
```

**Notes:**
- If the task involves Docker, include "use --network=host" in the description
- First run on a new slot takes longer (creates nix store overlay)
- Slots are assigned automatically - no need to specify a slot number

---

### get_task_info

Get information about a task including status, result, and merge result.

```
get_task_info(task_id: str) -> dict
```

**Returns:**
```json
{
  "status": "completed",
  "result": {
    "success": true,
    "summary": "Added 5 unit tests...",
    "files_changed": ["tests/auth.test.ts"],
    "error": null
  },
  "merge_result": {
    "merged": true,
    "method": "fast-forward",
    "commits": 2,
    "conflicts": []
  },
  "pid": 12345,
  "exit_code": 0
}
```

**Status values:** `pending`, `running`, `completed`, `failed`

---

### get_task_logs

Get path to task's serial console log file.

```
get_task_logs(task_id: str) -> {"log_path": str}
```

**Usage:**
```bash
# Stream logs in real-time
tail -f <log_path>

# View full log
cat <log_path>
```

---

### wait_next_event

Block until any task completes or fails.

```
wait_next_event(timeout_ms: int = 1800000) -> dict
```

**Parameters:**
| Name | Type | Default | Description |
|------|------|---------|-------------|
| `timeout_ms` | int | 1800000 | Timeout in milliseconds (30 minutes). Use longer values for long-running tasks. |

**Returns:**
```json
{
  "type": "completed",
  "task_id": "abc123",
  "result": { ... },
  "merge_result": { ... }
}
```

Or on timeout: `{"timeout": true}`

**Pattern for multiple tasks:**
```python
# Start multiple tasks
run_task("Task A", repo="frontend")
run_task("Task B", repo="backend")

# Wait for each to complete
event1 = wait_next_event(timeout_ms=300000)  # 5 min
event2 = wait_next_event(timeout_ms=300000)
```

---

### cleanup_task

Clean up task directory and optionally delete git ref.

```
cleanup_task(task_id: str, delete_ref: bool = False) -> {"success": bool}
```

**Parameters:**
| Name | Type | Default | Description |
|------|------|---------|-------------|
| `task_id` | str | required | Task ID to clean up |
| `delete_ref` | bool | false | Also delete `refs/tasks/<task_id>` from git |

---

### list_repos

List registered repositories that can be used with `run_task`.

```
list_repos() -> {"repos": [{"alias": str, "path": str, "added": str}, ...]}
```

**Example response:**
```json
{
  "repos": [
    {"alias": "myproject", "path": "/Users/me/projects/myproject", "added": "2025-01-15T10:30:00+00:00"},
    {"alias": "backend", "path": "/Users/me/projects/backend", "added": "2025-01-15T10:31:00+00:00"}
  ]
}
```

---

### list_tasks

List all tasks across all registered repos.

```
list_tasks() -> {"tasks": [{"task_id": str, "status": str, "description": str, "repo": str}, ...]}
```

---

### list_slots

Show slot status and availability.

```
list_slots() -> {"max_slots": int, "active": [...], "available": [int, ...]}
```

**Example response:**
```json
{
  "max_slots": 10,
  "active": [
    {"slot": 1, "task_id": "abc123"},
    {"slot": 3, "task_id": "def456"}
  ],
  "available": [2, 4, 5, 6, 7, 8, 9, 10]
}
```

## Parallel Execution

Slots are assigned automatically with repo affinity. Just call `run_task` with any registered repo — the `SlotManager` handles the rest:

```python
# Run tasks across different repos — slots assigned automatically
run_task(description="Add auth tests", repo="frontend")
run_task(description="Add API endpoint", repo="backend")
run_task(description="Update docs", repo="frontend")

# Wait for results
event1 = wait_next_event(timeout_ms=300000)
event2 = wait_next_event(timeout_ms=300000)
event3 = wait_next_event(timeout_ms=300000)
```

**How slot assignment works:**
- Each repo gets a "preferred" slot based on a hash of its path (deterministic)
- If the preferred slot is free, it's used — this maximizes Nix store and container cache reuse
- If the preferred slot is busy, any free slot is used instead
- Maximum 10 slots. If all are busy, `run_task` returns an error — wait for tasks to complete or check `list_slots()`

**Slot storage location:** `~/.microvm-orchestrator/slots/<N>/`

Slot storage persists across tasks, so container images and Nix packages are cached.

## File Locations

| Path | Description |
|------|-------------|
| `~/.microvm-orchestrator/allowed-repos.json` | Registered repos allowlist |
| `~/.microvm-orchestrator/slot-assignments.json` | Repo-to-slot affinity mapping |
| `.microvm/tasks/<id>/` | Task working directory |
| `.microvm/tasks/<id>/task.json` | Task metadata and state |
| `.microvm/tasks/<id>/task.md` | Original task description |
| `.microvm/tasks/<id>/repo/` | Isolated git repository clone |
| `.microvm/tasks/<id>/serial.log` | VM console output |
| `.microvm/tasks/<id>/result.json` | Task result from Claude |
| `.microvm/tasks/<id>/merge-result.json` | Git merge outcome |
| `.microvm/tasks/<id>/claude-stream.jsonl` | Claude Code JSON output stream |
| `~/.microvm-orchestrator/slots/<N>/` | Persistent slot storage |
| `~/.microvm-orchestrator/slots/<N>/var/` | Persistent /var for systemd, logs |
| `~/.microvm-orchestrator/slots/<N>/container-storage/` | Podman image cache |
| `~/.microvm-orchestrator/slots/<N>/nix-store.img` | Writable Nix store overlay |

## Result Formats

### Task Result (`result.json`)

```json
{
  "success": true,
  "summary": "Claude's full explanation of what was done",
  "files_changed": ["src/auth.ts", "tests/auth.test.ts"],
  "error": null
}
```

### Merge Result (`merge-result.json`)

Success:
```json
{
  "merged": true,
  "method": "fast-forward",
  "commits": 2,
  "conflicts": []
}
```

Conflict:
```json
{
  "merged": false,
  "reason": "conflicts",
  "conflicts": ["src/shared.ts"],
  "task_ref": "refs/tasks/<id>"
}
```

## Manual Conflict Resolution

If automatic merge fails, commits are preserved at `refs/tasks/<task-id>`. You can:

1. Use the conflict-resolver agent
2. Manually cherry-pick or rebase:

```bash
# View task commits
git log refs/tasks/<task-id>

# Cherry-pick
git cherry-pick refs/tasks/<task-id>

# Or rebase
git checkout -b temp refs/tasks/<task-id>
git rebase main
git checkout main
git merge --ff-only temp
git branch -d temp
```

## Troubleshooting

### Common Errors

| Error | Cause | Solution |
|-------|-------|----------|
| "Repo 'x' not registered" | Repo alias not in allowlist | Run `microvm-orchestrator allow /path/to/repo` |
| "All 10 slots are busy" | No free slots available | Wait for tasks to complete or check `list_slots()` |
| "No flake.nix found" | Target repo missing flake | Add `flake.nix` with `devShells.default` |
| "No API key found" | Missing environment variable | Set `ANTHROPIC_API_KEY` or run `claude /login` |
| "nix-build failed" | Flake evaluation error | Run `nix flake check` in your repo |
| "Network is not ready" | VM can't reach internet | Check host network connectivity |
| Task timeout | Long-running task | Increase `timeout_ms` in `wait_next_event` |
| "x86_64-linux not supported" | Missing Rosetta | Run `softwareupdate --install-rosetta` |

### Debugging Tips

**Check task state:**
```bash
cat .microvm/tasks/<id>/task.json | jq .status
```

**View Claude's output:**
```bash
tail -100 .microvm/tasks/<id>/serial.log
```

**Stream logs in real-time:**
```bash
tail -f .microvm/tasks/<id>/serial.log
```

**Check task result:**
```bash
cat .microvm/tasks/<id>/result.json | jq .
```

**Check merge result:**
```bash
cat .microvm/tasks/<id>/merge-result.json | jq .
```

**View Claude's stream output:**
```bash
cat .microvm/tasks/<id>/claude-stream.jsonl | jq -s .
```

### VM Boot Issues

- **First boot is slow**: Creating the Nix store overlay (~30GB sparse file) takes time
- **Rosetta errors**: Install with `softwareupdate --install-rosetta`
- **Build failures**: Check `nix flake check` on your target repo

## Performance & Limitations

| Metric | Value |
|--------|-------|
| First task per slot | 1-2 minutes (creates Nix store overlay) |
| Subsequent tasks | 30-60 seconds (VM boot + task execution) |
| Storage per slot | ~2-5 GB actual (30 GB max, sparse file) |
| VM resources | 4 vCPU, 4 GB RAM (hardcoded) |
| Platform | macOS Apple Silicon only (vfkit) |
| Max parallel slots | 10 (limited by host RAM, 4 GB per VM) |

**Note on storage**: The `nix-store.img` is a sparse file - it reports 30GB but only uses actual disk space for data written (~2GB typical).

## Security Considerations

- **Full agent autonomy**: VMs run Claude with `--dangerously-skip-permissions` for unrestricted development
- **API key handling**: Keys are written to a temp file, read once, then deleted immediately
- **Network access**: VMs have full internet access for npm, API calls, etc.
- **Git isolation**: Tasks work on clones, never modifying your original repo directly
- **Hardware isolation**: Complete VM isolation via vfkit hypervisor
- **Repo allowlist**: Only explicitly registered repositories can be used

## Development

See [AGENTS.md](AGENTS.md) for development guidelines and contribution information.

```bash
# Run tests
uv run pytest

# Run with coverage
uv run pytest --cov
```

## License

MIT
