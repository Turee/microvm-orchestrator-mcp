{ pkgs }:

# Nix derivation to fetch the opencode binary from GitHub releases.
# See https://github.com/opencode-ai/opencode for upstream releases.
#
# To update: find the new version tag, download the checksums.txt from the
# GitHub release page, and run:
#   nix hash convert --hash-algo sha256 --to sri <hex-hash>
# for each platform archive.
let
  version = "0.0.55";

  # Platform-specific release assets.
  # Hashes sourced from the checksums.txt for release v0.0.55.
  sources = {
    "x86_64-linux" = {
      url = "https://github.com/opencode-ai/opencode/releases/download/v${version}/opencode-linux-x86_64.tar.gz";
      hash = "sha256-fx9BID55IK7Ejz4iFtM06c6MbQp7EHzrdY/vpNTJgCU=";
    };
    "aarch64-linux" = {
      url = "https://github.com/opencode-ai/opencode/releases/download/v${version}/opencode-linux-arm64.tar.gz";
      hash = "sha256-Uw6xNv38nq3vlqoiULvbkhDp98wb1Pcz0zLKndYdIuA=";
    };
  };

  src = sources.${pkgs.stdenv.hostPlatform.system} or (throw "opencode: unsupported system ${pkgs.stdenv.hostPlatform.system}");
in
pkgs.stdenv.mkDerivation {
  pname = "opencode";
  inherit version;

  src = pkgs.fetchurl {
    inherit (src) url hash;
  };

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
