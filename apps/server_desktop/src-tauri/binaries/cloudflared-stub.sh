#!/usr/bin/env bash
# Fake cloudflared for P4.1a tests: emit a trycloudflare.com URL line on the same
# stream/shape real cloudflared uses (so the URL-capture regex is exercised),
# then sleep so the spawn/process-group teardown path can be tested WITHOUT a
# real binary or network. Point YESON_CLOUDFLARED_BIN at this script.
#
# Real cloudflared prints the URL to stderr inside a banner; we mirror that.
echo "+--------------------------------------------------------------------+" >&2
echo "|  Your quick Tunnel has been created! Visit it at:                  |" >&2
echo "|  https://fake-xyz.trycloudflare.com                                |" >&2
echo "+--------------------------------------------------------------------+" >&2
# Stay alive until signalled (SIGTERM/SIGKILL) so teardown has something to reap.
while true; do
  sleep 3600
done
