"""Live terminal progress for long-running commands (``scan``, ``explore``).

No dependencies (no ``rich``/``tqdm``) — the whole project has four runtime
deps and ships as a single self-contained tool, so this stays stdlib-only.

Three modes, chosen once per command by :func:`choose_mode` / :func:`make`:

* ``ANIMATED``   -- a real terminal: one live ``\\r``-redrawn line per phase,
  repainted by a background daemon thread; when a phase finishes its line is
  replaced with a settled one-line summary and the next phase's line starts
  fresh below it.
* ``MILESTONES`` -- not a terminal (piped, redirected, CI, the post-commit
  hook's ``codemap scan >log 2>&1``): plain ``\\n``-terminated lines at phase
  start, each 25% boundary, and phase end. No carriage returns, ever.
* ``SILENT``     -- ``--no-progress``, ``CODEMAP_NO_PROGRESS``, or
  ``explore --quiet``: nothing is written at all.

Deliberate constraints, load-bearing for the product, not just style:

* **No ANSI escapes of any kind** -- only ``\\r`` plus pad-to-clear. Must
  render correctly in Windows conhost, Git Bash/MINTTY, and Windows Terminal
  without enabling VT processing.
* **Everything goes to stderr.** ``stdout`` stays parseable (``explore
  --json``, ``snapshot``'s markdown, ``explain``'s report) and a run's
  progress lines carry wall-clock timings, which would otherwise break the
  "same input, same output" contract the rest of codemap holds to.
* A phase never mutates its own total mid-flight -- every total here
  (commits, files, symbols, timeline rows) is knowable before the phase
  opens, so "discovered late" support is intentionally not built.
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from pathlib import Path

ANIMATED = "animated"
MILESTONES = "milestones"
SILENT = "silent"

_FIRST_PAINT_DELAY = 0.15  # seconds -- a phase that finishes before this never flashes
_FRAME_INTERVAL = 0.08  # seconds (~12.5 fps)
_MIN_ANIMATABLE_WIDTH = 12
_BOUNCE_WIDTH = 3  # cells lit in the indeterminate "bouncing block"

_UNICODE_GLYPHS = {"full": "█", "empty": "░", "ellipsis": "…", "dash": "—"}
_ASCII_GLYPHS = {"full": "#", "empty": "-", "ellipsis": "...", "dash": "-"}


# --------------------------------------------------------------------------- mode


def choose_mode(stream, *, force_silent: bool = False) -> str:
    if force_silent:
        return SILENT
    if os.environ.get("CODEMAP_NO_PROGRESS"):
        return SILENT
    if os.environ.get("CI") or os.environ.get("TERM") == "dumb" or os.environ.get("NO_COLOR") is not None:
        return MILESTONES
    try:
        return ANIMATED if stream.isatty() else MILESTONES
    except Exception:
        # tracer._CaptureStream defines only write()/flush() -- no isatty() --
        # so a trace of codemap-on-codemap (`codemap trace -- -m codemap scan`)
        # must degrade quietly here rather than crash the inner command.
        return MILESTONES


def _supports_unicode(stream) -> bool:
    enc = getattr(stream, "encoding", None) or "ascii"
    try:
        "█░…".encode(enc)
        return True
    except (LookupError, UnicodeEncodeError):
        return False


# --------------------------------------------------------------------------- compose


def _fmt_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s"


def _ellipsize(text: str, budget: int, glyphs: dict) -> str:
    if budget <= 0:
        return ""
    if len(text) <= budget:
        return text
    e = glyphs["ellipsis"]
    if len(e) >= budget:
        return text[:budget]
    keep = budget - len(e)
    if "/" in text:
        # a repo-relative path -- the tail (filename) is the informative end
        return e + text[-keep:]
    return text[:keep] + e


def _bar(frac: float, width: int, glyphs: dict) -> str:
    filled = round(max(0.0, min(1.0, frac)) * width)
    return glyphs["full"] * filled + glyphs["empty"] * (width - filled)


def _bounce(frame: int, width: int, glyphs: dict) -> str:
    span = max(width - _BOUNCE_WIDTH, 1)
    period = span * 2
    pos = frame % period
    if pos > span:
        pos = period - pos
    return glyphs["empty"] * pos + glyphs["full"] * _BOUNCE_WIDTH + glyphs["empty"] * (width - pos - _BOUNCE_WIDTH)


def compose(
    label: str,
    n: int,
    total: int | None,
    elapsed: float,
    detail: str,
    *,
    width: int,
    glyphs: dict = _UNICODE_GLYPHS,
    frame: int = 0,
) -> str:
    """Pure line-composition function -- no I/O, no clock, no state.

    Field order is fixed: indent | label | bar | pct | counts | elapsed |
    detail. Detail is last because it is the only field allowed to shrink, so
    it absorbs whatever width remains. Never returns a line >= ``width``
    columns -- writing into the terminal's last column causes an unwanted
    auto-wrap in Windows conhost that breaks ``\\r`` redraw permanently, so
    callers must pass ``width - 1`` of the real terminal width.
    """
    budget = max(width, 0)
    indent = "  "
    lab = f"{label:<8}"
    prefix = indent + lab
    budget -= len(prefix)
    if budget <= 0:
        return prefix[:width]

    determinate = total is not None and total > 0
    pct = f"{(n * 100 // total) if determinate else 0:>3}%" if determinate else ""
    counts = f"{n}/{total}" if determinate else (str(n) if n else "")
    elapsed_s = _fmt_elapsed(elapsed)

    # degradation ladder: drop fields in this order until the detail budget fits
    show_elapsed = show_bar = show_counts = True
    bar_width = 20
    for _ in range(6):
        parts_width = (
            (bar_width + 1 + len(pct) if show_bar and determinate else 0)
            + (len(counts) + 1 if show_counts and counts else 0)
            + (len(elapsed_s) + 1 if show_elapsed else 0)
        )
        detail_budget = budget - parts_width - (1 if detail else 0)
        if detail_budget >= _MIN_ANIMATABLE_WIDTH or not detail:
            break
        if show_elapsed:
            show_elapsed = False
        elif show_bar and bar_width > 10:
            bar_width = 10
        elif show_bar:
            show_bar = False
        elif detail:
            detail = ""
        elif show_counts:
            show_counts = False
        else:
            break
    else:
        detail_budget = max(budget - parts_width, 0)

    bits = [prefix]
    if show_bar and determinate:
        bits.append(_bar(n / total, bar_width, glyphs) + " " + pct)
    elif show_bar and not determinate:
        bits.append(_bounce(frame, bar_width, glyphs))
    if show_counts and counts:
        bits.append(counts)
    if show_elapsed:
        bits.append(elapsed_s)
    line = "  ".join(bits)

    remaining = budget - (len(line) - len(prefix))
    if detail and remaining > 3:
        d = _ellipsize(detail, remaining - 2, glyphs)
        if d:
            line += "  " + d
    return line[:width] if width > 0 else ""


def _settled_line(label: str, summary: str, elapsed: float) -> str:
    return f"  {label:<8}  {summary}   {_fmt_elapsed(elapsed)}"


def _milestone_line(label: str, text: str) -> str:
    return f"{label}: {text}" if ":" not in text and "%" not in text else f"{label} {text}"


# --------------------------------------------------------------------------- painter


class _Painter:
    """Owns the stream, the paint lock, and (in animated mode) a repaint
    thread. The thread is the *only* place ``compose()`` results reach the
    stream -- callers only ever mutate cheap counters."""

    def __init__(
        self,
        stream,
        *,
        threaded: bool,
        clock=time.monotonic,
        width: int | None = None,
        on_first_paint=None,
    ) -> None:
        self._stream = stream
        self._clock = clock
        self._fixed_width = width
        self._on_first_paint = on_first_paint
        self._painted_once = False
        self._lock = threading.Lock()
        self._last_cols = 0
        self._disabled = False
        self._active = None  # the live Phase, or None
        self._stop = threading.Event()
        self._thread = None
        if threaded:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    # -- width -------------------------------------------------------------

    def _columns(self) -> int:
        if self._fixed_width is not None:
            return self._fixed_width
        try:
            cols = shutil.get_terminal_size(fallback=(80, 24)).columns
        except Exception:
            cols = 80
        return cols if cols > 0 else 80

    # -- raw writes (always under the lock) --------------------------------

    def _write(self, s: str) -> None:
        try:
            self._stream.write(s)
            self._stream.flush()
        except Exception:
            self._disabled = True

    def _erase_locked(self) -> None:
        if self._last_cols:
            self._write("\r" + " " * self._last_cols + "\r")
            self._last_cols = 0

    def _paint_locked(self, phase, frame: int) -> None:
        if self._disabled or phase is None:
            return
        phase.painted = True
        if not self._painted_once:
            self._painted_once = True
            if self._on_first_paint is not None:
                self._on_first_paint()  # writes the header line, once, before the first frame
        width = max(self._columns() - 1, 0)
        line = compose(
            phase.label, phase.n, phase.total, self._clock() - phase.opened_at,
            phase.detail_text, width=width, glyphs=phase.glyphs, frame=frame,
        )
        pad = max(0, self._last_cols - len(line))
        self._write("\r" + line + (" " * pad))
        self._last_cols = len(line)

    def settle_locked(self, phase, line: str | None) -> None:
        self._erase_locked()
        if line is not None:
            self._write(line + "\n")

    # -- lifecycle -----------------------------------------------------------

    def attach(self, phase) -> None:
        with self._lock:
            self._active = phase

    def detach(self, phase) -> None:
        """Stop repainting ``phase`` (if it is the active one) and erase its
        line -- a one-shot, permanent unbind, not a pause. See
        ``Phase.suspend_animation``."""
        with self._lock:
            if self._active is phase:
                self._active = None
            self._erase_locked()

    def detach_and_settle(self, phase, line: str | None) -> None:
        with self._lock:
            if self._active is phase:
                self._active = None
            self.settle_locked(phase, line)

    def suspend(self):
        self._lock.acquire()
        try:
            self._erase_locked()
        except Exception:
            self._lock.release()
            raise
        return self

    def resume(self) -> None:
        try:
            pass
        finally:
            self._lock.release()

    def paint_now(self) -> None:
        """Manual/synchronous frame -- used by the untimed test driver and by
        milestone/no-thread modes where nothing repaints on its own."""
        with self._lock:
            self._paint_locked(self._active, 0)

    def _loop(self) -> None:
        started_at = {}
        frame = 0
        while not self._stop.wait(_FRAME_INTERVAL):
            try:
                with self._lock:
                    phase = self._active
                    if phase is None:
                        continue
                    first_seen = started_at.setdefault(id(phase), self._clock())
                    if self._clock() - first_seen < _FIRST_PAINT_DELAY:
                        continue
                    self._paint_locked(phase, frame)
                frame += 1
            except Exception:
                self._disabled = True
                return

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
        with self._lock:
            self._erase_locked()


# --------------------------------------------------------------------------- Phase


class Phase:
    def __init__(self, reporter: "Reporter", label: str, total: int | None, unit: str) -> None:
        self._reporter = reporter
        self.label = label
        self.total = total
        self.unit = unit
        self.n = 0
        self.detail_text = ""
        self.summary_override = None
        self.painted = False
        self.glyphs = reporter._glyphs
        self.opened_at = reporter._clock()
        self._counter_fn = None
        self._last_bucket = 0
        self._closed = False

    # -- public API ----------------------------------------------------------

    def advance(self, n: int = 1, *, detail: str = "") -> None:
        self.n += n
        if detail:
            self.detail_text = detail
        self._reporter._on_advance(self)

    def set_detail(self, detail: str) -> None:
        self.detail_text = detail

    def set_summary(self, text: str) -> None:
        self.summary_override = text

    def bind_counter(self, fn) -> None:
        """Let the painter poll ``fn()`` for the live count instead of relying
        on ``advance()`` -- for a hot loop (the trace profiler callback) where
        adding a write is unacceptable."""
        self._counter_fn = fn

    def suspend_animation(self) -> None:
        """Permanently stop repainting this phase's line -- for ``trace``,
        once the target program starts printing its own output, so it never
        interleaves with a ``\\r``-redrawn line. No-op outside animated mode;
        the phase keeps working (counters, the eventual settled line) either
        way."""
        painter = getattr(self._reporter, "_painter", None)
        if painter is not None:
            painter.detach(self)

    def _live_n(self) -> int:
        if self._counter_fn is not None:
            try:
                return self._counter_fn()
            except Exception:
                return self.n
        return self.n

    def __enter__(self) -> "Phase":
        self._reporter._open(self)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._reporter._close_phase(self, aborted=exc_type is not None)


class _NullPhase:
    label = ""
    total = None
    n = 0
    detail_text = ""

    def advance(self, n: int = 1, *, detail: str = "") -> None:
        pass

    def set_detail(self, detail: str) -> None:
        pass

    def set_summary(self, text: str) -> None:
        pass

    def bind_counter(self, fn) -> None:
        pass

    def suspend_animation(self) -> None:
        pass

    def __enter__(self) -> "_NullPhase":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        pass


NO_PHASE = _NullPhase()


# --------------------------------------------------------------------------- Reporter


class Reporter:
    def __init__(
        self,
        stream,
        *,
        mode: str = MILESTONES,
        command: str = "",
        root: Path | None = None,
        clock=time.monotonic,
        width: int | None = None,
        threaded: bool = True,
    ) -> None:
        self.mode = mode
        self._stream = stream  # bound once, here -- never re-resolved from sys.stderr later
        self._command = command
        self._root = root
        self._clock = clock
        self._glyphs = _UNICODE_GLYPHS if (mode != SILENT and _supports_unicode(stream)) else _ASCII_GLYPHS
        self._header_written = False
        self._closed = False
        self._painter = (
            _Painter(stream, threaded=threaded, clock=clock, width=width, on_first_paint=self._write_header)
            if mode == ANIMATED
            else None
        )

    # -- header --------------------------------------------------------------

    def _write_header(self) -> None:
        if self._header_written or self.mode != ANIMATED:
            return
        self._header_written = True
        where = f"  {self._root}" if self._root else ""
        try:
            self._stream.write(f"codemap {self._command}{where}\n")
            self._stream.flush()
        except Exception:
            pass

    # -- phases ----------------------------------------------------------------

    def phase(self, label: str, total: int | None = None, *, unit: str = "") -> Phase:
        if self.mode == SILENT:
            return NO_PHASE
        return Phase(self, label, total, unit)

    def _open(self, phase: Phase) -> None:
        if self.mode == ANIMATED:
            if phase.total == 0:
                return
            self._painter.attach(phase)
        elif self.mode == MILESTONES:
            if phase.total == 0:
                return
            unit = phase.unit or "items"
            text = f"{phase.total} {unit}" if phase.total else "starting"
            self._write_line(_milestone_line(phase.label, text))

    def _on_advance(self, phase: Phase) -> None:
        if self.mode != MILESTONES or not phase.total:
            return
        bucket = (phase.n * 4) // phase.total
        if phase._last_bucket < bucket < 4:
            phase._last_bucket = bucket
            unit = phase.unit or "items"
            pct = bucket * 25
            self._write_line(f"{phase.label} {pct}% — {phase.n}/{phase.total} {unit}")

    def _summary_text(self, phase: Phase) -> str:
        if phase.summary_override:
            return phase.summary_override
        unit = phase.unit or "items"
        n = phase._live_n() if phase.total is None else phase.n
        return f"{n} {unit}"

    def _close_phase(self, phase: Phase, *, aborted: bool) -> None:
        if phase._closed:
            return
        phase._closed = True
        elapsed = self._clock() - phase.opened_at

        if self.mode == ANIMATED:
            if phase.total == 0:
                return
            if aborted:
                summary = f"interrupted at {phase.n}/{phase.total}" if phase.total else f"interrupted at {phase.n}"
                self._painter.detach_and_settle(phase, _settled_line(phase.label, summary, elapsed))
                return
            if not phase.painted:
                # never painted (finished inside the first-paint grace window) --
                # stay exactly as quiet as an unmodified run would have been
                self._painter.detach_and_settle(phase, None)
                return
            self._painter.detach_and_settle(phase, _settled_line(phase.label, self._summary_text(phase), elapsed))
        elif self.mode == MILESTONES:
            if phase.total == 0:
                return
            unit = phase.unit or "items"
            if aborted:
                self._write_line(f"{phase.label} interrupted at {phase.n}/{phase.total or '?'} {unit}")
            else:
                self._write_line(f"{phase.label} done — {self._summary_text(phase)} in {_fmt_elapsed(elapsed)}")

    def _write_line(self, text: str) -> None:
        try:
            self._stream.write(text + "\n")
            self._stream.flush()
        except Exception:
            pass

    def tick(self) -> None:
        """Force one synchronous repaint. No-op in milestone/silent mode, or
        when the background thread is already running. Exists so tests can
        drive a deterministic frame with ``threaded=False`` instead of
        depending on thread scheduling or wall-clock time."""
        if self._painter is not None:
            self._painter.paint_now()

    # -- pause/resume for a tee'd stream (tracer) -----------------------------

    def suspend(self):
        """Erase the active line and hold the paint lock so nothing else can
        write until :meth:`resume`. No-op outside animated mode."""
        if self._painter is None:
            return _NullSuspend()
        self._painter.suspend()
        return _PainterSuspend(self._painter)

    # -- lifecycle -------------------------------------------------------------

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._painter is not None:
            self._painter.close()


class _NullSuspend:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _PainterSuspend:
    def __init__(self, painter: _Painter) -> None:
        self._painter = painter

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._painter.resume()
        return False


# --------------------------------------------------------------------------- singleton + factory

NULL = Reporter(None, mode=SILENT)


def make(
    stream=None,
    *,
    command: str = "",
    root: Path | None = None,
    mode: str | None = None,
    **kwargs,
) -> Reporter:
    stream = stream if stream is not None else sys.stderr
    mode = mode if mode is not None else choose_mode(stream)
    if mode == SILENT:
        return NULL
    return Reporter(stream, mode=mode, command=command, root=root, **kwargs)


def from_args(args, *, command: str, root, stream=None) -> Reporter:
    stream = stream if stream is not None else sys.stderr
    force_silent = bool(getattr(args, "no_progress", False)) or bool(getattr(args, "quiet", False))
    mode = choose_mode(stream, force_silent=force_silent)
    if mode == SILENT:
        return NULL
    return Reporter(stream, mode=mode, command=command, root=root)
