"""按需读取当前屏幕文字：截图后在本机用 macOS Vision OCR，不保存截图。"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .computer import ComputerController, ComputerError

log = logging.getLogger(__name__)

SCREENCAPTURE = "/usr/sbin/screencapture"
PRIVATE_WORDS = (
    "无痕",
    "隐私浏览",
    "incognito",
    "private browsing",
    "1password",
    "keychain",
    "密码",
    "passwords",
    "bitwarden",
)

SWIFT_SOURCE = r'''import AppKit
import Foundation
import Vision

guard CommandLine.arguments.count >= 2 else {
    FileHandle.standardError.write(Data("usage: dafeiyu-ocr <image>\n".utf8))
    exit(2)
}
let path = CommandLine.arguments[1]
guard let image = NSImage(contentsOfFile: path) else {
    FileHandle.standardError.write(Data("cannot open image\n".utf8))
    exit(3)
}
var rect = CGRect(origin: .zero, size: image.size)
guard let cgImage = image.cgImage(forProposedRect: &rect, context: nil, hints: nil) else {
    FileHandle.standardError.write(Data("cannot decode image\n".utf8))
    exit(4)
}
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
request.recognitionLanguages = ["zh-Hans", "en-US"]
let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
do {
    try handler.perform([request])
} catch {
    FileHandle.standardError.write(Data("ocr failed: \(error)\n".utf8))
    exit(5)
}
let observations = request.results ?? []
let sorted = observations.sorted { left, right in
    let dy = abs(left.boundingBox.midY - right.boundingBox.midY)
    if dy > 0.008 {
        return left.boundingBox.midY > right.boundingBox.midY
    }
    return left.boundingBox.minX < right.boundingBox.minX
}
var lines: [String] = []
for observation in sorted {
    if let text = observation.topCandidates(1).first?.string, !text.isEmpty {
        lines.append(text)
    }
}
print(lines.joined(separator: "\n"))
'''


class ScreenError(RuntimeError):
    pass


class ScreenReader:
    def __init__(
        self,
        *,
        enabled: bool = False,
        computer: ComputerController | None = None,
        cache_dir: str | Path | None = None,
        timeout: float = 45.0,
        max_chars: int = 4000,
        private_words: tuple[str, ...] = PRIVATE_WORDS,
    ) -> None:
        self.enabled = enabled
        self.computer = computer
        self.cache_dir = Path(cache_dir).expanduser() if cache_dir else Path.home() / ".dafeiyu" / "bin"
        self.timeout = timeout
        self.max_chars = max_chars
        self.private_words = tuple(word.lower() for word in private_words)

    async def _run(self, *args: str) -> str:
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise ScreenError(f"启动命令失败：{exc}") from exc
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise ScreenError("屏幕读取超时") from None
        out = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()
        if process.returncode != 0:
            raise ScreenError((err or out or f"命令返回 {process.returncode}")[:400])
        return out

    async def _ensure_ocr_binary(self) -> Path:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        binary = self.cache_dir / "dafeiyu-ocr"
        source = self.cache_dir / "dafeiyu-ocr.swift"
        source.write_text(SWIFT_SOURCE, encoding="utf-8")
        if binary.exists() and binary.stat().st_mtime_ns >= source.stat().st_mtime_ns:
            return binary
        swiftc = shutil.which("swiftc")
        if not swiftc:
            raise ScreenError("找不到 swiftc；安装 Xcode Command Line Tools 后才能做本地 OCR")
        await self._run(swiftc, "-O", str(source), "-o", str(binary))
        if not binary.exists():
            raise ScreenError("OCR 编译失败")
        return binary

    def _check_private(self, app: str, window: str) -> None:
        haystack = f"{app} {window}".lower()
        if any(word and word in haystack for word in self.private_words):
            raise ScreenError("当前窗口看起来是隐私/密码页面，拒绝截图")

    async def read(self) -> dict[str, Any]:
        if not self.enabled:
            raise ScreenError("屏幕读取已关闭（SCREEN_READ_ENABLED=false）")
        if self.computer is None:
            raise ScreenError("屏幕读取没有配置电脑状态组件")
        try:
            status = await self.computer.get_status()
        except ComputerError as exc:
            raise ScreenError(f"读取前台窗口失败：{exc}") from exc
        app = str(status.get("app") or "")
        window = str(status.get("window") or "")
        self._check_private(app, window)
        binary = await self._ensure_ocr_binary()
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            image_path = Path(handle.name)
        try:
            await self._run(SCREENCAPTURE, "-x", "-m", "-t", "png", str(image_path))
            if not image_path.exists() or image_path.stat().st_size < 1000:
                raise ScreenError(
                    "截图为空，可能没有给大肥鱼/终端开启「屏幕录制」权限"
                )
            text = await self._run(str(binary), str(image_path))
        finally:
            image_path.unlink(missing_ok=True)
        text = text.strip()
        if not text:
            raise ScreenError("没有识别到文字；检查「屏幕录制」权限，或当前页面不是文字内容")
        truncated = len(text) > self.max_chars
        return {
            "ok": True,
            "action": "screen_read",
            "app": app,
            "window": window[:200],
            "text": text[: self.max_chars],
            "chars": len(text),
            "truncated": truncated,
        }
