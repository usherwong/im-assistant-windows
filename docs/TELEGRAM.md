# Telegram Desktop support

## Why Telegram is not just another row

WeChat and KakaoTalk both draw their conversation on a **flat** panel colour, and the reader leans on
that: `capture.chat_area` finds the pane by "the most common colour of the right half", and
`ocr.who_said` decides "is this text on the panel or inside a bubble" by comparing the text's
background with that panel colour. Telegram Desktop breaks those assumptions:

1. **One window, list + conversation together.** KakaoTalk opens a window per chat; Telegram has a
   single top-level window per process and it is **titled with the open chat's name** (measured:
   `\u200ezhaomm`), plus the same process owns a tray-icon message window, a `_q_titlebar` helper and
   theme/screen observer windows. Window picking must not depend on a fixed title.
2. **The conversation sits on a wallpaper.** The default chat background is a patterned green/blue
   gradient (measured palette: own bubble `#DEFDEF`, other bubble `#FFFFFF`, wallpaper `#84B889`…
   `#8CD6D2`). There is no single panel colour to find, so the flat detector returns nothing or the
   wrong rectangle and the app reports "消息区认不出来".
3. **Own bubbles are right-aligned and theme-dependent.** Light themes paint them `#EEFFDE`/`#DEFDEF`
   (green only slightly above blue: measured 222/253/239 — the WeChat rule `G > R+40 and G > B+40`
   misses it), the night theme `#2B5278`. Custom themes repaint both.
4. **Telegram prints the send time inside the bubble.** OCR reads it as body text: `好的` came out as
   `好的 23:40`, `…可以这样实现` as `…可以这样实现 23:40`, and a lone `23:40` box becomes its own line.

## What changed

- **`app/chatapps.py`** — a `TELEGRAM` row: `telegram.exe`; helper windows in `skip_titles`;
  `min_share=0.45` so a photo viewer or call window is not mistaken for the chat; `area="patterned"`
  to select the wallpaper-aware pane finder; `input_dx/input_dy=90/34` for the paste target (Telegram's
  input row starts with the attach button).
  - `is_me` now takes the OCR box and the pane width as optional arguments. WeChat and KakaoTalk still
    decide by colour only. Telegram accepts `#EEFFDE`-family green, the night-theme blue, and as a last
    resort "the box ends past 75% of the pane width" (own bubbles are right-aligned, the other side's
    are capped well short of that).
  - new `clean(text)`: strips the send time (and stray ✓/✔) that Telegram draws inside the bubble.
    `好的 23:40` → `好的`; a fragment that is nothing but a time returns `""` and the caller drops it.
    Spaceless trailing times are left alone (`我们23:40见` is untouched).
- **`app/capture.py`** —
  - `chat_area(full, app)` dispatches on `app.area`; the old code is `_chat_area_flat`, unchanged.
  - `_chat_area_patterned` finds the pane as **the largest rectangle that is not the window's chrome
    colour** (chrome = overall modal colour: chat list, header, input bar, title area are all that
    colour). Longest run of "not chrome" columns → left/right; longest run of such rows → top/bottom,
    which drops header, input bar and the pinned-message band for free. The panel colour it returns is
    sampled from the wallpaper's left inset, not the whole area — the whole area's mode is the other
    side's white bubble, and short messages would then be mistaken for sender names. Measured on a
    1626×1261 window: `x 574..1598`, `y 156..1157`, exactly the visible pane.
  - `dpi_aware()`: the old `SetProcessDPIAware()` (system-DPI-aware) on a 200% display reported
    Telegram's 1626×1262 window as 813×631 and captured **half resolution** — small text OCR suffered
    badly. The new helper asks for per-monitor v2 (then falls back), so frames and coordinates are
    real pixels for every app.
  - `Capture` falls back to **BitBlt polling** when Windows Graphics Capture cannot start
    (`Failed to convert item to GraphicsCaptureItem` — seen on VMs and some RDP sessions; it failed
    for *every* window here, not just Telegram). The BitBlt frame is taken from the DWM visible bounds
    so it shares the origin used by `fill()`.
- **`app/fill.py`, `main.py`** — the paste target offset comes from the app row instead of a
  hard-coded 60/40.
- **`probe/probe_telegram.py`** — read-only probe: chosen window, capture backend, both pane-finder
  results, message-area palette, header OCR, every classified line/box. No clicks, no typing, no
  sending.

## Verifying against a live window

Telegram must be **visible on the desktop** — a window hidden in the tray is DWM-cloaked
(`DWMWA_CLOAKED=1`) and nothing can capture it; `ShowWindow` / re-launching / `tg://` do not bring it
back, only the tray click does.

```powershell
python -m probe.probe_telegram                   # one frame, print everything
python -m probe.probe_telegram --save frame.png  # keep the frame for eyeballing (local only)
```

Expected on a light theme with the default wallpaper (measured):

```
frame 后端: wgc            # or bitblt on machines without WGC
patterned: x 574..1598 (1024px)  y 156..1157 (1001px)  面板色 #8CD6D2
title OCR : 'zhaomm'
her '支持真假身份设定吗'
me  '当前可以了理解成，他不管真假身份设定。可以这样实现'
her '好的'
me  '这个猎头，就是猎头，然后再加个设定：假猎头'
...
time (308, 47)-(384, 80) '23:38'      # dropped, shown as "time" in the debug view
```

If `dispatch` is wrong, the knobs are the chrome distance `> 26` and the run fractions
`col > 0.5` / `row > 0.3` in `_chat_area_patterned`. If lines are all `her`, add the theme's own
bubble colour to `_telegram_me`.

## Known limits

- **Solid or gradient backgrounds.** The pane finder needs a wallpaper that differs from the chrome
  colour and is not a single flat colour. A plain white background makes everything look like chrome
  and the finder declines, falling back to the flat detector (correct for a solid non-white colour,
  wrong for white-on-white). Default wallpapers are fine.
- **Quoted replies.** The quoted snippet inside a bubble has its own background shade; it is read as
  part of the message, so a reply comes out as `usher wong 这个功能真强大… 这个是候选回复里，我选的哈哈`.
  The model sees the quote as context — noisy but not wrong. A future rule could drop boxes whose
  background differs from the bubble the surrounding lines share.
- **Group chat sender names.** Telegram draws them on the wallpaper, where the "modal colour ≥ 45%"
  test drops them as image text, so group messages carry no speaker name. 1:1 chats are unaffected.
- **Tray.** Closed to tray → no visible window → the app reports "未找到聊天窗口".
- **Two chat apps at once.** `find_chat_hwnd` walks `chatapps.APPS` in order, so with WeChat and
  Telegram both open WeChat wins (same as before this change). Close one, or pin the order, if you
  switch between them.
- **Sandboxed sessions.** Windows Graphics Capture, clipboard and input injection are unavailable in
  some restricted sessions (VMs, some RDP setups, containerised shells). Capture degrades to BitBlt
  (works); "填入" needs clipboard + a click, so it only works in a normal interactive session.
- **Themes.** `_telegram_me` knows the light and night bubbles plus right alignment; an unusual theme
  that paints own bubbles dark *and* very wide can still fool the geometry fallback.
