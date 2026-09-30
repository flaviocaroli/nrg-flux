#!/usr/bin/env bash
# Install the NRG-Flux timers on a systemd host (VPS or systemd-enabled WSL).
# Assumes the repo lives at /opt/nrg-flux — edit REPO below if not.
set -euo pipefail
REPO="${REPO:-/opt/nrg-flux}"
UNIT_DIR=/etc/systemd/system

if ! id -u nrgflux >/dev/null 2>&1; then
  sudo useradd --system --home "$REPO" --shell /usr/sbin/nologin nrgflux
fi
sudo chown -R nrgflux: "$REPO/backend"

for f in nrgflux-ingest.service nrgflux-ingest.timer \
         nrgflux-retrain.service nrgflux-retrain.timer; do
  sudo sed "s#/opt/nrg-flux#$REPO#g" "$(dirname "$0")/$f" | sudo tee "$UNIT_DIR/$f" >/dev/null
done

sudo systemctl daemon-reload
sudo systemctl enable --now nrgflux-ingest.timer nrgflux-retrain.timer
echo
systemctl list-timers 'nrgflux-*' --no-pager
echo
echo "Logs:  journalctl -u nrgflux-ingest.service -f"
echo "       journalctl -u nrgflux-retrain.service -f"
