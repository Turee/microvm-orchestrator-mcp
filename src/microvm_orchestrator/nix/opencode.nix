{ pkgs }:

# Nix derivation to fetch the opencode binary from GitHub releases.
# See https://github.com/opencode-ai/opencode for upstream releases.
let
  version = "0.1.82";

  # Platform-specific release assets.
  sources = {
    "x86_64-linux" = {
      url = "https://github.com/opencode-ai/opencode/releases/download/v${version}/opencode_Linux_x86_64.tar.gz";
      sha256 = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";
    };
    "aarch64-linux" = {
      url = "https://github.com/opencode-ai/opencode/releases/download/v${version}/opencode_Linux_arm64.tar.gz";
      sha256 = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";
    };
  };

  src = sources.${pkgs.stdenv.hostPlatform.system} or (throw "opencode: unsupported system ${pkgs.stdenv.hostPlatform.system}");
in
pkgs.stdenv.mkDerivation {
  pname = "opencode";
  inherit version;

  src = pkgs.fetchurl {
    inherit (src) url sha256;
  };

  nativeBuildInputs = [ pkgs.installShellFiles ];

  sourceRoot = ".";

  installPhase = ''
    runHook preInstall
    install -D -m 755 opencode $out/bin/opencode
    runHook postInstall
  '';

  meta = with pkgs.lib; {
    description = "AI coding agent for the terminal";
    homepage = "https://github.com/opencode-ai/opencode";
    license = licenses.mit;
    platforms = [ "x86_64-linux" "aarch64-linux" ];
    mainProgram = "opencode";
  };
}
