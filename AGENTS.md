# Agent Instructions

## Role: Principal Engineer

When working on this codebase, adopt the mindset of a **principal engineer**. This means:

- **Think before acting**: Understand the full context before making changes. Read related code, understand dependencies, and consider ripple effects.
- **Preserve architectural integrity**: This is a carefully designed system. Changes should fit naturally into existing patterns, not fight against them.
- **Favor correctness over speed**: A working, well-tested change is better than a quick hack. Take time to get it right.
- **Consider failure modes**: microVMs, git operations, and async events can fail in subtle ways. Handle errors gracefully.
- **Document your reasoning**: Future agents (and humans) need to understand why decisions were made.

---

## Project Overview

**microvm-orchestrator-mcp** is an MCP (Model Context Protocol) server that enables Claude Code to delegate tasks to isolated microVMs running on macOS via vfkit.

### What It Does

1. **Task Isolation**: Spawns lightweight Linux VMs to run Claude Code tasks in sandboxed environments
2. **Parallel Execution**: Supports multiple concurrent tasks using slot-based persistent storage
3. **Git Integration**: Automatically creates isolated repositories, executes work, and merges results back
4. **Event-Driven**: Emits async events for task completion/failure monitoring
5. **Secure Credentials**: Manages API keys securely within VM lifecycle

### Why It Exists

Running untrusted or experimental code in the main environment is risky. This orchestrator provides:
- Complete isolation via hardware virtualization
- Reproducible environments via NixOS
- Safe experimentation with automatic rollback on failure
- Parallel workload distribution

---

## Development Guidelines

### Before Making Changes

1. **Understand the state machine**: Tasks follow PENDING → RUNNING → COMPLETED/FAILED. State transitions are thread-safe and validated.
2. **Respect the singleton**: `Orchestrator` is a global singleton accessed via `get_orchestrator()`. Don't create additional instances.
3. **Consider concurrency**: Multiple VMs can run simultaneously. The event queue handles cross-thread signaling.
4. **Test git operations carefully**: The merge logic is delicate. Changes can cause data loss.

### Code Style

- **Python 3.13+**: Use modern Python features (type hints, match statements)
- **Docstrings**: All public functions should have clear docstrings
- **Error handling**: Catch specific exceptions, provide meaningful error messages
- **Logging**: Use appropriate log levels (debug for verbose, info for milestones, error for failures)

### Making Changes Safely

1. **Read the related code first**: Before modifying a function, understand its callers and callees
2. **Preserve error handling**: Don't remove try/except blocks without understanding why they exist
3. **Test with real VMs**: The nix-build and VM execution paths are complex - test end-to-end
4. **Check merge implications**: Changes to git.py can affect how work gets integrated back

### Red Flags (Stop and Think)

- Changing state transition logic in `task.py`
- Modifying the event queue signaling in `events.py`
- Altering git merge strategies in `git.py`
- Changing the slot/persistent storage layout
- Modifying the VM environment variables or mounts

These are critical paths where bugs cause data loss or deadlocks.

---

## Issue Tracking with Beads

This project uses **bd** (beads) for issue tracking. Run `bd onboard` to get started.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --status in_progress  # Claim work
bd close <id>         # Complete work
bd sync               # Sync with git
```

### Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd sync
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
