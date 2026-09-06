"""codemap/progress.py -- the animated/milestone progress reporter.

No test here depends on thread scheduling or wall-clock time: animated-mode
tests build the ``Reporter`` with ``threaded=False`` and drive frames with
``rep.tick()`` explicitly (see ``codemap/progress.py``'s own ``Reporter.tick``
docstring for why that knob exists).
"""

from __future__ import annotations

import pytest

from codemap import config, db, indexer, progress


class FakeStream:
    """A minimal stand-in for a terminal (or a redirected file), with no
    real I/O -- just enough for ``write``/``flush``/``isatty``/``encoding``."""

    def __init__(self, *, tty: bool = True, encoding: str = "utf-8") -> None:
        self._tty = tty
        self.encoding = encoding
        self.chunks: list[str] = []
        self.flush_count = 0

    def write(self, s: str) -> int:
        self.chunks.append(s)
        return len(s)

    def flush(self) -> None:
        self.flush_count += 1

    def isatty(self) -> bool:
        return self._tty

    @property
    def text(self) -> str:
        return "".join(self.chunks)


def _render_terminal(text: str) -> list[str]:
    """Tiny terminal simulator: ``\\r`` resets the cursor to column 0 on the
    current (uncommitted) line, ``\\n`` commits it and starts a new one.
    Lets a test assert on what a real terminal would actually *show* after a
    sequence of ``\\r``-redraws, rather than poking at raw chunk internals."""
    lines = [""]
    col = 0
    for ch in text:
        if ch == "\r":
            col = 0
        elif ch == "\n":
            lines.append("")
            col = 0
        else:
            cur = lines[-1]
            cur = cur[:col] + ch + cur[col + 1 :] if col < len(cur) else cur + ch
            lines[-1] = cur
            col += 1
    return lines


