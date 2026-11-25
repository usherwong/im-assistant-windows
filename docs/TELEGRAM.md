# Telegram Desktop support

## Why Telegram is not just another row

WeChat and KakaoTalk both draw their conversation on a **flat** panel colour, and the reader leans on
that: `capture.chat_area` finds the pane by "the most common colour of the right half", and
`ocr.who_said` decides "is this text on the panel or inside a bubble" by comparing the text's
background with that panel colour. Telegram Desktop breaks three of those assumptions:

1. **One window, list + conversation together.** KakaoTalk opens a window per chat; Telegram has a
   single top-level window titled `Telegram`, and the same process also owns a tray-icon message
   window, a `_q_titlebar` helper and theme/screen observer windows. Window picking has to know that.
2. **The conversation sits on a wallpaper.** Since v4 the default chat background is a textured
   gradient. There is no single panel colour to find, so the flat detector returns nothing (or the
   wrong rectangle) and the app reports "消息区认不出来".
3. **Own bubbles are right-aligned, and their colour is theme-dependent.** Light themes paint own
   bubbles `#EEFFDE`, the night theme `#2B5278`; custom themes repaint both. Colour alone cannot
   decide who said what.

## What changed

- **`app/chatapps.py`** — a `TELEGRAM` row: `telegram.exe`, main window title `Telegram`, the
  tray/observer windows in `skip_titles`, `min_share=0.45` so a photo viewer or call window is not
  mistaken for the chat, `area="patterned"` to select the wallpaper-aware pane finder, and
  `input_dx/input_dy=90/34` for the paste target (Telegram's input row starts with the attach button).
  `is_me` now takes the OCR box and the pane width as optional arguments: WeChat and KakaoTalk still
  decide by colour only, Telegram also accepts "the bubble ends near the pane's right padding" when
  the colour says nothing.
- **`app/capture.py`** — `chat_area(full, app)` dispatches on `app.area`. The old code is
  `_chat_area_flat`, unchanged. New `_chat_area_patterned` finds the pane by texture instead of
  colour:
  - **left/right** — chat-list and chrome columns are a single flat colour for their whole height
    (even when the window is taller than one screen); wallpaper columns change colour as you go down
    them, gradient or pattern. First and last "changing" column = pane edges. Bubbles keep padding,
    so they never touch the edge.
  - **top/bottom** — the header and the input bar are flat rows. Measure the median colour of the
    top and bottom few rows, then the first row that is clearly not it: message-area top and input-bar
    top. A full-width flat card right under the header (the pinned-message bar) is skipped the same
    way.
  - the returned panel colour is sampled from the wallpaper's left inset, not from the whole area —
    sampling the whole area would return white (the other side's bubble) and short messages would be
    mistaken for sender names.
- **`app/ocr.py`** — passes the box and pane width to `is_me`. Nothing else changes: a wallpaper is
  never flat, so text printed straight on the wallpaper (timestamps, date pills) fails the existing
  "modal colour ≥ 45% of the box" test and is dropped as image text, which is what we want.
- **`app/fill.py`, `main.py`** — the paste target offset now comes from the app row instead of a
  hard-coded 60/40.
- **`probe/probe_telegram.py`** — read-only probe: prints the chosen window, both pane-finder
  results, the message-area palette (bubble colours at a glance), the header OCR result and every
  classified line/box. No clicks, no typing, no sending.

## Verifying against a live window

Telegram must be **visible on the desktop** — a window hidden in the tray is not rendered, so there
is nothing to capture.

```powershell
python -m probe.probe_telegram                 # one frame, print everything
python -m probe.probe_telegram --save frame.png  # keep the frame for eyeballing (local only)
```

What to look for:

- `dispatch` should land on the message area, not on the chat list and not on the header/input bar.
  If it does not, the numbers to adjust are the texture threshold `col_std > 3.0` and the
  colour-distance threshold `> 24` in `_chat_area_patterned`.
- `message area palette` should show two big flat colours (own bubble, other bubble) plus wallpaper
  shades. If own-bubble colour is not what `_telegram_me` accepts, add it there.
- Every line should be `me` or `her`; `gray`/`image` lines in the message list mean the theme has a
  very flat wallpaper (see limits).

## Known limits

- **Plain or gradient backgrounds.** A user-set solid chat background makes every column flat; the
  patterned finder then declines and the flat one takes over (which is correct for a solid colour).
  A pure vertical gradient is the awkward case: columns change, rows do not — detection may land on
  the wrong rectangle. Use one of Telegram's default wallpapers, or a solid colour.
- **Group chat sender names.** Telegram prints them on the wallpaper, where the flatness test drops
  them as image text, so group messages are attributed to "the other side" without a name.
  1:1 chats are unaffected.
- **Themes.** `_telegram_me` knows the light (#EEFFDE) and night (#2B5278) bubbles and falls back to
  right alignment. A theme that paints own bubbles dark *and* shows a very wide bubble can still fool
  the geometry fallback.
- **Tray.** Closes to tray → no window → the app reports "未找到聊天窗口".
