# Nix Flake-Based Development Environments

## Overview

The microvm-orchestrator uses Nix flakes to provide reproducible, project-specific
development environments. Users define their tooling in a standard `flake.nix`, and
Claude Code runs inside this environment via `nix develop`.

## Why Flakes?

- **Reproducible**: `flake.lock` pins exact versions
- **Declarative**: Single source of truth for project dependencies
- **Standard**: Uses the Nix ecosystem's recommended approach
- **Flexible**: Any Nix package available, custom shell hooks supported

## User Experience

1. Add `flake.nix` to your repository root
2. Define `devShells.default` with your required tools
3. Claude Code automatically runs inside this environment

## Example flake.nix

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

## How It Works

1. MicroVM boots with writable Nix store (persistent disk image)
2. MicroVM has Nix flakes experimental features enabled
3. Task runner runs: `nix develop /workspace/repo --command <claude-code>`
4. Nix fetches flake inputs and builds devShell (cached in persistent store)
5. Claude Code executes with all flake-defined tools available
6. **Claude can modify flake.nix and run `nix develop` again to get new tools**

## Architecture

### Writable Nix Store

The VM uses a persistent disk image for `/nix/store` (per slot). This enables:
- Building packages inside the VM
- Claude modifying `flake.nix` and adding dependencies
- Caching builds across tasks (avoiding re-downloads)

### Key Files

| File | Purpose |
|------|---------|
| `nix/vm-config.nix` | NixOS VM configuration with flakes enabled |
| `nix/scripts/run-claude-task.sh` | Task runner that wraps Claude in `nix develop` |
| `default.nix` | VM build entry point |
| `src/microvm_orchestrator/core/vm.py` | Manages Nix store disk images |

## Requirements

- Repository MUST contain `flake.nix` at root
- Flake MUST define `devShells.default`
- Network access required to fetch flake inputs and Nix binary cache
- Persistent Nix store disk image (per slot) for caching builds
