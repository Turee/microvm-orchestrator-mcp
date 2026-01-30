# microvm-orchestrator-mcp

MCP server for orchestrating parallel execution of development tasks in isolated microVM Claude instances.

## Features

- **Parallel Task Execution**: Run multiple Claude instances in isolated NixOS microvms simultaneously
- **Smart Dependency Analysis**: Automatically determines which tasks can run in parallel vs. sequentially
- **Git Isolation**: Each task runs in an isolated git repository to prevent conflicts
- **Automatic Merging**: Commits are rebased and merged back to your branch after task completion
- **Conflict Resolution**: Semantic merge when parallel tasks modify the same files

## Prerequisites

- Nix with flakes enabled
- nix-darwin with Linux builder (for macOS hosts)
- Git repository (tasks run in isolated git clones)
- Python 3.13+

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

```bash
# Enable the MCP server in Claude Code settings
# Add to ~/.config/claude-code/mcp.json:
{
  "microvm": {
    "type": "http",
    "url": "http://127.0.0.1:8765/mcp"
  }
}
```

## Running the Server

```bash
# Install dependencies
uv sync

# Start the MCP server
python -m microvm_orchestrator
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `run_task` | Start a task in a microVM, returns task_id immediately |
| `get_task_info` | Get task status, result, and merge result |
| `get_task_logs` | Get path to serial console log file |
| `wait_next_event` | Block until a task completes or fails |
| `cleanup_task` | Remove task directory, optionally delete git ref |

## How It Works

1. **Parse tasks**: Reads task files and identifies individual tasks
2. **Analyze dependencies**: Task-analyzer agent determines parallel vs sequential execution
3. **Launch VMs**: Each task runs in an isolated NixOS microvm via MCP `run_task`
4. **Monitor progress**: Use `get_task_logs` to get log file path, then `tail -f` to stream
5. **Wait for completion**: `wait_next_event` blocks until tasks finish
6. **Auto-merge**: Commits from isolated repo are rebased onto your branch
7. **Resolve conflicts**: If merge fails, commits are preserved at `refs/tasks/<id>`

## File Locations

| Path | Description |
|------|-------------|
| `.microvm/tasks/<id>/` | Task working directory |
| `.microvm/tasks/<id>/task.json` | Task metadata |
| `.microvm/tasks/<id>/task.md` | Original task description |
| `.microvm/tasks/<id>/repo/` | Isolated git repository |
| `.microvm/tasks/<id>/serial.log` | VM console output |
| `.microvm/tasks/<id>/result.json` | Task result |
| `.microvm/tasks/<id>/merge-result.json` | Merge outcome |
| `~/.microvm-orchestrator/slots/<N>/` | Persistent slot storage |

## Result Format

```json
{
  "success": true,
  "summary": "Claude's full explanation of what was done",
  "files_changed": ["src/auth.ts", "tests/auth.test.ts"],
  "error": null
}
```

## Merge Result Format

```json
{
  "merged": true,
  "method": "fast-forward",
  "commits": 2,
  "conflicts": []
}
```

Or if conflicts occurred:

```json
{
  "merged": false,
  "reason": "conflicts",
  "conflicts": ["src/shared.ts"],
  "task_ref": "refs/tasks/<id>"
}
```

## Parallel Execution with Slots

Use different slots for parallel tasks to avoid storage conflicts:

```python
# Parallel tasks use different slots
run_task(description="Task A", slot=1)
run_task(description="Task B", slot=2)
run_task(description="Task C", slot=3)
```

Slot storage persists across tasks, so container images are cached.

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

## License

MIT
