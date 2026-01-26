{
  configFile ? "",
  taskDir ? "/tmp/claude-task",
  varDir ? "",
  containerDir ? "",
  socketPath ? "control.socket",
  slot ? "1",
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

  # Read config from file path passed via argument
  defaultConfig = {
    packages = [ ];
  };
  config =
    if configFile == "" then
      defaultConfig
    else
      defaultConfig // builtins.fromJSON (builtins.readFile configFile);

  # Convert empty strings to null for optional paths
  effectiveVarDir = if varDir == "" then null else varDir;
  effectiveContainerDir = if containerDir == "" then null else containerDir;

  # Build the NixOS configuration using the proper evalModules approach
  nixosConfiguration = import "${toString <nixpkgs>}/nixos/lib/eval-config.nix" {
    system = guestSystem;
    modules = [
      microvmModules
      (import ./nix/vm-config.nix {
        projectConfig = config;
        taskDir = taskDir;
        varDir = effectiveVarDir;
        containerDir = effectiveContainerDir;
        socketPath = socketPath;
        inherit hostPkgs;
      })
    ];
  };
in
{
  claude-microvm = nixosConfiguration.config.microvm.declaredRunner;
}
