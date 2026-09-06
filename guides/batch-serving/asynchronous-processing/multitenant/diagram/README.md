# Animated architecture diagram

A looping diagram of the **team × tier × model** scenario: **two models**, each
with its own worker pool and vLLM; within each model, three team/tier lanes flow
through a per-team **reserved/overflow** quota gate and the **tier-priority** merge
into that model's pool. The loop tells the **model-isolation + priority** story —
**model A** saturates, so its pool gate goes to **WAIT** (parks in-memory) while
**model B keeps flowing**.

> The queue column is drawn generically (Redis SortedSet queue names shown); the
> **GCP Pub/Sub** backend is equivalent (topics/subscriptions in place of sorted
> sets). Canvas is 1300×900; `capture.sh` matches that window size and the 18s
> `--loop`.

## Files

| File | Use |
| :-- | :-- |
| `architecture.html` | The source — self-contained animated SVG (CSS keyframes + SMIL). Open in a browser; animates live. Edit this. |
| `architecture.gif` | Looping GIF for embedding in a README (GitHub strips inline-SVG animation, so use the GIF). |
| `capture.sh` | Regenerates the GIF from the HTML. |

## View it live

```bash
open diagram/architecture.html      # or: xdg-open / just open the file in a browser
```

## Embed in a README

```markdown
![Async Processor multi-tenant demo](diagram/architecture.gif)
```

## Regenerate the GIF/MP4

Requires `google-chrome` (headless) and `ffmpeg`:

```bash
./diagram/capture.sh
```

It scrubs Chrome's virtual clock one frame at a time (`--virtual-time-budget`, which
advances both CSS and SMIL animations), screenshots each frame, and assembles a
palette-optimized GIF + an H.264 MP4 of one 12s loop.

## Tweaking

Everything is driven by one shared loop period (`--loop` in the `<style>` block); all
timed effects (gate open/close, saturation meter, backlog bar, dot flow) use keyframe
percentages of that period, so they stay in sync. Colors are CSS variables at the top.
