"""SQLite 记忆：聊天记录、长期事实、刷到过的视频、运行状态。"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);

CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_facts_created ON facts(created_at);

CREATE TABLE IF NOT EXISTS videos (
    bvid TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    cover TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    duration INTEGER NOT NULL DEFAULT 0,
    stats TEXT NOT NULL DEFAULT '{}',
    first_seen_at REAL NOT NULL,
    shared_at REAL
);
CREATE INDEX IF NOT EXISTS idx_videos_seen ON videos(first_seen_at);

CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    idle_seconds INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activity_created ON activity(created_at);

CREATE TABLE IF NOT EXISTS sticker_usage (
    kind TEXT NOT NULL,
    sticker_id TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 1,
    last_seen REAL NOT NULL,
    PRIMARY KEY (kind, sticker_id)
);

CREATE TABLE IF NOT EXISTS learned_videos (
    bvid TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    skill_id TEXT NOT NULL DEFAULT '',
    learned_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS computer_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    params TEXT NOT NULL DEFAULT '{}',
    result TEXT NOT NULL DEFAULT '{}',
    ok INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS token_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    estimated INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_token_usage_created ON token_usage(created_at);

CREATE TABLE IF NOT EXISTS shares (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bvid TEXT NOT NULL,
    score INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shares_created ON shares(created_at);
CREATE INDEX IF NOT EXISTS idx_shares_bvid ON shares(bvid, created_at);
"""


