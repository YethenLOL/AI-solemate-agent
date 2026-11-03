from __future__ import annotations

from pathlib import Path

from dafeiyu.frames import FrameGrabber, find_ffmpeg, pick_timestamps


def write_fake_ffmpeg(path: Path, log: Path | None = None) -> Path:
    script = path / "ffmpeg"
    lines = ["#!/bin/sh"]
    if log is not None:
        lines.append(f'printf "%s\\n" "$@" >> "{log}"')
    lines.append('for last in "$@"; do :; done')
    lines.append('printf "fake-jpeg" > "$last"')
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    script.chmod(0o755)
    return script


def test_find_ffmpeg_prefers_explicit(tmp_path: Path) -> None:
    fake = write_fake_ffmpeg(tmp_path)
    assert find_ffmpeg(str(fake)) == str(fake)


def test_pick_timestamps_uses_hotspots() -> None:
    hot = [{"开始秒": 60}, {"开始秒": 120}, {"开始秒": 300}]
    assert pick_timestamps(hot, 600, 2) == [65.0, 125.0]
    assert pick_timestamps([], 400, 3) == [100.0, 200.0, 300.0]
    assert pick_timestamps([{"开始秒": 598}], 600, 1) == [597.0]


async def test_grab_file_builds_ffmpeg_command(tmp_path: Path) -> None:
    log = tmp_path / "args.txt"
    fake = write_fake_ffmpeg(tmp_path, log)
    grabber = FrameGrabber(
        ffmpeg=str(fake),
        user_agent="test-agent",
        cache_dir=tmp_path / "cache",
        max_width=640,
        timeout=10,
    )
    images = await grabber.grab_file(
        "https://example.com/v.mp4", [12.5, 30.0], referer="https://www.bilibili.com/video/BV1"
    )

    assert len(images) == 2
    assert all(image.exists() and image.read_text() == "fake-jpeg" for image in images)
    args = log.read_text(encoding="utf-8")
    assert "-ss" in args
    assert "12.50" in args
    assert "30.00" in args
    assert "scale='min(640,iw)':-2" in args
    assert "Referer: https://www.bilibili.com/video/BV1" in args
    lines = args.splitlines()
    assert lines.index("-user_agent") < lines.index("-i")
    assert lines.index("-headers") < lines.index("-i")


async def test_read_frames_ocr_and_cleanup(tmp_path: Path) -> None:
    fake = write_fake_ffmpeg(tmp_path)

    class FakeScreen:
        async def ocr_file(self, path: Path) -> str:
            return "画面里的文字"

    grabber = FrameGrabber(
        ffmpeg=str(fake),
        cache_dir=tmp_path / "cache",
        timeout=10,
        ocr_max_chars=100,
        screen=FakeScreen(),  # type: ignore[arg-type]
    )
    result = await grabber.read_frames("input.mp4", [1.0, 2.0])

    assert result.ok and result.count == 2
    assert result.text == "[1s] 画面里的文字\n[2s] 画面里的文字"
    cache = tmp_path / "cache"
    leftovers = list(cache.glob("dafeiyu-frames-*")) if cache.exists() else []
    assert leftovers == []


def test_status_without_ffmpeg(tmp_path: Path) -> None:
    grabber = FrameGrabber(ffmpeg="/definitely/not/ffmpeg", enabled=False)
    assert not grabber.available
    assert "关闭" in grabber.status()


def test_command_supports_output_seek(tmp_path: Path) -> None:
    fake = write_fake_ffmpeg(tmp_path)
    grabber = FrameGrabber(ffmpeg=str(fake), user_agent="ua", cache_dir=tmp_path / "cache")
    args = grabber._command(
        "https://example.com/v.mp4", 10, tmp_path / "out.jpg", "https://b23.tv/x", input_seek=False
    )
    assert args.index("-i") < args.index("-ss")
    assert "-reconnect" in args
