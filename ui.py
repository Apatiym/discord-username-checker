# -*- coding: utf-8 -*-
"""Console look: croissant gradient, ASCII logo, flicker-free animations.

The gradient is real 24-bit ANSI, per character, with a 256-color fallback and
an ASCII logo when the console cannot print block characters.

Animation rules:
  * in a TTY a line is redrawn with \\r and erased with ESC[K - never by
    clearing the screen, which is what keeps it flicker-free;
  * redirected output (a file, a pipe) skips animations entirely;
  * fixed bar width, padded percentages, 8-15 fps, NO_ANIM=1 turns it all off.
"""

import os
import random
import shutil
import sys
import threading
import time

# ---------------- palette ----------------

# dark crust -> baked -> gold -> butter cream
STOPS = ((122, 66, 26), (198, 124, 38), (241, 178, 76), (252, 226, 172))
HIGHLIGHT = (255, 246, 214)
MUTED = (150, 116, 86)
RESET = "\033[0m"

RED = (224, 96, 78)
GREEN = (126, 202, 108)

LOGO = r"""
    ▄▄     ▄▄▄▄▄▄       ▄▄▄▄
   ████    ██▀▀▀▀██   ██▀▀▀▀█
   ████    ██    ██  ██▀
  ██  ██   ███████   ██
  ██████   ██  ▀██▄  ██▄
 ▄██  ██▄  ██    ██   ██▄▄▄▄█
 ▀▀    ▀▀  ▀▀    ▀▀▀    ▀▀▀▀
"""

LOGO_ASCII = r"""
 ####   ####    ####
 #   #  #      #
 #   #   ###   #
 #   #      #  #
 ####   ####    ####
"""


def _truecolor():
    if os.environ.get("NO_TRUECOLOR"):
        return False
    if os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit"):
        return True
    if os.environ.get("WT_SESSION"):
        return True
    return os.name == "nt"  # conhost on Windows 10+ understands 24-bit SGR


TRUECOLOR = _truecolor()


def unicode_ok():
    """Whether the current output can print the block characters of the logo."""
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        LOGO.strip().encode(enc)
        return True
    except Exception:
        return False


def _lerp(a, b, t):
    return (
        round(a[0] + (b[0] - a[0]) * t),
        round(a[1] + (b[1] - a[1]) * t),
        round(a[2] + (b[2] - a[2]) * t),
    )


def sample(t, stops=STOPS):
    """Color at position t (0..1) across the stop list."""
    if t <= 0:
        return stops[0]
    if t >= 1:
        return stops[-1]
    pos = t * (len(stops) - 1)
    i = int(pos)
    return _lerp(stops[i], stops[i + 1], pos - i)


