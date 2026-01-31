{
  taskDir,
  varDir,
  containerDir,
  nixStoreImage,
  socketPath,
  hostPkgs,
}:
{ pkgs, lib, ... }:
let
  # Script to run Claude Code task
  runClaudeTask = pkgs.runCommand "run-claude-task" { } ''
    cp ${
      pkgs.replaceVars ./scripts/run-claude-task.sh {
        bash = pkgs.bash;
        git = pkgs.git;
        jq = pkgs.jq;
        gawk = pkgs.gawk;
        nodejs = pkgs.nodejs_22;
        gnugrep = pkgs.gnugrep;
        nix = pkgs.nix;
      }
    } $out
    chmod +x $out
  '';
in
{
  networking.hostName = "claude-microvm";
  networking.useDHCP = true;
  # Use Cloudflare DNS as fallback since vfkit NAT may not provide DNS
  networking.nameservers = [
    "1.1.1.1"
    "8.8.8.8"
  ];
  users.users.root.password = "";
  users.users.claude = {
    isNormalUser = true;
    uid = 1000;
    # Rootless Podman requires subuid/subgid ranges
    subUidRanges = [
      {
        startUid = 100000;
        count = 65536;
      }
    ];
    subGidRanges = [
      {
        startGid = 100000;
        count = 65536;
      }
    ];
  };

  # Podman container runtime with Docker CLI compatibility
  virtualisation.podman = {
    enable = true;
    dockerCompat = true; # Provides 'docker' command
    defaultNetwork.settings.dns_enabled = true;
  };

  # Configure container registries for non-interactive operation
  # Use single registry to avoid interactive prompts (no ambiguity = no prompt)
  # Use mkForce to override the default registries.conf from containers.nix
  environment.etc."containers/registries.conf".text = lib.mkForce ''
    unqualified-search-registries = ["docker.io"]
    short-name-mode = "disabled"
  '';

  # System packages for Claude Code
  environment.systemPackages =
    with pkgs;
    [
      git
      jq
      curl
      coreutils
      gnugrep
      nodejs_22
      podman-compose # For docker-compose.yml support
    ];

  # Enable networking for npm/API calls
  networking.firewall.enable = false;

  # Enable Nix flakes and modern CLI for in-VM builds
  nix.settings = {
    experimental-features = [ "nix-command" "flakes" ];
    trusted-users = [ "root" "claude" ];
  };

  # Autologin and run task script
  services.getty.autologinUser = "root";
  programs.bash.loginShellInit = ''
    # Only run the task from the serial console getty (hvc0).
    # Otherwise tty1 autologin can run first, skip the task, and still poweroff.
    TTY="$(tty 2>/dev/null || true)"
    if [ "$TTY" != "/dev/hvc0" ]; then
      echo "Not on /dev/hvc0 ($TTY); skipping task runner in this login shell."
    else
      # Wait for network to be ready (up to 30 seconds)
      echo "Waiting for network..."
      for i in $(seq 1 30); do
        if ${pkgs.curl}/bin/curl -s --max-time 2 https://registry.npmjs.org/ > /dev/null 2>&1; then
          echo "Network is ready!"
          break
        fi
        echo "Waiting for network... ($i/30)"
        sleep 1
      done

      # Run the Claude task
      ${runClaudeTask}

      # Power off when done
      poweroff
    fi
  '';

  microvm = {
    # Use host's pkgs for the hypervisor runner (Darwin)
    vmHostPackages = hostPkgs;

    graphics.enable = false;

    # User networking for internet access (NAT)
    interfaces = [
      {
        type = "user";
        id = "usernet";
        mac = "02:00:00:00:00:01";
      }
    ];

    # Writable Nix store overlay for building packages inside VM
    writableStoreOverlay = "/nix/.rw-store";

    # Ext4 volume for writable Nix store (30GB - Nix store can grow large)
    volumes = [
      {
        image = nixStoreImage;
        mountPoint = "/nix/.rw-store";
        size = 30000;
      }
    ];

    shares = [
      # Host nix store (read-only)
      {
        proto = "virtiofs";
        tag = "ro-store";
        source = "/nix/store";
        mountPoint = "/nix/.ro-store";
      }
      # Task directory contains: repo/, task.md, start-ref, result.json
      # This is the isolated workspace for the task
      {
        proto = "virtiofs";
        tag = "taskdir";
        source = taskDir;
        mountPoint = "/workspace";
      }
    ]
    ++ (
      if varDir != null then
        [
          # Persistent /var storage
          {
            proto = "virtiofs";
            tag = "var-storage";
            source = varDir;
            mountPoint = "/var";
          }
        ]
      else
        [ ]
    )
    ++ (
      if containerDir != null then
        [
          # Persistent container storage
          {
            proto = "virtiofs";
            tag = "container-storage";
            source = containerDir;
            mountPoint = "/var/lib/containers";
          }
        ]
      else
        [ ]
    );

    hypervisor = "vfkit";
    socket = socketPath;

    # Enable Rosetta for x86_64 binary translation (Apple Silicon)
    vfkit.rosetta = {
      enable = true;
      install = false; # Don't auto-install (avoids surprise dialogs)
    };

    # Allocate reasonable resources for Claude Code
    mem = 4096;
    vcpu = 4;
    balloon = false;
  };

  # Enable Rosetta 2 in the VM guest (mounts virtiofs, registers binfmt)
  virtualisation.rosetta.enable = true;
}