def _animated(stream, **kwargs) -> progress.Reporter:
    return progress.Reporter(stream, mode=progress.ANIMATED, threaded=False, **kwargs)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests of `choose_mode`'s isatty-detection branch need the real
    environment, not the project's autouse `_no_progress_animation` fixture
    (conftest.py) -- and not this repo's own CI, which sets `CI=true` and
    would otherwise force MILESTONES regardless of the fake tty."""
    for var in ("CODEMAP_NO_PROGRESS", "CI", "TERM", "NO_COLOR"):
        monkeypatch.delenv(var, raising=False)


# --------------------------------------------------------------------------- compose()


def test_compose_percent_floors_never_shows_100_early():
    line29 = progress.compose("history", 29, 30, 4.1, "", width=78)
    line30 = progress.compose("history", 30, 30, 4.1, "", width=78)
    assert "96%" in line29
    assert "100%" not in line29
    assert "100%" in line30


def test_compose_label_is_padded_and_indented():
    line = progress.compose("x", 0, None, 0.0, "", width=78)
    assert line.startswith("  " + f"{'x':<8}")


@pytest.mark.parametrize("width", [10, 15, 20, 40, 80, 120, 200])
def test_compose_never_exceeds_the_given_width(width):
    line = progress.compose(
        "historylong", 12345, 999999, 123.4,
        "a/very/long/detail/path/that/keeps/going/forever.py",
        width=width,
    )
    assert len(line) <= width


def test_compose_indeterminate_has_no_percent_or_counts_by_default():
    line = progress.compose("graph", 0, None, 1.0, "", width=78)
    assert "%" not in line


# --------------------------------------------------------------------------- ellipsis


def test_ellipsize_path_detail_keeps_the_informative_tail():
    out = progress._ellipsize("codemap/site/model.py", 10, progress._UNICODE_GLYPHS)
    assert out.startswith(progress._UNICODE_GLYPHS["ellipsis"])
    assert out.endswith(".py")
    assert len(out) <= 10


def test_ellipsize_non_path_detail_keeps_the_head():
    out = progress._ellipsize("verylongsymbolnamewithnoseparator", 10, progress._UNICODE_GLYPHS)
    assert out.endswith(progress._UNICODE_GLYPHS["ellipsis"])
    assert len(out) <= 10


def test_ellipsize_short_text_is_untouched():
    assert progress._ellipsize("short.py", 40, progress._UNICODE_GLYPHS) == "short.py"


# --------------------------------------------------------------------------- mode selection


def test_choose_mode_tty_is_animated(clean_env):
    assert progress.choose_mode(FakeStream(tty=True)) == progress.ANIMATED


def test_choose_mode_non_tty_is_milestones(clean_env):
    assert progress.choose_mode(FakeStream(tty=False)) == progress.MILESTONES


def test_choose_mode_force_silent():
    assert progress.choose_mode(FakeStream(tty=True), force_silent=True) == progress.SILENT


def test_choose_mode_env_no_progress(clean_env, monkeypatch):
    monkeypatch.setenv("CODEMAP_NO_PROGRESS", "1")
    assert progress.choose_mode(FakeStream(tty=True)) == progress.SILENT


def test_choose_mode_ci_env_downgrades_to_milestones(clean_env, monkeypatch):
    monkeypatch.setenv("CI", "true")
    assert progress.choose_mode(FakeStream(tty=True)) == progress.MILESTONES


def test_choose_mode_survives_a_stream_with_no_isatty(clean_env):
    class NoIsatty:
        pass  # tracer._CaptureStream-shaped, minus the isatty() it does define

    assert progress.choose_mode(NoIsatty()) == progress.MILESTONES


def test_ascii_fallback_when_stream_encoding_cant_render_unicode():
    stream = FakeStream(encoding="cp1252")
    assert progress._supports_unicode(stream) is False
    rep = _animated(stream, width=60)
    assert rep._glyphs is progress._ASCII_GLYPHS


# --------------------------------------------------------------------------- animated rendering


def test_no_ansi_ever_and_settles_with_a_trailing_newline():
    stream = FakeStream()
    rep = _animated(stream, width=80, command="scan")
    with rep.phase("history", 3, unit="commits") as p:
        for i in range(3):
            p.advance(detail=f"sha{i}")
            rep.tick()
    rep.close()
    text = stream.text
    assert "\x1b" not in text
    assert "\r" in text
    assert text.endswith("\n")
    assert "3 commits" in text


def test_erase_leaves_no_trailing_garbage_after_a_shorter_frame():
    stream = FakeStream()
    rep = _animated(stream, width=200)
    with rep.phase("worktree", 100, unit="files") as p:
        p.advance(detail="a" * 80)
        rep.tick()
        p.advance(detail="b")
        rep.tick()
    rep.close()
    lines = _render_terminal(stream.text)
    settled = [ln for ln in lines if "worktree" in ln]
    assert settled
    assert "a" * 5 not in settled[-1]


def test_fast_phase_never_painted_is_completely_silent():
    """A phase that finishes before anything ever repaints it (the common
    case: `codemap scan` on an up-to-date repo) must stay exactly as quiet
    as an unmodified run -- no header, no settled line, nothing."""
    stream = FakeStream()
    rep = _animated(stream, width=80)
    with rep.phase("graph"):  # indeterminate, never ticked
        pass
    rep.close()
    assert stream.text == ""


def test_zero_total_phase_is_silent():
    stream = FakeStream()
    rep = _animated(stream, width=80)
    with rep.phase("worktree", 0, unit="files"):
        pass
    rep.close()
    assert stream.text == ""


def test_exception_inside_a_phase_settles_cleanly_and_reraises():
    stream = FakeStream()
    rep = _animated(stream, width=80)
    with pytest.raises(ValueError):
        with rep.phase("history", 5, unit="commits") as p:
            p.advance()
            rep.tick()
            raise ValueError("boom")
    rep.close()
    assert stream.text.endswith("\n")
    assert "interrupted" in stream.text


def test_close_is_idempotent():
    stream = FakeStream()
    rep = _animated(stream, width=80)
    with rep.phase("history", 1, unit="commits") as p:
        p.advance()
        rep.tick()
    rep.close()
    before = stream.text
    rep.close()
    assert stream.text == before


def test_header_written_once_before_the_first_frame_not_at_open():
    stream = FakeStream()
    rep = _animated(stream, width=80, command="scan")
    with rep.phase("history", 2, unit="commits") as p:
        assert stream.text == ""  # opening a phase alone writes nothing
        p.advance()
        rep.tick()
        assert stream.text.startswith("codemap scan")
        p.advance()
        rep.tick()
    rep.close()
    assert stream.text.count("codemap scan") == 1  # header never repeats


def test_suspend_animation_stops_repainting_and_erases():
    stream = FakeStream()
    rep = _animated(stream, width=80)
    with rep.phase("run", unit="steps") as p:
        p.advance(5)
        rep.tick()
        assert "\r" in stream.text
        p.suspend_animation()  # erases the live line -- one more write, then silence
        after_suspend_len = len(stream.text)
        p.advance(5)
        rep.tick()  # no active phase left to paint -- a no-op
        assert len(stream.text) == after_suspend_len
    rep.close()
    # the phase still settles normally once the target-run finishes
    assert "steps" in stream.text


# --------------------------------------------------------------------------- milestone mode


def test_milestone_mode_emits_exactly_the_boundary_lines():
    stream = FakeStream(tty=False)
    rep = progress.Reporter(stream, mode=progress.MILESTONES)
    with rep.phase("history", 30, unit="commits") as p:
        for _ in range(30):
            p.advance()
    lines = [ln for ln in stream.text.split("\n") if ln]
    assert len(lines) == 5
    assert "\r" not in stream.text
    assert lines[0] == "history: 30 commits"
    assert lines[-1].startswith("history done")


def test_milestone_mode_never_duplicates_the_100_percent_boundary():
    stream = FakeStream(tty=False)
    rep = progress.Reporter(stream, mode=progress.MILESTONES)
    with rep.phase("worktree", 4, unit="files") as p:
        for _ in range(4):
            p.advance()
    lines = [ln for ln in stream.text.split("\n") if ln]
    assert sum("100%" in ln for ln in lines) == 0
    assert lines[-1].startswith("worktree done")


def test_milestone_mode_zero_total_is_silent():
    stream = FakeStream(tty=False)
    rep = progress.Reporter(stream, mode=progress.MILESTONES)
    with rep.phase("worktree", 0, unit="files"):
        pass
    assert stream.text == ""


def test_milestone_mode_interrupted_line_on_exception():
    stream = FakeStream(tty=False)
    rep = progress.Reporter(stream, mode=progress.MILESTONES)
    with pytest.raises(ValueError):
        with rep.phase("history", 10, unit="commits") as p:
            p.advance(3)
            raise ValueError("boom")
    assert "interrupted at 3/10" in stream.text
    assert "\r" not in stream.text


# --------------------------------------------------------------------------- null reporter


def test_null_reporter_phase_is_the_shared_singleton_and_writes_nothing():
    assert progress.NULL.phase("x", 10) is progress.NO_PHASE
    with progress.NULL.phase("x", 10) as p:
        p.advance(5)
        p.set_detail("y")
        p.set_summary("z")
        p.bind_counter(lambda: 1)
        p.suspend_animation()
    progress.NULL.close()  # never raises


def test_make_returns_null_singleton_when_forced_silent():
    rep = progress.make(FakeStream(tty=True), mode=progress.SILENT)
    assert rep is progress.NULL


def test_from_args_quiet_flag_forces_silent():
    class Args:
        quiet = True
        no_progress = False

    rep = progress.from_args(Args(), command="explore", root=None, stream=FakeStream(tty=True))
    assert rep is progress.NULL


def test_from_args_no_progress_flag_forces_silent():
    class Args:
        quiet = False
        no_progress = True

    rep = progress.from_args(Args(), command="scan", root=None, stream=FakeStream(tty=True))
    assert rep is progress.NULL


def test_from_args_missing_attrs_defaults_to_not_silent(clean_env):
    """A hand-rolled test `class Args` stand-in without `no_progress` (as the
    three in tests/test_explore.py were before this feature) must not crash
    -- from_args() is the single place that reads the attribute defensively."""

    class Args:
        pass

    rep = progress.from_args(Args(), command="explore", root=None, stream=FakeStream(tty=False))
    assert rep.mode == progress.MILESTONES


# --------------------------------------------------------------------------- indexer.scan() regression guard


def test_scan_with_no_progress_argument_writes_nothing(fixture_repo, tmp_path, capsys):
    """The ~15 existing direct callers of indexer.scan() across this test
    suite never pass `progress=` -- confirms that path stays exactly as
    silent as it was before this feature, independent of TTY detection."""
    cfg = config.load(fixture_repo.path)
    conn = db.connect(tmp_path / "index.db")
    db.migrate(conn)
    indexer.scan(conn, cfg, until=fixture_repo.sha("c10-syntaxerror"))
    conn.close()
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""