def _to_256(rgb):
    """Nearest color from the xterm-256 palette (6x6x6 cube + grays)."""
    r, g, b = rgb
    best, best_d = 0, 1 << 30
    for ri in range(6):
        for gi in range(6):
            for bi in range(6):
                cr, cg, cb = ri * 51, gi * 51, bi * 51
                d = (r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2
                if d < best_d:
                    best_d, best = d, 16 + 36 * ri + 6 * gi + bi
    for i in range(24):
        v = 8 + i * 10
        d = (r - v) ** 2 + (g - v) ** 2 + (b - v) ** 2
        if d < best_d:
            best_d, best = d, 232 + i
    return best


def fg(rgb):
    if TRUECOLOR:
        return f"\033[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m"
    return f"\033[38;5;{_to_256(rgb)}m"


def paint(text, stops=STOPS, width=None, diagonal=False, span=(0.0, 1.0),
          glow=None, glow_width=0.16, glow_strength=0.6):
    """Paints a string or a block with a left-to-right gradient.

    glow is a cursor position (0..1) for a bright sweep that follows it.
    """
    lines = text.split("\n")
    w = max((len(line) for line in lines), default=1) if width is None else width
    h = max(1, len(lines))
    w = max(1, w)
    t0, t1 = span

    out = []
    for y, line in enumerate(lines):
        buf = []
        for x, ch in enumerate(line):
            if ch == " ":
                buf.append(ch)
                continue
            tx = x / max(1, w - 1)
            if diagonal and h > 1:
                tx = tx * 0.72 + (y / (h - 1)) * 0.28
            rgb = sample(t0 + (t1 - t0) * tx, stops)
            if glow is not None:
                dist = abs(tx - glow)
                if dist < glow_width:
                    rgb = _lerp(rgb, HIGHLIGHT, (1 - dist / glow_width) * glow_strength)
            buf.append(fg(rgb) + ch)
        out.append("".join(buf) + RESET)
    return "\n".join(out)


def logo(glow=None):
    art = LOGO if unicode_ok() else LOGO_ASCII
    return paint(art.strip("\n"), diagonal=True, glow=glow)


def accent(text):
    """One saturated gradient tone for menu digits and accents."""
    return fg(sample(0.55)) + str(text) + RESET


def muted(text):
    """Quiet warm tone for captions."""
    return fg(MUTED) + str(text) + RESET


def rule(width, char="─"):
    """Thin line in the same gradient - instead of ==== and ----."""
    return paint(char * width)


# ---------------- animation core ----------------

SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SPIN_ASCII = "|/-\\"
FILL, FILL_ASCII = "█", "#"
EMPTY, EMPTY_ASCII = "░", "-"
FPS = 14
GLYPHS = "abcdefghijklmnopqrstuvwxyz0123456789"

_live = None
_lock = threading.Lock()


def enabled():
    if os.environ.get("NO_ANIM"):
        return False
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


def _erase():
    sys.stdout.write("\r\x1b[K")
    sys.stdout.flush()


def _chars():
    """Box/braille characters if the console can print them, else ASCII."""
    if unicode_ok():
        return SPIN, FILL, EMPTY
    return SPIN_ASCII, FILL_ASCII, EMPTY_ASCII


def _bar(frac, width, fill, empty):
    filled = max(0, min(width, int(round(frac * width))))
    out = "".join(fg(sample(x / max(1, width - 1))) + fill for x in range(filled))
    if filled < width:
        out += muted(empty * (width - filled))
    return out


def write_line(text):
    """Prints a permanent line above the live line, without interleaving."""
    with _lock:
        line = _live
        if line is not None and line.active:
            _erase()
        print(text)
        sys.stdout.flush()


class Loader:
    """One line: spinner, stage caption, gradient bar.

    The work runs on a worker thread, so the spinner keeps turning while
    DPAPI, leveldb or a network call is in flight.
    """

    def __init__(self, labels, width=22, min_time=0.7):
        self.labels = list(labels) or ["loading"]
        self.width = width
        self.min_time = min_time
        self.spin, self.fill, self.empty = _chars()
        self.label_w = max(16, max((len(x) for x in self.labels), default=0))
        self.stage = 0
        self.done = False
        self.shown = 0.0

    def mark(self, index):
        self.stage = max(0, min(index, len(self.labels) - 1))

    def run(self, work):
        """Turns the animation while work(mark) runs, then returns its result."""
        self.stage = 0
        self.done = False
        self.shown = 0.0

        if not enabled():
            return work(lambda i: None)

        result = {}

        def runner():
            try:
                result["value"] = work(self.mark)
            except BaseException as exc:  # re-raised after the animation stops
                result["error"] = exc
            finally:
                self.done = True

        worker = threading.Thread(target=runner, daemon=True)
        worker.start()
        self._animate()
        worker.join(0.2)
        if "error" in result:
            raise result["error"]
        return result.get("value")

    def _animate(self):
        start = time.time()
        last_stage = self.stage
        stage_at = start
        frame = 0
        try:
            while True:
                now = time.time()
                if self.stage != last_stage:
                    last_stage, stage_at = self.stage, now

                if self.done:
                    target = 1.0
                else:
                    creep = min(0.75, (now - stage_at) / 0.55)
                    target = (self.stage + creep) / len(self.labels)

                # chase the finish faster, otherwise the bar crawls asymptotically
                rate = 0.5 if self.done else 0.22
                self.shown += (target - self.shown) * rate
                if self.shown > 0.995:
                    self.shown = 1.0

                sys.stdout.write("\r" + self._line(frame) + "\x1b[K")
                sys.stdout.flush()
                frame += 1

                if self.done and self.shown >= 1.0 and now - start >= self.min_time:
                    break
                time.sleep(1.0 / FPS)
        finally:
            sys.stdout.write("\r\x1b[K")
            sys.stdout.flush()

    def _line(self, frame):
        label = self.labels[min(self.stage, len(self.labels) - 1)]
        pct = int(round(self.shown * 100))
        spin = accent(self.spin[frame % len(self.spin)])
        return (f"  {spin}  {muted(label.ljust(self.label_w))}  "
                f"{_bar(self.shown, self.width, self.fill, self.empty)}{RESET}"
                f"  {muted(f'{pct:3d}%')}")


class ScanLine:
    """Bottom line during a scan: spinner, progress, rate and counters."""

    def __init__(self, total, width=16, note=""):
        self.total = max(1, int(total))
        self.width = width
        self.spin, self.fill, self.empty = _chars()
        self.lock = threading.Lock()
        self.data = {
            "done": 0, "taken": 0, "free": 0, "invalid": 0,
            "errors": 0, "limits": 0, "rate": 0.0, "delay": 0.0, "note": note,
        }
        self.active = False
        self.frame = 0
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        global _live
        if not enabled():
            return
        self.active = True
        _live = self
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        global _live
        if not self.active:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(0.3)
        self.active = False
        if _live is self:
            _live = None
        self.clear()

    def update(self, **fields):
        with self.lock:
            self.data.update(fields)

    def clear(self):
        if not enabled():
            return
        with _lock:
            _erase()

    def _loop(self):
        while not self._stop.is_set():
            self.draw()
            self.frame += 1
            time.sleep(1.0 / 8)

    def draw(self):
        if not self.active:
            return
        with self.lock:
            data = dict(self.data)
        with _lock:
            sys.stdout.write("\r" + self._render(data) + "\x1b[K")
            sys.stdout.flush()

    def _render(self, data):
        frac = data["done"] / self.total
        spin = accent(self.spin[self.frame % len(self.spin)])
        parts = [
            f"  {spin}  {_bar(frac, self.width, self.fill, self.empty)}{RESET}",
            muted(f"{data['done']:>4}/{self.total}"),
            muted(f"{data['rate']:4.1f}/s"),
            muted("taken") + " " + fg(RED) + str(data["taken"]),
            muted("free") + " " + fg(GREEN) + str(data["free"]),
            muted(f"{data['delay']:.2f}s"),
        ]
        if data["note"]:
            parts.append(accent(data["note"]))
        return "  ".join(parts)


# ---------------- one-shot effects ----------------

def reveal(text, suffix="", frames=9, delay=0.045, glow_passes=1):
    """Reveals a line: junk characters settle into the real ones left to right."""
    if not enabled() or not text:
        print("  " + paint(text, width=max(1, len(text))) + suffix)
        return

    total = len(text)
    for frame in range(frames + 1):
        locked = round(total * frame / frames)
        cells = []
        for i, ch in enumerate(text):
            if i < locked:
                cells.append(fg(sample(i / max(1, total - 1))) + ch)
            else:
                cells.append(muted(random.choice(GLYPHS)))
        sys.stdout.write("\r  " + "".join(cells) + RESET + suffix + "\x1b[K")
        sys.stdout.flush()
        time.sleep(delay)

    for _ in range(glow_passes):
        for i in range(13):
            line = paint(text, width=max(1, total), glow=i / 12)
            sys.stdout.write("\r  " + line + RESET + suffix + "\x1b[K")
            sys.stdout.flush()
            time.sleep(0.028)

    sys.stdout.write("\r  " + paint(text, width=max(1, total))
                     + RESET + suffix + "\x1b[K\n")
    sys.stdout.flush()


def sweep(render, height, seconds=0.85, fps=15):
    """Redraws a fixed block in place while a highlight moves across it."""
    if not enabled() or height <= 0:
        return
    # cursor-up cannot leave the visible window: on a short console every frame
    # would smear further down instead of redrawing in place
    try:
        lines = shutil.get_terminal_size().lines
    except Exception:
        lines = 24
    if lines and height + 2 > lines:
        return
    frames = max(2, int(seconds * fps))
    for i in range(frames + 1):
        glow = None if i >= frames else i / frames
        block = render(glow).split("\n")
        # back to the top of the block on EVERY frame, not just the first
        sys.stdout.write(f"\x1b[{max(1, len(block))}A")
        sys.stdout.write("".join("\x1b[K" + line + "\n" for line in block))
        sys.stdout.flush()
        if i < frames:
            time.sleep(1.0 / fps)


def typed(text, delay=0.014, span=(0.3, 1.0)):
    """Prints a gradient title character by character."""
    if not text:
        return
    if not enabled():
        print("  " + paint(text, width=max(1, len(text)), span=span))
        return
    total = max(1, len(text) - 1)
    t0, t1 = span
    sys.stdout.write("  ")
    for i, ch in enumerate(text):
        sys.stdout.write(fg(sample(t0 + (t1 - t0) * (i / total))) + ch)
        sys.stdout.flush()
        time.sleep(delay)
    sys.stdout.write(RESET + "\n")
    sys.stdout.flush()
