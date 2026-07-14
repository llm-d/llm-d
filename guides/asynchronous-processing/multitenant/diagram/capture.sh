#!/usr/bin/env bash
# Regenerate architecture.gif / architecture.mp4 from architecture.html.
# Requires: google-chrome (headless) and ffmpeg.
#
# Captures one full loop by advancing Chrome's virtual clock per frame.
# --run-all-compositor-stages-before-draw makes each screenshot land exactly on
# its virtual time (deterministic, no jitter); --virtual-time-budget advances
# CSS *and* SMIL animations before the shot.
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="file://$DIR/architecture.html"
FRAMES="$(mktemp -d)"

LOOP_MS=18000      # must match the --loop CSS var
FPS=20             # capture frame rate (raise for smoother motion)
GIF_FPS=16         # GIF playback rate (subsampled from FPS to bound size)
GIF_W=980          # GIF width in px
STEP=$((1000/FPS)); i=0

for ((t=0; t<LOOP_MS; t+=STEP)); do
  b=$t; [ "$b" -lt 40 ] && b=40
  google-chrome --headless=new --disable-gpu --no-sandbox --hide-scrollbars \
    --run-all-compositor-stages-before-draw --window-size=1300,900 --force-device-scale-factor=1 \
    --virtual-time-budget="$b" --screenshot="$FRAMES/$(printf '%04d' "$i").png" "$SRC" >/dev/null 2>&1 || true
  i=$((i+1))
done

ffmpeg -y -framerate "$FPS" -i "$FRAMES/%04d.png" \
  -vf "fps=$GIF_FPS,scale=$GIF_W:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=256:stats_mode=full[p];[b][p]paletteuse=dither=sierra2_4a" \
  -loop 0 "$DIR/architecture.gif"

ffmpeg -y -framerate "$FPS" -i "$FRAMES/%04d.png" -c:v libx264 -pix_fmt yuv420p \
  -vf "scale=1280:-2" -movflags +faststart "$DIR/architecture.mp4"

rm -rf "$FRAMES"
echo "wrote $DIR/architecture.{gif,mp4} ($i frames @ ${FPS}fps; gif ${GIF_FPS}fps/${GIF_W}px)"
