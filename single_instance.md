# Plan: Single-Instance MCP Server with Automatic Slot Assignment

## Goal
Convert microvm-orchestrator-mcp from per-project model to single global server serving all projects with automatic slot assignment.

## Current State
- Server runs per-project, cwd determines project root
- `run_task(description, slot=1)` - user specifies slot manually
- Tasks stored in `<project>/.microvm/tasks/<task-id>/`
- Slots at `~/.microvm-orchestrator/slots/<N>/` (already centralized)

---

## Recommended Design

### 1. Working Directory: Pre-Registered Allowlist

**Security Model:** LLM can only reference pre-approved repos by alias.

#### User Workflow
```bash
# One-time setup per repo (run from project directory)
cd /path/to/myproject
microvm-orchestrator allow

# Or with explicit path and custom alias
microvm-orchestrator allow /path/to/myproject --alias myproj

# List registered repos
microvm-orchestrator list

# Remove a repo
microvm-orchestrator remove myproject
```

#### Alias Generation
- **Default:** Directory name (e.g., `/path/to/myproject` → `myproject`)
- **Collision handling:** Append number (`myproject-2`) or require explicit alias
- **Custom:** User can specify with `--alias`

#### Storage
```json
// ~/.microvm-orchestrator/allowed-repos.json
{
  "myproject": {
    "path": "/Users/ture/projects/myproject",
    "added": "2025-01-31T10:00:00Z"
  },
  "other-repo": {
    "path": "/Users/ture/work/other-repo",
    "added": "2025-01-30T15:00:00Z"
  }
}
```

#### API Change
```python
# Before
run_task(description: str, slot: int = 1)

# After
run_task(description: str, repo: str)  # repo is alias, NOT path
```

#### Security Properties
- LLM cannot invent paths (only known aliases work)
- User explicitly approves each repo
- Server validates alias exists before any operation
- Path never exposed to or controlled by LLM

### 2. Slot Assignment: Repo-Affinity with Dynamic Fallback

**Strategy:**
1. Hash repo path to get "preferred" slot (deterministic)
2. If preferred slot is free → use it (cache reuse benefit)
3. If preferred slot is busy → use any free slot
4. If all slots busy → return error with status

**Why repo-affinity?**
- Nix store overlay (30GB) and Podman cache are expensive to rebuild
- Same repo benefits from cached dependencies
- Bounded slots prevent resource exhaustion (default: 10)

### 3. Task Storage: Per-Project (unchanged)

**Structure:**
```
<project>/.microvm/tasks/<task-id>/
├── task.json
├── repo/              # Isolated git clone
├── serial.log
└── result.json

~/.microvm-orchestrator/
├── slots/
│   └── 1/, 2/, ... 10/        # Slot persistent storage (nix, containers)
└── slot-assignments.json      # Repo→slot affinity mapping
```

**Rationale:**
- Task data stays visible in project tree
- Easy to inspect/debug from project directory
- Only slot metadata is centralized

---

## Implementation

### Files to Modify

| File | Changes |
|------|---------|
| `src/microvm_orchestrator/core/slots.py` | **NEW** - SlotManager class |
| `src/microvm_orchestrator/core/registry.py` | **NEW** - RepoRegistry (allowlist management) |
| `src/microvm_orchestrator/cli.py` | **NEW** - CLI commands (allow, list, remove, serve) |
| `src/microvm_orchestrator/core/task.py` | Rename `project_root` → `repo_path` |
| `src/microvm_orchestrator/tools.py` | Add SlotManager + RepoRegistry; update `run_task(desc, repo)` |
| `src/microvm_orchestrator/server.py` | Update `run_task` signature to take `repo` alias |
| `src/microvm_orchestrator/core/git.py` | Use `task.repo_path` (rename only) |
| `src/microvm_orchestrator/__main__.py` | Route to CLI |

### New Component: SlotManager

```python
# src/microvm_orchestrator/core/slots.py
@dataclass
class SlotManager:
    max_slots: int = 10
    assignments_path: Path  # ~/.microvm-orchestrator/slot-assignments.json

    # repo_path_hash -> preferred slot (persisted to disk)
    _repo_to_slot: dict[str, int]

    # slot -> task_id (in-memory only, tracks active tasks)
    _active_tasks: dict[int, str]

    _lock: threading.Lock

    def acquire_slot(self, repo_path: Path, task_id: str) -> int:
        """Get slot with repo affinity, fallback to any free slot."""
        repo_hash = self._hash_path(repo_path)
        with self._lock:
            # Try preferred slot first
            if repo_hash in self._repo_to_slot:
                preferred = self._repo_to_slot[repo_hash]
                if preferred not in self._active_tasks:
                    self._active_tasks[preferred] = task_id
                    return preferred

            # Find any free slot
            for slot in range(1, self.max_slots + 1):
                if slot not in self._active_tasks:
                    self._active_tasks[slot] = task_id
                    self._repo_to_slot[repo_hash] = slot
                    self._persist()
                    return slot

            raise AllSlotsBusyError()

    def release_slot(self, slot: int) -> None:
        """Called when task completes."""

    def _hash_path(self, repo_path: Path) -> str:
        """Stable hash of canonical repo path for affinity lookup."""
```

