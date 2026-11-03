"""两本日记：给主人的信、她自己的私人日记。

都是普通 Markdown，按天追加；只写不覆盖，方便主人随时打开看。
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

KINDS = {"owner": "给主人的信", "private": "私人日记"}
HEADERS = {"owner": "# 给主人的信", "private": "# 私人日记"}


class DiaryStore:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory).expanduser()
        self.directory.mkdir(parents=True, exist_ok=True)

    def path_for(self, kind: str) -> Path:
        if kind not in KINDS:
            raise ValueError(f"未知日记类型：{kind}")
        return self.directory / f"{kind}.md"

    def append(self, kind: str, text: str, *, now: datetime | None = None) -> Path:
        body = (text or "").strip()
        if not body:
            raise ValueError("日记内容不能为空")
        path = self.path_for(kind)
        now = now or datetime.now()
        day = now.strftime("%Y-%m-%d")
        block = f"## {day}\n\n{body}\n\n"
        if not path.exists():
            path.write_text(f"{HEADERS[kind]}\n\n{block}", encoding="utf-8")
        else:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(block)
        return path

    def read(self, kind: str, *, max_chars: int | None = None) -> str:
        path = self.path_for(kind)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""
        if max_chars and len(text) > max_chars:
            return "…（前面省略）\n" + text[-max_chars:]
        return text

    def recent(self, days: int = 7, *, max_chars: int = 3000) -> dict[str, str]:
        """取最近 N 天的两本日记，给主动聊天/夜间流程当上下文。"""
        cutoff = datetime.now().timestamp() - max(days, 1) * 86400
        result: dict[str, str] = {}
        for kind in KINDS:
            text = self.read(kind)
            entries = re.split(r"^## ", text, flags=re.MULTILINE)
            kept = []
            for entry in entries[1:]:
                header, _, body = entry.partition("\n")
                try:
                    stamp = datetime.strptime(header.strip()[:10], "%Y-%m-%d").timestamp()
                except ValueError:
                    continue
                if stamp >= cutoff:
                    kept.append(f"## {header.strip()}\n{body.strip()}")
            joined = "\n\n".join(kept).strip()
            if len(joined) > max_chars:
                joined = joined[-max_chars:]
            result[kind] = joined
        return result

    def latest(self, kind: str) -> str:
        text = self.read(kind)
        matches = list(re.finditer(r"^## .*$", text, flags=re.MULTILINE))
        if not matches:
            return ""
        return text[matches[-1].start() :].strip()
