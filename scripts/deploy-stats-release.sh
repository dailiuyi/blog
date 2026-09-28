#!/usr/bin/env bash

set -Eeuo pipefail
umask 022

amd64_binary=${1:?Usage: deploy-stats-release.sh AMD64_BINARY ARM64_BINARY}
arm64_binary=${2:?Usage: deploy-stats-release.sh AMD64_BINARY ARM64_BINARY}

if [[ $EUID -ne 0 ]]; then
  echo "This deployment helper must run through sudo." >&2
  exit 1
fi
for candidate in "$amd64_binary" "$arm64_binary"; do
  if [[ ! $candidate =~ ^/tmp/blog-deploy-[0-9]+-[0-9]+/nabunana-stats-linux-(amd64|arm64)$ || ! -f $candidate || -L $candidate ]]; then
    echo "Refusing unexpected statistics artifact: $candidate" >&2
    exit 2
  fi
done

case "$(uname -m)" in
  x86_64|amd64) binary=$amd64_binary ;;
  aarch64|arm64) binary=$arm64_binary ;;
  *) echo "Unsupported server architecture: $(uname -m)" >&2; exit 2 ;;
esac

target=/opt/nabunana-stats/nabunana-stats
previous=/opt/nabunana-stats/nabunana-stats.previous
next=/opt/nabunana-stats/nabunana-stats.next
install -o root -g root -m 755 "$binary" "$next"
if [[ -f $target ]]; then cp -p -- "$target" "$previous"; fi
mv -f -- "$next" "$target"
systemctl restart nabunana-stats.service

if ! curl --fail --silent --show-error --retry 15 --retry-delay 1 --retry-connrefused \
  --connect-timeout 2 --max-time 3 --retry-max-time 30 http://127.0.0.1:8787/api/stats/health >/dev/null; then
  echo "Statistics health check failed; restoring the previous binary." >&2
  if [[ -f $previous ]]; then
    mv -f -- "$previous" "$target"
    systemctl restart nabunana-stats.service
  fi
  exit 1
fi
echo "Statistics service updated for $(uname -m)."