### New Component: RepoRegistry

```python
# src/microvm_orchestrator/core/registry.py
@dataclass
class RepoRegistry:
    registry_path: Path  # ~/.microvm-orchestrator/allowed-repos.json

    def allow(self, path: Path, alias: str | None = None) -> str:
        """Register a repo. Returns the alias used."""
        path = path.resolve()
        if not (path / ".git").exists():
            raise ValueError("Not a git repository")

        alias = alias or path.name
        # Handle collisions...
        self._repos[alias] = {"path": str(path), "added": datetime.now().isoformat()}
        self._persist()
        return alias

    def resolve(self, alias: str) -> Path:
        """Resolve alias to path. Raises if not found."""
        if alias not in self._repos:
            raise UnknownRepoError(f"Repo '{alias}' not registered. Run: microvm-orchestrator allow")
        return Path(self._repos[alias]["path"])

    def list(self) -> dict[str, dict]:
        """List all registered repos."""
        return self._repos

    def remove(self, alias: str) -> None:
        """Remove a repo from allowlist."""
```

### New Component: CLI

```python
# src/microvm_orchestrator/cli.py
import click

@click.group()
def cli():
    pass

@cli.command()
@click.argument('path', default='.', type=click.Path(exists=True))
@click.option('--alias', '-a', help='Custom alias for the repo')
def allow(path, alias):
    """Register a repository for use with microvm tasks."""
    registry = RepoRegistry()
    used_alias = registry.allow(Path(path), alias)
    click.echo(f"Registered: {used_alias}")

@cli.command()
def list():
    """List registered repositories."""
    registry = RepoRegistry()
    for alias, info in registry.list().items():
        click.echo(f"  {alias}: {info['path']}")

@cli.command()
@click.argument('alias')
def remove(alias):
    """Remove a repository from the allowlist."""

@cli.command()
def serve():
    """Start the MCP server."""
    from .server import run
    run()
```

### Updated Task Model

```python
@dataclass
class Task:
    id: str
    description: str
    status: TaskStatus
    slot: int
    repo_path: Path      # Original repo location (was project_root)

    @property
    def task_dir(self) -> Path:
        # Per-project storage (unchanged location)
        return self.repo_path / ".microvm" / "tasks" / self.id
```

### Updated Orchestrator

```python
class Orchestrator:
    def __init__(self):
        self.registry = RepoRegistry()      # NEW: allowlist
        self.slot_manager = SlotManager()
        self.event_queue = EventQueue()
        self._processes: dict[str, VMProcess] = {}
        self._tasks: dict[str, Task] = {}

    async def run_task(self, description: str, repo: str) -> dict:
        # Resolve alias to path (validates against allowlist)
        repo_path = self.registry.resolve(repo)  # Raises if not allowed

        task_id = str(uuid.uuid4())
        slot = self.slot_manager.acquire_slot(repo_path, task_id)

        task = Task.create(
            id=task_id,
            description=description,
            repo_path=repo_path,
            slot=slot,
        )
        # ... setup isolated repo, start VM
        # On task exit: self.slot_manager.release_slot(slot)
```

---

## Edge Cases

| Scenario | Handling |
|----------|----------|
| Unknown repo alias | Return `{"error": "Repo 'x' not registered. Run: microvm-orchestrator allow"}` |
| All slots busy | Return `{"error": "All 10 slots busy", "active": [...]}` |
| Task completes | `release_slot()` in `_on_task_exit()` callback |
| Server restart | Scan for orphaned tasks, rebuild `_active_tasks` |
| Repo moved | User re-runs `allow` with new path (or remove + allow) |
| Alias collision | Append `-2`, `-3`, etc. or require explicit `--alias` |

---

## New Tools

```python
@mcp.tool()
async def list_repos() -> dict:
    """List registered repositories that can be used with run_task."""
    return {
        "repos": [
            {"alias": "repo-a", "path": "/path/to/repo-a"},
            {"alias": "repo-b", "path": "/path/to/repo-b"},
        ]
    }

@mcp.tool()
async def list_slots() -> dict:
    """Show slot status for debugging."""
    return {
        "max_slots": 10,
        "active": [{"slot": 1, "repo": "repo-a", "task_id": "abc"}],
        "available": [2, 3, 4, ...]
    }
```

---

## Verification

1. **Unit tests**:
   - RepoRegistry: allow, resolve, remove, collision handling
   - SlotManager: affinity logic, concurrent access, persistence

2. **Integration test**: Run tasks on 2 registered repos simultaneously

3. **Manual test**:
   ```bash
   # Register repos
   cd /path/to/repo-a && microvm-orchestrator allow
   cd /path/to/repo-b && microvm-orchestrator allow

   # Verify registration
   microvm-orchestrator list
   # Output: repo-a: /path/to/repo-a
   #         repo-b: /path/to/repo-b

   # Start server
   microvm-orchestrator serve

   # In Claude Code, run tasks
   run_task("task 1", repo="repo-a")
   run_task("task 2", repo="repo-b")

   # Verify: different slots assigned, tasks run in correct repos
   # Verify: unknown alias returns clear error
   run_task("task 3", repo="unknown")  # → error
   ```