class Memory:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ 基础
    def connect(self) -> "Memory":
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(SCHEMA)
        conn.commit()
        self._conn = conn
        return self

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Memory 还没 connect()")
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def _execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, tuple(params))
            self.conn.commit()
            return cur

    def _query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self.conn.execute(sql, tuple(params)))

    # ------------------------------------------------------------------ 聊天
    def add_message(self, session_id: str, role: str, content: str, created_at: float | None = None) -> None:
        self._execute(
            "INSERT INTO messages(session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, role, content, time.time() if created_at is None else created_at),
        )

    def recent_messages(self, session_id: str, limit: int = 20) -> list[dict[str, str]]:
        if limit <= 0:
            return []
        rows = self._query(
            "SELECT role, content, created_at FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        )
        return [
            {"role": row["role"], "content": row["content"], "created_at": row["created_at"]}
            for row in reversed(rows)
        ]

    def clear_session(self, session_id: str) -> int:
        cur = self._execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        return cur.rowcount

    def count_messages(self, session_id: str) -> int:
        rows = self._query("SELECT COUNT(*) AS n FROM messages WHERE session_id = ?", (session_id,))
        return int(rows[0]["n"]) if rows else 0

    # ------------------------------------------------------------------ 事实
    def add_fact(self, content: str, source: str = "", created_at: float | None = None) -> bool:
        content = content.strip()
        if not content:
            return False
        now = time.time() if created_at is None else created_at
        rows = self._query("SELECT id FROM facts WHERE content = ? LIMIT 1", (content,))
        if rows:
            self._execute("UPDATE facts SET created_at = ?, source = ? WHERE id = ?", (now, source, rows[0]["id"]))
            return False
        self._execute(
            "INSERT INTO facts(content, source, created_at) VALUES (?, ?, ?)",
            (content, source, now),
        )
        return True

    def list_facts(self, limit: int = 50) -> list[str]:
        rows = self._query("SELECT content FROM facts ORDER BY created_at DESC LIMIT ?", (limit,))
        return [row["content"] for row in rows]

    def search_facts(self, keyword: str, limit: int = 10) -> list[str]:
        like = f"%{keyword.strip()}%"
        rows = self._query(
            "SELECT content FROM facts WHERE content LIKE ? ORDER BY created_at DESC LIMIT ?",
            (like, limit),
        )
        return [row["content"] for row in rows]

    def count_facts(self) -> int:
        rows = self._query("SELECT COUNT(*) AS n FROM facts")
        return int(rows[0]["n"]) if rows else 0

    # ------------------------------------------------------------------ 视频
    def upsert_video(
        self,
        *,
        bvid: str,
        title: str,
        author: str = "",
        url: str = "",
        cover: str = "",
        description: str = "",
        duration: int = 0,
        stats: dict[str, Any] | None = None,
        first_seen_at: float | None = None,
    ) -> bool:
        """记录一条刷到的视频，返回是否是新视频。"""
        now = time.time() if first_seen_at is None else first_seen_at
        stats_json = json.dumps(stats or {}, ensure_ascii=False)
        cur = self._execute(
            "INSERT OR IGNORE INTO videos(bvid, title, author, url, cover, description, duration, stats, first_seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (bvid, title, author, url, cover, description, duration, stats_json, now),
        )
        if cur.rowcount > 0:
            return True
        self._execute(
            "UPDATE videos SET title = ?, author = ?, url = ?, cover = ?, description = ?, duration = ?, stats = ? "
            "WHERE bvid = ?",
            (title, author, url, cover, description, duration, stats_json, bvid),
        )
        return False

    def unshared_videos(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT * FROM videos WHERE shared_at IS NULL ORDER BY first_seen_at DESC LIMIT ?",
            (limit,),
        )
        return [_video_row(row) for row in rows]

    def recent_videos(self, limit: int = 10, *, include_shared: bool = True) -> list[dict[str, Any]]:
        if include_shared:
            rows = self._query("SELECT * FROM videos ORDER BY first_seen_at DESC LIMIT ?", (limit,))
        else:
            rows = self._query(
                "SELECT * FROM videos WHERE shared_at IS NOT NULL ORDER BY shared_at DESC LIMIT ?", (limit,)
            )
        return [_video_row(row) for row in rows]

    def mark_video_shared(self, bvid: str, shared_at: float | None = None) -> None:
        self._execute(
            "UPDATE videos SET shared_at = ? WHERE bvid = ?",
            (time.time() if shared_at is None else shared_at, bvid),
        )

    def count_videos(self) -> int:
        rows = self._query("SELECT COUNT(*) AS n FROM videos")
        return int(rows[0]["n"]) if rows else 0

    # ------------------------------------------------------------------ 电脑活动
    def add_activity(
        self,
        app: str,
        title: str = "",
        idle_seconds: int = 0,
        created_at: float | None = None,
    ) -> None:
        self._execute(
            "INSERT INTO activity(app, title, idle_seconds, created_at) VALUES (?, ?, ?, ?)",
            (app, title, idle_seconds, time.time() if created_at is None else created_at),
        )

    def recent_activity(self, limit: int = 10, since: float | None = None) -> list[dict[str, Any]]:
        sql = "SELECT app, title, idle_seconds, created_at FROM activity"
        params: list[Any] = []
        if since is not None:
            sql += " WHERE created_at >= ?"
            params.append(since)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = self._query(sql, params)
        return [
            {
                "app": row["app"],
                "title": row["title"],
                "idle_seconds": row["idle_seconds"],
                "created_at": row["created_at"],
            }
            for row in reversed(rows)
        ]

    def prune_activity(self, days: int = 7) -> int:
        cutoff = time.time() - max(days, 1) * 86400
        cur = self._execute("DELETE FROM activity WHERE created_at < ?", (cutoff,))
        return cur.rowcount

    def count_activity(self) -> int:
        rows = self._query("SELECT COUNT(*) AS n FROM activity")
        return int(rows[0]["n"]) if rows else 0

    # ------------------------------------------------------------------ 表情使用
    def record_sticker(self, kind: str, sticker_id: str, seen_at: float | None = None) -> None:
        if not kind or not sticker_id:
            return
        now = time.time() if seen_at is None else seen_at
        self._execute(
            "INSERT INTO sticker_usage(kind, sticker_id, count, last_seen) VALUES (?, ?, 1, ?) "
            "ON CONFLICT(kind, sticker_id) DO UPDATE SET count = count + 1, last_seen = excluded.last_seen",
            (kind, sticker_id, now),
        )

    def top_stickers(self, kind: str = "face", limit: int = 10) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT kind, sticker_id, count, last_seen FROM sticker_usage "
            "WHERE kind = ? ORDER BY count DESC, last_seen DESC LIMIT ?",
            (kind, limit),
        )
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------ 学习记录
    def has_learned(self, bvid: str) -> bool:
        rows = self._query("SELECT 1 FROM learned_videos WHERE bvid = ? LIMIT 1", (bvid,))
        return bool(rows)

    def mark_learned(self, bvid: str, title: str = "", skill_id: str = "", learned_at: float | None = None) -> None:
        self._execute(
            "INSERT INTO learned_videos(bvid, title, skill_id, learned_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(bvid) DO UPDATE SET title = excluded.title, skill_id = excluded.skill_id, "
            "learned_at = excluded.learned_at",
            (bvid, title, skill_id, time.time() if learned_at is None else learned_at),
        )

    def recent_learned(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT bvid, title, skill_id, learned_at FROM learned_videos ORDER BY learned_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in rows]

    def count_learned(self) -> int:
        rows = self._query("SELECT COUNT(*) AS n FROM learned_videos")
        return int(rows[0]["n"]) if rows else 0

    # ------------------------------------------------------------------ 电脑操作审计
    def add_computer_action(
        self,
        action: str,
        params: dict[str, Any],
        result: dict[str, Any],
        *,
        ok: bool,
        created_at: float | None = None,
    ) -> None:
        self._execute(
            "INSERT INTO computer_actions(action, params, result, ok, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                action,
                json.dumps(params, ensure_ascii=False, default=str),
                json.dumps(result, ensure_ascii=False, default=str),
                int(ok),
                time.time() if created_at is None else created_at,
            ),
        )

    def recent_computer_actions(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT action, params, result, ok, created_at FROM computer_actions ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        result = []
        for row in rows:
            try:
                params = json.loads(row["params"])
            except (TypeError, json.JSONDecodeError):
                params = {}
            try:
                payload = json.loads(row["result"])
            except (TypeError, json.JSONDecodeError):
                payload = {}
            result.append(
                {
                    "action": row["action"],
                    "params": params,
                    "result": payload,
                    "ok": bool(row["ok"]),
                    "created_at": row["created_at"],
                }
            )
        return result

    # ------------------------------------------------------------------ 状态
    def set_state(self, key: str, value: Any) -> None:
        self._execute(
            "INSERT INTO state(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    def get_state(self, key: str, default: Any = None) -> Any:
        rows = self._query("SELECT value FROM state WHERE key = ?", (key,))
        if not rows:
            return default
        try:
            return json.loads(rows[0]["value"])
        except (TypeError, json.JSONDecodeError):
            return rows[0]["value"]

    def get_float(self, key: str, default: float = 0.0) -> float:
        value = self.get_state(key, default)
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def get_int(self, key: str, default: int = 0) -> int:
        value = self.get_state(key, default)
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def delete_state(self, key: str) -> None:
        self._execute("DELETE FROM state WHERE key = ?", (key,))

    # ------------------------------------------------------------------ 用量
    def record_usage(
        self,
        *,
        model: str = "",
        source: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int | None = None,
        estimated: bool = False,
        created_at: float | None = None,
    ) -> None:
        prompt = max(int(prompt_tokens or 0), 0)
        completion = max(int(completion_tokens or 0), 0)
        total = prompt + completion if total_tokens is None else max(int(total_tokens), 0)
        self._execute(
            "INSERT INTO token_usage(created_at, model, source, prompt_tokens, "
            "completion_tokens, total_tokens, estimated) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                time.time() if created_at is None else created_at,
                model,
                source,
                prompt,
                completion,
                total,
                1 if estimated else 0,
            ),
        )

    def usage_rows(self, since: float) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT created_at, model, source, prompt_tokens, completion_tokens, "
            "total_tokens, estimated FROM token_usage WHERE created_at >= ? ORDER BY created_at",
            (since,),
        )
        return [dict(row) for row in rows]

    def count_usage(self) -> int:
        rows = self._query("SELECT COUNT(*) AS n FROM token_usage")
        return int(rows[0]["n"]) if rows else 0

    # ------------------------------------------------------------------ 分享
    def record_share(
        self,
        bvid: str,
        score: int = 0,
        reason: str = "",
        created_at: float | None = None,
    ) -> None:
        self._execute(
            "INSERT INTO shares(bvid, score, reason, created_at) VALUES (?, ?, ?, ?)",
            (bvid, int(score), reason, time.time() if created_at is None else created_at),
        )

    def share_count_since(self, since: float) -> int:
        rows = self._query("SELECT COUNT(*) AS n FROM shares WHERE created_at >= ?", (since,))
        return int(rows[0]["n"]) if rows else 0

    def has_shared_since(self, bvid: str, since: float) -> bool:
        rows = self._query(
            "SELECT 1 FROM shares WHERE bvid = ? AND created_at >= ? LIMIT 1", (bvid, since)
        )
        return bool(rows)

    def recent_shares(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT bvid, score, reason, created_at FROM shares ORDER BY id DESC LIMIT ?",
            (max(1, limit),),
        )
        return [dict(row) for row in rows]


def _video_row(row: sqlite3.Row) -> dict[str, Any]:
    try:
        stats = json.loads(row["stats"])
    except (TypeError, json.JSONDecodeError):
        stats = {}
    return {
        "bvid": row["bvid"],
        "title": row["title"],
        "author": row["author"],
        "url": row["url"],
        "cover": row["cover"],
        "description": row["description"],
        "duration": row["duration"],
        "stats": stats,
        "first_seen_at": row["first_seen_at"],
        "shared_at": row["shared_at"],
    }
