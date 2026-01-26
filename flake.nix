{
  description = "MCP server for orchestrating parallel tasks in isolated microVMs";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        python = pkgs.python313;
      in
      {
        devShells.default = pkgs.mkShell {
          packages = [
            python
            pkgs.uv
          ];

          shellHook = ''
            export VIRTUAL_ENV="$PWD/.venv"
            export PATH="$VIRTUAL_ENV/bin:$PATH"

            if [ ! -d "$VIRTUAL_ENV" ]; then
              echo "Creating virtual environment with Python 3.13..."
              uv venv --python ${python}/bin/python
            fi

            echo "microvm-orchestrator-mcp dev environment"
            echo "Python: $(python --version)"
          '';
        };
      }
    );
}
