{
  # Required arguments - no defaults, fail early if missing
  taskDir,
  nixStoreImage,
  socketPath,
  slot,
  # Optional arguments
  varDir ? "",
  containerDir ? "",
}:

let
  hostSystem = "aarch64-darwin";
  guestSystem = "aarch64-linux";

  # Import nixpkgs with the NixOS library
  nixpkgs = import <nixpkgs> { };
  pkgs = import <nixpkgs> { system = guestSystem; };
  hostPkgs = import <nixpkgs> { system = hostSystem; };

  # Fetch microvm.nix
  microvm = builtins.fetchTarball {
    url = "https://github.com/astro/microvm.nix/archive/refs/heads/main.tar.gz";
  };

  # Import microvm modules
  microvmModules = import "${microvm}/nixos-modules/microvm";

  # Convert empty strings to null for optional paths
  effectiveVarDir = if varDir == "" then null else varDir;
  effectiveContainerDir = if containerDir == "" then null else containerDir;

  # Build the NixOS configuration using the proper evalModules approach
  nixosConfiguration = import "${toString <nixpkgs>}/nixos/lib/eval-config.nix" {
    system = guestSystem;
    modules = [
      microvmModules
      (import ./nix/vm-config.nix {
        inherit taskDir nixStoreImage socketPath hostPkgs;
        varDir = effectiveVarDir;
        containerDir = effectiveContainerDir;
      })
    ];
  };
in
{
  claude-microvm = nixosConfiguration.config.microvm.declaredRunner;
}
