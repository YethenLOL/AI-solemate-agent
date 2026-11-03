from __future__ import annotations

from pathlib import Path

import pytest

from dafeiyu.screen import ScreenError, ScreenReader


class FakeComputer:
    async def get_status(self):
        return {"app": "Safari", "window": "Python 教程 - 第 7 页"}


async def test_screen_disabled() -> None:
    reader = ScreenReader(enabled=False, computer=FakeComputer())  # type: ignore[arg-type]
    with pytest.raises(ScreenError, match="SCREEN_READ_ENABLED"):
        await reader.read()


async def test_screen_private_window_blocked() -> None:
    reader = ScreenReader(enabled=True, computer=FakeComputer())  # type: ignore[arg-type]
    with pytest.raises(ScreenError):
        reader._check_private("Safari", "无痕浏览")
    with pytest.raises(ScreenError):
        reader._check_private("1Password", "登录")


async def test_screen_read_with_fake_capture(tmp_path: Path, monkeypatch) -> None:
    reader = ScreenReader(
        enabled=True,
        computer=FakeComputer(),  # type: ignore[arg-type]
        cache_dir=tmp_path,
    )
    async def fake_ensure():
        return tmp_path / "ocr"

    async def fake_run(*args):
        if args[0].endswith("screencapture"):
            Path(args[-1]).write_bytes(b"fake-png" * 1000)
            return ""
        return "识别到的页面文字"

    monkeypatch.setattr(reader, "_ensure_ocr_binary", fake_ensure)
    monkeypatch.setattr(reader, "_run", fake_run)
    result = await reader.read()
    assert result["text"] == "识别到的页面文字"
    assert result["app"] == "Safari"
