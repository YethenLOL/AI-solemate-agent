"""macOS 控制台：配置、人格编辑、启动/停止机器人和日志。"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
from logging.handlers import RotatingFileHandler
import os
import queue
import socket
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any

APP_HOME = Path(
    os.environ.get("DAFEIYU_HOME", str(Path.home() / "Library/Application Support/Dafeiyu"))
).expanduser()
os.environ.setdefault("DAFEIYU_HOME", str(APP_HOME))

import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .app import (
    add_file_logging,
    check_services,
    learn_keywords_now,
    learn_now,
    learn_url_now,
    persona_test,
    relearn_skills_now,
    run_app,
)
from .config import APP_HOME as CONFIG_HOME
from .config import Config, ConfigError
from .llm import LLMClient
from .memory import Memory
from .persona import PersonaCard, build_system_prompt
from .skills import SkillStore
from .usage import PERIOD_BUCKETS, UsageBucket, aggregate, bucket_starts, human_tokens

log = logging.getLogger(__name__)

BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", str(Path(__file__).resolve().parent.parent)))
INSTALLER_CANDIDATES = (
    Path.home() / "Documents/NapCatInstaller.app",
    Path.home() / "Downloads/NapCatInstaller.app",
    Path.home() / "Downloads/NapCatInstaller/NapCatInstaller.app",
)


class QueueLogHandler(logging.Handler):
    def __init__(self, sink: queue.Queue[str]) -> None:
        super().__init__()
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.sink.put_nowait(self.format(record))
        except Exception:
            pass


def ensure_app_home() -> None:
    APP_HOME.mkdir(parents=True, exist_ok=True)
    (APP_HOME / "data").mkdir(exist_ok=True)
    (APP_HOME / "personas").mkdir(exist_ok=True)

    env_target = APP_HOME / ".env"
    if not env_target.exists():
        for source in (BUNDLE_ROOT / ".env.example", Path.cwd() / ".env.example"):
            if source.exists():
                env_target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
                break
    # 从源码目录启动时，顺手导入现有配置，避免重新填一遍
    source_env = BUNDLE_ROOT / ".env"
    if not env_target.exists() and source_env.exists():
        env_target.write_text(source_env.read_text(encoding="utf-8"), encoding="utf-8")

    persona_target = APP_HOME / "personas" / "dafeiyu.md"
    if not persona_target.exists():
        for source in (BUNDLE_ROOT / "personas" / "dafeiyu.md", Path.cwd() / "personas" / "dafeiyu.md"):
            if source.exists():
                persona_target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
                break


def set_env_values(path: Path, updates: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen: set[str] = set()
    output: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if stripped and not stripped.startswith("#") and "=" in line:
            key = line.split("=", 1)[0].strip()
            if key in updates:
                output.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        output.append(line)
    for key, value in updates.items():
        if key not in seen:
            output.append(f"{key}={value}")
    path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")


def port_open(port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


class DafeiyuApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("大肥鱼 · 奥米控制台")
        self.geometry("1040x760")
        self.minsize(900, 640)
        self.log_queue: queue.Queue[str] = queue.Queue()
        self._bot_thread: threading.Thread | None = None
        self._bot_loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._persona_path: Path | None = None

        self._setup_logging()
        self._make_vars()
        self._build_ui()
        self._load_config()
        self._load_persona()

        self.after(200, self._drain_logs)
        self.after(1200, self.refresh_status)
        self.after(600, self._usage_tick)
        if os.environ.get("DAFEIYU_AUTOSTART") == "1":
            self.after(1500, self.start_bot)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------------------------------------------------------ 基础
    def _setup_logging(self) -> None:
        handler = QueueLogHandler(self.log_queue)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        root.addHandler(handler)
        add_file_logging(APP_HOME / "logs" / "dafeiyu.log")
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("websockets").setLevel(logging.WARNING)

    def _make_vars(self) -> None:
        self.var_api_key = tk.StringVar()
        self.var_owner = tk.StringVar()
        self.var_model = tk.StringVar(value="deepseek-chat")
        self.var_base_url = tk.StringVar(value="https://api.deepseek.com/v1")
        self.var_ws_url = tk.StringVar(value="ws://127.0.0.1:3001")
        self.var_token = tk.StringVar()
        self.var_proactive = tk.BooleanVar(value=True)
        self.var_computer = tk.BooleanVar(value=True)
        self.var_activity = tk.BooleanVar(value=True)
        self.var_learning = tk.BooleanVar(value=True)
        self.var_dsh = tk.BooleanVar(value=False)
        self.var_dsh_chat = tk.BooleanVar(value=False)
        self.var_screen = tk.BooleanVar(value=False)
        self.var_skill_url = tk.StringVar()
        self.var_skill_keywords = tk.StringVar()
        self.var_cookie = tk.StringVar()
        self.var_status_webui = tk.StringVar(value="检测中…")
        self.var_status_ws = tk.StringVar(value="检测中…")
        self.var_status_bot = tk.StringVar(value="已停止")
        self.var_persona_stats = tk.StringVar(value="")
        self.var_test_message = tk.StringVar(value="在吗，今天有点累")
        self.var_show_key = tk.BooleanVar(value=False)
        self.var_usage_period = tk.StringVar(value="day")
        self.var_usage_total = tk.StringVar(value="—")
        self.var_usage_detail = tk.StringVar(value="还没有记录")
        self._usage_buckets: list[UsageBucket] = []

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        header = ttk.Frame(self, padding=(18, 12))
        header.pack(fill="x")
        ttk.Label(header, text="大肥鱼 · 奥米控制台", font=("SF Pro Display", 20, "bold")).pack(side="left")
        ttk.Label(header, text="QQ × B站 × 电脑感知", foreground="#888").pack(side="left", padx=12)

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self._build_overview(notebook)
        self._build_config(notebook)
        self._build_persona(notebook)
        self._build_skills(notebook)
        self._build_logs(notebook)
        self._build_help(notebook)

    def _build_overview(self, notebook: ttk.Notebook) -> None:
        frame = ttk.Frame(notebook, padding=18)
        notebook.add(frame, text="概览")

        cards = ttk.Frame(frame)
        cards.pack(fill="x")
        for index, (title, variable) in enumerate(
            (("NapCat WebUI (6099)", self.var_status_webui), ("OneBot (3001)", self.var_status_ws), ("机器人", self.var_status_bot))
        ):
            card = ttk.LabelFrame(cards, text=title, padding=(16, 10))
            card.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 10, 0))
            cards.columnconfigure(index, weight=1)
            ttk.Label(card, textvariable=variable, font=("SF Pro Text", 14, "bold")).pack(anchor="w")

        buttons = ttk.Frame(frame, padding=(0, 16))
        buttons.pack(fill="x")
        self.btn_start = ttk.Button(buttons, text="▶ 启动机器人", command=self.start_bot)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(buttons, text="■ 停止", command=self.stop_bot, state="disabled")
        self.btn_stop.pack(side="left", padx=8)
        ttk.Button(buttons, text="刷新状态", command=self.refresh_status).pack(side="left", padx=8)
        ttk.Button(buttons, text="打开 NapCat WebUI", command=self.open_webui).pack(side="left", padx=8)
        ttk.Button(buttons, text="NapCat 安装器", command=self.open_installer).pack(side="left", padx=8)

        self._build_usage(frame)

        tips = ttk.LabelFrame(frame, text="使用提示", padding=14)
        tips.pack(fill="both", expand=True)
        text = (
            "1. 先用 NapCat 登录机器人 QQ（奥米），并在 WebUI 里开好 3001 端口的 WebSocket 服务器。\n"
            "2. 在「配置」里填 DeepSeek Key、主人 QQ、Token，保存。\n"
            "3. 点「启动机器人」，然后用主人 QQ 私聊机器人。\n\n"
            "电脑感知需要给 Terminal（或本 App）开启「辅助功能」和「自动化」权限，详见「帮助」。\n"
            "NapCat 和 QQ 不随本 App 分发，需要单独安装。"
        )
        ttk.Label(tips, text=text, justify="left", wraplength=900).pack(anchor="w")

    # ------------------------------------------------------------------ 用量
    def _build_usage(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text="Token 消耗", padding=(14, 10))
        box.pack(fill="x", pady=(0, 14))
        top = ttk.Frame(box)
        top.pack(fill="x")
        ttk.Label(top, text="统计周期：").pack(side="left")
        for value, text in (("day", "日"), ("week", "周"), ("month", "月")):
            ttk.Radiobutton(
                top,
                text=text,
                value=value,
                variable=self.var_usage_period,
                command=self.refresh_usage,
            ).pack(side="left", padx=(0, 8))
        ttk.Button(top, text="刷新", command=self.refresh_usage).pack(side="right")

        ttk.Label(box, textvariable=self.var_usage_total, font=("SF Pro Display", 20, "bold")).pack(
            anchor="w", pady=(6, 0)
        )
        ttk.Label(box, textvariable=self.var_usage_detail, foreground="#777").pack(anchor="w")

        self.usage_canvas = tk.Canvas(box, height=110, highlightthickness=0)
        self.usage_canvas.pack(fill="x", pady=(8, 0))
        self.usage_canvas.bind("<Configure>", lambda _event: self._draw_usage())

    def refresh_usage(self) -> None:
        period = self.var_usage_period.get() or "day"
        count = PERIOD_BUCKETS.get(period, 7)
        now = datetime.now()
        chart_start = bucket_starts(period, now, count)[0]
        try:
            config = Config.from_env(require_llm=False)
            memory = Memory(config.db_path).connect()
        except Exception as exc:
            log.debug("读取 token 用量失败：%s", exc)
            self.var_usage_total.set("—")
            self.var_usage_detail.set("读取用量失败")
            self._usage_buckets = []
            self._draw_usage()
            return
        try:
            rows = memory.usage_rows(chart_start.timestamp())
        finally:
            memory.close()

        totals, buckets = aggregate(rows, period, count, now)
        label = {"day": "今日", "week": "本周", "month": "本月"}.get(period, "今日")
        self.var_usage_total.set(f"{label} {human_tokens(totals.total)} tokens")
        detail = (
            f"输入 {human_tokens(totals.prompt)} · 输出 {human_tokens(totals.completion)}"
            f" · 调用 {totals.calls} 次"
        )
        if totals.estimated_calls:
            detail += f"（其中 {totals.estimated_calls} 次为估算）"
        self.var_usage_detail.set(detail)
        self._usage_buckets = buckets
        self._draw_usage()

    def _draw_usage(self) -> None:
        canvas = getattr(self, "usage_canvas", None)
        if canvas is None:
            return
        canvas.delete("all")
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width <= 1 or height <= 1:
            return
        background, foreground = self._theme_colors()
        canvas.configure(background=background)
        buckets = self._usage_buckets
        if not buckets or not any(bucket.total for bucket in buckets):
            canvas.create_text(
                width / 2,
                height / 2,
                text="还没有 token 记录",
                fill=foreground,
                font=("SF Pro Text", 11),
            )
            return

        left, right, top, bottom = 6, 6, 18, 22
        chart_w = max(width - left - right, 1)
        chart_h = max(height - top - bottom, 1)
        peak = max(bucket.total for bucket in buckets) or 1
        slot = chart_w / len(buckets)
        bar_w = min(max(slot * 0.5, 6), 42)
        for index, bucket in enumerate(buckets):
            center = left + slot * index + slot / 2
            bar_h = chart_h * (bucket.total / peak)
            if bucket.total:
                canvas.create_rectangle(
                    center - bar_w / 2,
                    top + chart_h - bar_h,
                    center + bar_w / 2,
                    top + chart_h,
                    fill="#3b82f6",
                    outline="",
                )
                canvas.create_text(
                    center,
                    top + chart_h - bar_h - 7,
                    text=human_tokens(bucket.total),
                    fill=foreground,
                    font=("SF Pro Text", 8),
                )
            canvas.create_text(
                center,
                top + chart_h + 8,
                text=bucket.label,
                fill=foreground,
                font=("SF Pro Text", 8),
            )
        canvas.create_text(
            left,
            4,
            text=f"峰值 {human_tokens(peak)}",
            anchor="w",
            fill=foreground,
            font=("SF Pro Text", 8),
        )

    def _theme_colors(self) -> tuple[str, str]:
        style = ttk.Style(self)
        background = style.lookup("TFrame", "background") or "#ffffff"
        try:
            red, green, blue = self.winfo_rgb(background)
        except tk.TclError:
            return "#ffffff", "#444444"
        luminance = (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 65535
        return background, ("#e5e5e5" if luminance < 0.5 else "#444444")

    def _usage_tick(self) -> None:
        self.refresh_usage()
        self.after(30_000, self._usage_tick)

    def _build_config(self, notebook: ttk.Notebook) -> None:
        frame = ttk.Frame(notebook, padding=18)
        notebook.add(frame, text="配置")
        form = ttk.Frame(frame)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)

        rows = [
            ("DeepSeek / OpenAI Key", self.var_api_key, "key"),
            ("主人 QQ", self.var_owner, "plain"),
            ("模型", self.var_model, "plain"),
            ("Base URL", self.var_base_url, "plain"),
            ("OneBot WS 地址", self.var_ws_url, "plain"),
            ("OneBot Token", self.var_token, "token"),
            ("B 站 Cookie（含 SESSDATA）", self.var_cookie, "token"),
        ]
        for index, (label, variable, kind) in enumerate(rows):
            ttk.Label(form, text=label).grid(row=index, column=0, sticky="w", pady=6, padx=(0, 12))
            if kind == "key":
                entry = ttk.Entry(form, textvariable=variable, show="•")
                entry.grid(row=index, column=1, sticky="ew", pady=6)
                ttk.Checkbutton(
                    form,
                    text="显示",
                    variable=self.var_show_key,
                    command=lambda e=entry: e.configure(show="" if self.var_show_key.get() else "•"),
                ).grid(row=index, column=2, sticky="w", padx=8)
            else:
                ttk.Entry(form, textvariable=variable, show="•" if kind == "token" else "").grid(
                    row=index, column=1, sticky="ew", pady=6
                )

        checks = ttk.Frame(frame, padding=(0, 12))
        checks.pack(fill="x")
        ttk.Checkbutton(checks, text="主动聊天", variable=self.var_proactive).pack(side="left", padx=(0, 18))
        ttk.Checkbutton(checks, text="电脑感知与操作", variable=self.var_computer).pack(side="left", padx=(0, 18))
        ttk.Checkbutton(checks, text="记录活动", variable=self.var_activity).pack(side="left", padx=(0, 18))
        ttk.Checkbutton(checks, text="B 站学习", variable=self.var_learning).pack(side="left", padx=(0, 18))
        ttk.Checkbutton(checks, text="DSH 本地学习运行时", variable=self.var_dsh).pack(side="left", padx=(0, 18))
        ttk.Checkbutton(checks, text="DSH 作为聊天大脑", variable=self.var_dsh_chat).pack(side="left", padx=(0, 18))
        ttk.Checkbutton(checks, text="允许读屏（OCR）", variable=self.var_screen).pack(side="left")

        actions = ttk.Frame(frame, padding=(0, 10))
        actions.pack(fill="x")
        ttk.Button(actions, text="保存配置", command=self.save_config).pack(side="left")
        ttk.Button(actions, text="测试模型 + B 站", command=self.run_check).pack(side="left", padx=8)

        ttk.Label(
            frame,
            text=f"配置文件：{APP_HOME / '.env'}\n人格卡：{APP_HOME / 'personas' / 'dafeiyu.md'}",
            foreground="#777",
            justify="left",
        ).pack(anchor="w", pady=(14, 0))

    def _build_persona(self, notebook: ttk.Notebook) -> None:
        frame = ttk.Frame(notebook, padding=14)
        notebook.add(frame, text="人格")
        top = ttk.Frame(frame)
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, textvariable=self.var_persona_stats, foreground="#666").pack(side="left")
        ttk.Button(top, text="保存", command=self.save_persona).pack(side="right")
        ttk.Button(top, text="重新载入", command=self._load_persona).pack(side="right", padx=6)
        ttk.Button(top, text="查看提示词", command=self.preview_persona).pack(side="right", padx=6)
        ttk.Button(top, text="统计", command=self.update_persona_stats).pack(side="right", padx=6)

        self.persona_editor = scrolledtext.ScrolledText(
            frame, wrap="word", font=("SF Mono", 12), undo=True, padx=12, pady=10
        )
        self.persona_editor.pack(fill="both", expand=True)

        test_row = ttk.Frame(frame, padding=(0, 8))
        test_row.pack(fill="x")
        ttk.Label(test_row, text="试聊：").pack(side="left")
        ttk.Entry(test_row, textvariable=self.var_test_message).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(test_row, text="发送", command=self.run_persona_test).pack(side="left")

    def _build_skills(self, notebook: ttk.Notebook) -> None:
        frame = ttk.Frame(notebook, padding=14)
        notebook.add(frame, text="技能")
        top = ttk.Frame(frame)
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, text="奥米从主人 B 站观看历史里学到的技能卡", foreground="#666").pack(side="left")
        ttk.Button(top, text="立即学习", command=self.run_learn_now).pack(side="right")
        ttk.Button(top, text="打开目录", command=self.open_skills_dir).pack(side="right", padx=6)
        ttk.Button(top, text="刷新", command=self.refresh_skills).pack(side="right", padx=6)
        ttk.Button(top, text="重新蒸馏", command=self.relearn_skills_clicked).pack(
            side="right", padx=6
        )

        source = ttk.Frame(frame)
        source.pack(fill="x", pady=(0, 8))
        ttk.Label(source, text="手动学习：").pack(side="left")
        self.skill_url_entry = ttk.Entry(source, textvariable=self.var_skill_url, width=38)
        self.skill_url_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Button(source, text="学习链接", command=self.learn_url_clicked).pack(side="left", padx=(0, 14))
        ttk.Label(source, text="关键词：").pack(side="left")
        self.skill_keywords_entry = ttk.Entry(source, textvariable=self.var_skill_keywords, width=20)
        self.skill_keywords_entry.pack(side="left", padx=(0, 6))
        ttk.Button(source, text="搜索学习", command=self.learn_keywords_clicked).pack(side="left")

        body = ttk.PanedWindow(frame, orient="horizontal")
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body)
        right = ttk.Frame(body)
        body.add(left, weight=1)
        body.add(right, weight=2)
        self.skills_list = tk.Listbox(left, exportselection=False, font=("SF Pro Text", 12))
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.skills_list.yview)
        self.skills_list.configure(yscrollcommand=scroll.set)
        self.skills_list.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.skills_list.bind("<<ListboxSelect>>", self.show_selected_skill)
        self.skills_text = scrolledtext.ScrolledText(
            right, wrap="word", font=("SF Mono", 12), padx=10, pady=8
        )
        self.skills_text.pack(fill="both", expand=True)
        self._skill_rows: list = []
        self.refresh_skills()

    def _skill_store(self) -> SkillStore:
        try:
            config = Config.from_env(require_llm=False)
            return SkillStore(config.skills_dir)
        except ConfigError:
            return SkillStore(APP_HOME / "skills")

    def refresh_skills(self) -> None:
        store = self._skill_store()
        self._skill_rows = store.list()
        self.skills_list.delete(0, tk.END)
        for skill in self._skill_rows:
            self.skills_list.insert(tk.END, skill.summary())
        if self._skill_rows:
            self.skills_list.selection_set(0)
            self.show_selected_skill()
        else:
            self.skills_text.delete("1.0", tk.END)
            self.skills_text.insert(
                "1.0",
                "还没有技能。可以：\n"
                "1. 在配置里填 B 站 Cookie，点「立即学习」读观看历史；\n"
                "2. 在上方粘贴 B 站视频链接，点「学习链接」；\n"
                "3. 输入关键词，点「搜索学习」。",
            )
        log.info("技能库：%d 个", len(self._skill_rows))

    def show_selected_skill(self, _event: object | None = None) -> None:
        selection = self.skills_list.curselection()
        if not selection:
            return
        index = selection[0]
        if index >= len(self._skill_rows):
            return
        skill = self._skill_rows[index]
        self.skills_text.delete("1.0", tk.END)
        self.skills_text.insert(
            "1.0",
            f"{skill.name}\n{skill.description}\n来源：{skill.source or '本地'}\n\n{skill.content}",
        )

    def open_skills_dir(self) -> None:
        store = self._skill_store()
        subprocess.Popen(["open", str(store.directory)])

    def run_learn_now(self) -> None:
        if not self.save_config(silent=True):
            return
        try:
            config = Config.from_env(require_llm=False)
        except ConfigError as exc:
            messagebox.showerror("配置错误", str(exc))
            return
        if not config.llm_api_key and not config.dsh_enabled:
            messagebox.showerror("缺少 Key", "要么填 LLM_API_KEY，要么勾选 DSH 本地学习运行时")
            return
        keywords = self.var_skill_keywords.get().strip()
        if not config.bili_cookie:
            if keywords:
                log.info("没有 Cookie，改为按关键词搜索学习：%s", keywords)
                self._start_learning(config, lambda cfg: learn_keywords_now(cfg, keywords), "关键词学习")
                return
            messagebox.showinfo(
                "没有 B 站 Cookie",
                "读取观看历史需要 B 站 Cookie（至少含 SESSDATA）。\n\n"
                "也可以直接：\n"
                "• 在「手动学习」粘贴 B 站视频链接，点「学习链接」\n"
                "• 输入关键词，点「搜索学习」",
            )
            self.skill_url_entry.focus_set()
            return
        log.info("开始从 B 站历史学习…")
        self._start_learning(config, learn_now, "历史学习")

    def learn_url_clicked(self) -> None:
        url = self.var_skill_url.get().strip()
        if not url:
            self.skill_url_entry.focus_set()
            messagebox.showinfo("学习链接", "先粘贴一个 B 站视频链接")
            return
        if "bilibili.com" not in url and not url.startswith("BV"):
            messagebox.showerror("链接不对", "请粘贴 B 站视频链接，例如 https://www.bilibili.com/video/BV...")
            return
        if not self.save_config(silent=True):
            return
        config = self._learning_config()
        if config is None:
            return
        self._start_learning(config, lambda cfg: learn_url_now(cfg, url), "链接学习")

    def learn_keywords_clicked(self) -> None:
        keywords = self.var_skill_keywords.get().strip()
        if not keywords:
            self.skill_keywords_entry.focus_set()
            messagebox.showinfo("搜索学习", "先输入关键词，比如：雅思、Python")
            return
        if not self.save_config(silent=True):
            return
        config = self._learning_config()
        if config is None:
            return
        self._start_learning(config, lambda cfg: learn_keywords_now(cfg, keywords), "关键词学习")

    def relearn_skills_clicked(self) -> None:
        config = self._learning_config()
        if config is None:
            return
        self._start_learning(config, relearn_skills_now, "重新蒸馏")

    def _learning_config(self) -> Config | None:
        try:
            config = Config.from_env(require_llm=False)
        except ConfigError as exc:
            messagebox.showerror("配置错误", str(exc))
            return None
        if not config.llm_api_key and not config.dsh_enabled:
            messagebox.showerror("缺少 Key", "要么填 LLM_API_KEY，要么勾选 DSH 本地学习运行时")
            return None
        return config

    def _start_learning(self, config: Config, action, label: str) -> None:
        log.info("开始%s…", label)
        threading.Thread(target=self._learn_worker, args=(config, action), daemon=True).start()

    def _learn_worker(self, config: Config, action) -> None:
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                ok = asyncio.run(action(config))
            if ok:
                self.after(0, self.refresh_skills)
        except Exception:
            log.exception("B 站学习失败")
        finally:
            text = buf.getvalue().strip()
            if text:
                log.info(text)

    def _build_logs(self, notebook: ttk.Notebook) -> None:
        frame = ttk.Frame(notebook, padding=10)
        notebook.add(frame, text="日志")
        self.log_text = scrolledtext.ScrolledText(frame, wrap="word", font=("SF Mono", 11), state="disabled")
        self.log_text.pack(fill="both", expand=True)
        ttk.Button(frame, text="清空日志", command=self.clear_logs).pack(anchor="e", pady=(8, 0))

    def _build_help(self, notebook: ttk.Notebook) -> None:
        frame = ttk.Frame(notebook, padding=18)
        notebook.add(frame, text="帮助")
        text = (
            "首次使用顺序\n"
            "1. 安装 NapCat（macOS 安装器），扫码登录机器人 QQ。\n"
            "2. NapCat WebUI → 网络配置 → 新建 WebSocket 服务器：127.0.0.1:3001，消息格式 array。\n"
            "3. 在「配置」里填 Key、主人 QQ、Token，保存。\n"
            "4. 启动机器人；用主人 QQ 私聊机器人。\n\n"
            "权限\n"
            "• 电脑感知需要「辅助功能」：系统设置 → 隐私与安全性 → 辅助功能 → 打开 Terminal / 大肥鱼。\n"
            "• 控制音乐、读取窗口可能需要「自动化」授权，首次会弹窗。\n"
            "• 电脑感知只读前台 App、窗口标题、空闲时间；不截屏、不记录键盘、不读剪贴板。\n\n"
            "安全\n"
            "• 电脑操作只允许主人在私聊里使用；群聊和陌生人会被拒绝。\n"
            "• 不支持任意 shell、删除文件、给别人发消息。\n"
            "• 可在「配置」关闭电脑感知或电脑操作。\n\n"
            "NapCat 和 QQ 是第三方软件，不随本 App 分发。"
        )
        ttk.Label(frame, text=text, justify="left", wraplength=900).pack(anchor="w")
        actions = ttk.Frame(frame, padding=(0, 16))
        actions.pack(fill="x")
        ttk.Button(actions, text="打开辅助功能设置", command=self.open_accessibility).pack(side="left")
        ttk.Button(actions, text="打开 NapCat WebUI", command=self.open_webui).pack(side="left", padx=8)
        ttk.Button(actions, text="NapCat 安装器", command=self.open_installer).pack(side="left", padx=8)

    # ------------------------------------------------------------------ 配置
    def _load_config(self) -> None:
        try:
            config = Config.from_env(require_llm=False)
        except ConfigError as exc:
            messagebox.showerror("配置有问题", str(exc))
            return
        self.var_api_key.set(config.llm_api_key)
        self.var_owner.set(str(config.owner_qq or ""))
        self.var_model.set(config.llm_model)
        self.var_base_url.set(config.llm_base_url)
        self.var_ws_url.set(config.onebot_ws_url)
        self.var_token.set(config.onebot_token)
        self.var_proactive.set(config.proactive_enabled)
        self.var_computer.set(config.computer_enabled)
        self.var_activity.set(config.activity_enabled)
        self.var_learning.set(config.learning_enabled)
        self.var_dsh.set(config.dsh_enabled)
        self.var_dsh_chat.set(config.dsh_chat)
        self.var_screen.set(config.screen_read_enabled)
        self.var_skill_keywords.set(", ".join(config.learning_keywords))
        self.var_cookie.set(config.bili_cookie)
        self._persona_path = Path(config.persona_file)
        log.info("配置已载入：%s", APP_HOME / ".env")

    def save_config(self, *, silent: bool = False) -> bool:
        owner = self.var_owner.get().strip()
        if owner and not owner.isdigit():
            messagebox.showerror("配置错误", "主人 QQ 必须是数字")
            return False
        updates = {
            "LLM_API_KEY": self.var_api_key.get().strip(),
            "LLM_MODEL": self.var_model.get().strip(),
            "LLM_BASE_URL": self.var_base_url.get().strip(),
            "OWNER_QQ": owner,
            "ONEBOT_WS_URL": self.var_ws_url.get().strip(),
            "ONEBOT_ACCESS_TOKEN": self.var_token.get().strip(),
            "PROACTIVE_ENABLED": "true" if self.var_proactive.get() else "false",
            "COMPUTER_ENABLED": "true" if self.var_computer.get() else "false",
            "ACTIVITY_ENABLED": "true" if self.var_activity.get() else "false",
            "LEARNING_ENABLED": "true" if self.var_learning.get() else "false",
            "DSH_ENABLED": "true" if self.var_dsh.get() else "false",
            "DSH_CHAT": "true" if self.var_dsh_chat.get() else "false",
            "SCREEN_READ_ENABLED": "true" if self.var_screen.get() else "false",
            "LEARNING_KEYWORDS": self.var_skill_keywords.get().strip(),
            "BILI_COOKIE": self.var_cookie.get().strip(),
        }
        set_env_values(APP_HOME / ".env", updates)
        log.info("配置已保存")
        if not silent:
            messagebox.showinfo("已保存", "配置已写入，重启机器人后生效。")
        return True

    def run_check(self) -> None:
        if not self.save_config(silent=True):
            return
        try:
            config = Config.from_env()
        except ConfigError as exc:
            messagebox.showerror("配置错误", str(exc))
            return

        def worker() -> None:
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                ok = asyncio.run(check_services(config))
            self.log_queue.put(buffer.getvalue().strip())
            self.log_queue.put("✅ 自检通过" if ok else "❌ 自检失败")

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------ 人格
    def _load_persona(self) -> None:
        config = Config.from_env(require_llm=False)
        path = Path(config.persona_file)
        self._persona_path = path
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            source = BUNDLE_ROOT / "personas" / "dafeiyu.md"
            path.write_text(source.read_text(encoding="utf-8") if source.exists() else "", encoding="utf-8")
        self.persona_editor.delete("1.0", "end")
        self.persona_editor.insert("1.0", path.read_text(encoding="utf-8"))
        self.update_persona_stats()

    def save_persona(self) -> None:
        if self._persona_path is None:
            return
        self._persona_path.parent.mkdir(parents=True, exist_ok=True)
        self._persona_path.write_text(self.persona_editor.get("1.0", "end-1c"), encoding="utf-8")
        self.update_persona_stats()
        log.info("人格卡已保存：%s（下一条消息生效）", self._persona_path)

    def update_persona_stats(self) -> None:
        try:
            config = Config.from_env(require_llm=False)
            card = PersonaCard(
                config.persona_file,
                name=config.persona_name,
                owner_qq=config.owner_qq,
                max_chars=config.persona_max_chars,
            )
            text = card.load()
            self.var_persona_stats.set(f"{len(text)} 字符 / 上限 {card.max_chars} · {card.path.name}")
        except Exception as exc:
            self.var_persona_stats.set(f"读取失败：{exc}")

    def preview_persona(self) -> None:
        config = Config.from_env(require_llm=False)
        try:
            now = datetime.now(ZoneInfo(config.timezone))
        except (ZoneInfoNotFoundError, ValueError):
            now = datetime.now()
        card = PersonaCard(
            config.persona_file,
            name=config.persona_name,
            owner_qq=config.owner_qq,
            max_chars=config.persona_max_chars,
        )
        prompt = build_system_prompt(
            name=config.persona_name,
            now=now,
            extra=config.persona_extra,
            persona_text=card.load(),
        )
        window = tk.Toplevel(self)
        window.title("完整系统提示词")
        window.geometry("900x700")
        text = scrolledtext.ScrolledText(window, wrap="word", font=("SF Mono", 11))
        text.pack(fill="both", expand=True)
        text.insert("1.0", prompt)
        text.configure(state="disabled")

    def run_persona_test(self) -> None:
        if not self.save_config(silent=True):
            return
        message = self.var_test_message.get().strip()
        if not message:
            return
        try:
            config = Config.from_env()
        except ConfigError as exc:
            messagebox.showerror("配置错误", str(exc))
            return

        def worker() -> None:
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                asyncio.run(persona_test(config, message))
            self.log_queue.put(buffer.getvalue().strip())

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------ 机器人
    def start_bot(self) -> None:
        if self._bot_thread and self._bot_thread.is_alive():
            return
        if not self.save_config(silent=True):
            return
        try:
            config = Config.from_env(require_llm=not self.var_dsh_chat.get())
        except ConfigError as exc:
            messagebox.showerror("还不能启动", str(exc))
            return
        self._bot_thread = threading.Thread(target=self._bot_worker, args=(config,), daemon=True)
        self._bot_thread.start()
        self.var_status_bot.set("启动中…")
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")

    def _bot_worker(self, config: Config) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        stop = asyncio.Event()
        self._bot_loop = loop
        self._stop_event = stop
        try:
            loop.run_until_complete(run_app(config, stop_event=stop))
        except Exception:
            log.exception("机器人异常退出")
            self.log_queue.put("❌ 机器人异常退出，查看日志")
        finally:
            self._bot_loop = None
            self._stop_event = None
            loop.close()
            self.log_queue.put("机器人已停止")
            self.after(0, self._mark_stopped)

    def _mark_stopped(self) -> None:
        self.var_status_bot.set("已停止")
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")

    def stop_bot(self) -> None:
        loop, stop = self._bot_loop, self._stop_event
        if loop is not None and stop is not None:
            loop.call_soon_threadsafe(stop.set)
            self.var_status_bot.set("停止中…")
            log.info("正在停止机器人…")

    def refresh_status(self) -> None:
        webui = port_open(6099)
        ws = port_open(3001)
        self.var_status_webui.set("✅ 在线" if webui else "— 未启动")
        self.var_status_ws.set("✅ 在线" if ws else "— 未启动")
        if self._bot_thread and self._bot_thread.is_alive():
            self.var_status_bot.set("✅ 运行中")
        else:
            self.var_status_bot.set("已停止")
        self.after(2000, self.refresh_status)

    # ------------------------------------------------------------------ 动作
    def open_webui(self) -> None:
        webbrowser.open("http://127.0.0.1:6099/webui")

    def open_installer(self) -> None:
        for path in INSTALLER_CANDIDATES:
            if path.exists():
                subprocess.Popen(["open", str(path)])
                return
        webbrowser.open("https://github.com/NapNeko/NapCat-Mac-Installer/releases/latest")

    def open_accessibility(self) -> None:
        subprocess.Popen(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"])

    # ------------------------------------------------------------------ 日志
    def _drain_logs(self) -> None:
        lines: list[str] = []
        while True:
            try:
                lines.append(self.log_queue.get_nowait())
            except queue.Empty:
                break
        if lines:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", "\n".join(lines) + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        self.after(200, self._drain_logs)

    def clear_logs(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def on_close(self) -> None:
        if self._bot_thread and self._bot_thread.is_alive():
            if not messagebox.askyesno("退出", "机器人还在运行，确定退出吗？"):
                return
            self.stop_bot()
            self.after(1200, self.destroy)
        else:
            self.destroy()


def main() -> int:
    ensure_app_home()
    app = DafeiyuApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
