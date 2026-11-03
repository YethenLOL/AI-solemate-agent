"""抽帧看画面：ffmpeg 截取关键帧 + 本机 macOS Vision OCR。

只有装了 ffmpeg 才启用；没有就自动跳过，其余深读照常。
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .screen import ScreenReader, ScreenError

log = logging.getLogger(__name__)

FALLBACK_FFMPEG = (
    "/opt/homebrew/bin/ffmpeg",
    "/usr/local/bin/ffmpeg",
    "/opt/local/bin/ffmpeg",
    "/usr/bin/ffmpeg",
)


def find_ffmpeg(explicit: str = "") -> str:
    """查找顺序：显式配置 → FFMPEG 环境变量 → PATH → 常见安装位置。"""
    for candidate in (explicit, os.environ.get("FFMPEG", "")):
        value = (candidate or "").strip()
        if value and Path(value).expanduser().is_file():
            return str(Path(value).expanduser())
    found = shutil.which("ffmpeg")
    if found:
        return found
    for candidate in FALLBACK_FFMPEG:
        if Path(candidate).is_file():
            return candidate
    return ""


def pick_timestamps(
    hot: Iterable[dict[str, Any]],
    duration: int | float,
    count: int = 3,
) -> list[float]:
    """优先取弹幕热点开始后 5 秒；没有热点就均匀取。"""
    count = max(1, min(int(count), 6))
    stamps: list[float] = []
    for item in hot:
        try:
            start = float(item.get("开始秒") or 0)
        except (TypeError, ValueError):
            continue
        stamp = max(0.0, start + 5.0)
        if duration and stamp >= float(duration) - 1:
            stamp = max(0.0, float(duration) - 3.0)
        if stamp not in stamps:
            stamps.append(stamp)
        if len(stamps) >= count:
            break
    if not stamps:
        total = float(duration or 60)
        stamps = [total * (index + 1) / (count + 1) for index in range(count)]
    return sorted(stamps)[:count]


@dataclass(slots=True)
class FrameReadResult:
    count: int = 0
    text: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.count > 0


class FrameGrabber:
    def __init__(
        self,
        *,
        enabled: bool = True,
        ffmpeg: str = "",
        user_agent: str = "",
        cache_dir: str | Path | None = None,
        max_width: int = 720,
        timeout: float = 60.0,
        ocr_max_chars: int = 2000,
        screen: ScreenReader | None = None,
    ) -> None:
        self.enabled = enabled
        self.ffmpeg = find_ffmpeg(ffmpeg)
        self.user_agent = user_agent
        self.cache_dir = Path(cache_dir).expanduser() if cache_dir else None
        self.max_width = max(320, min(int(max_width), 1920))
        self.timeout = max(10.0, float(timeout))
        self.ocr_max_chars = max(200, int(ocr_max_chars))
        self.screen = screen or ScreenReader(enabled=True, cache_dir=cache_dir)

    @property
    def available(self) -> bool:
        return bool(self.enabled and self.ffmpeg)

    def status(self) -> str:
        if not self.enabled:
            return "抽帧已关闭（FRAMES_ENABLED=false）"
        if not self.ffmpeg:
            return "找不到 ffmpeg（装一下：brew install ffmpeg）"
        return f"ffmpeg: {self.ffmpeg}"

    async def _run(self, *args: str) -> str:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError("ffmpeg 超时") from None
        if process.returncode != 0:
            message = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError((message or f"ffmpeg 返回 {process.returncode}")[:400])
        return stdout.decode("utf-8", errors="replace").strip()

    def _command(
        self,
        source: str,
        stamp: float,
        target: Path,
        referer: str = "",
        *,
        input_seek: bool = True,
    ) -> list[str]:
        scale = f"scale='min({self.max_width},iw)':-2"
        command = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
        if str(source).startswith(("http://", "https://")):
            command += [
                "-reconnect",
                "1",
                "-reconnect_streamed",
                "1",
                "-reconnect_delay_max",
                "5",
            ]
        if self.user_agent:
            command += ["-user_agent", self.user_agent]
        if referer:
            command += ["-headers", f"Referer: {referer}\r\n"]
        stamp_text = f"{max(0.0, stamp):.2f}"
        if input_seek:
            command += ["-ss", stamp_text, "-i", source]
        else:
            command += ["-i", source, "-ss", stamp_text]
        command += [
            "-frames:v",
            "1",
            "-vf",
            scale,
            "-q:v",
            "3",
            str(target),
        ]
        return command

    async def _grab_one(
        self, source: str, stamp: float, target: Path, referer: str = ""
    ) -> bool:
        for attempt, input_seek in enumerate((True, False)):
            try:
                await self._run(
                    *self._command(source, stamp, target, referer, input_seek=input_seek)
                )
            except RuntimeError as exc:
                log.info(
                    "抽帧 %ss 失败（%s）：%s",
                    stamp,
                    "快速定位" if input_seek else "解码后定位",
                    exc,
                )
                continue
            if target.exists() and target.stat().st_size > 0:
                return True
            log.info("抽帧 %ss 没有产出图片", stamp)
        return False

    async def grab_file(
        self, source: str | Path, timestamps: list[float], *, referer: str = ""
    ) -> list[Path]:
        if not self.available:
            raise RuntimeError(self.status())
        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        workdir = Path(tempfile.mkdtemp(prefix="dafeiyu-frames-", dir=self.cache_dir))
        images: list[Path] = []
        try:
            for index, stamp in enumerate(timestamps, start=1):
                target = workdir / f"frame-{index:02d}.jpg"
                if await self._grab_one(str(source), stamp, target, referer):
                    images.append(target)
        except Exception:
            shutil.rmtree(workdir, ignore_errors=True)
            raise
        return images

    async def read_frames(
        self,
        source: str | Path,
        timestamps: list[float],
        *,
        referer: str = "",
    ) -> FrameReadResult:
        """抽帧并逐张 OCR；临时图片用完立即删除，只返回文字。"""
        if not self.available:
            return FrameReadResult(error=self.status())
        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix="dafeiyu-frames-", dir=self.cache_dir))
        texts: list[str] = []
        count = 0
        try:
            for index, stamp in enumerate(timestamps, start=1):
                target = folder / f"frame-{index:02d}.jpg"
                if not await self._grab_one(str(source), stamp, target, referer):
                    continue
                count += 1
                try:
                    text = await self.screen.ocr_file(target)
                except (ScreenError, RuntimeError, OSError) as exc:
                    log.debug("帧 OCR 失败：%s", exc)
                    continue
                cleaned = " ".join(text.split())
                if cleaned:
                    texts.append(f"[{stamp:.0f}s] {cleaned}")
            joined = "\n".join(texts)[: self.ocr_max_chars]
            return FrameReadResult(count=count, text=joined)
        finally:
            shutil.rmtree(folder, ignore_errors=True)


def cleanup_images(images: Iterable[Path]) -> None:
    for image in images:
        try:
            image.unlink()
        except OSError:
            pass
