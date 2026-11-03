# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/) and the project uses semantic versioning.

## [Unreleased]

### Added

- Night routine inspired by dsh-bili-whale: random wake windows with a skip chance, preference + danmaku-density ranking, deep reads (subtitles/comments/danmaku hotspots), score-gated sharing (≤3/day, same BV once per 7 days, silence decay), two diaries, and a traffic guard with read/write budgets plus escalating 412 pauses.
- GUI diary tab and night schedule status.
- Frame reading: ffmpeg grabs frames at danmaku hotspots and macOS Vision OCRs on-screen text into the deep-read material (`FRAMES_ENABLED`); if a remote seek fails it retries with output-side seeking and HTTP reconnect.
- Skill distillation now produces actionable cards: structured JSON (tools, websites, prerequisites, steps with commands, verification, pitfalls, references) rendered to Markdown, with tool names and links stored in frontmatter for search.
- Token usage panel on the overview tab: per-call recording (exact provider usage, or a local estimate for DSH runs) with day / week / month totals and a bar chart.
- Learning progress is now logged per video (`学习 i/n`, skip reason, `生成技能`), so the GUI log tab and `tail -f` show what 奥米 is doing in real time.

### Fixed

- Bilibili HTTP 412 handling now resets the buvid fingerprint, rotates the User-Agent, and backs off before giving up; search additionally falls back to the unsigned endpoint. `BiliRiskControl` lets callers distinguish risk control from other API errors.

## [1.1.0] - 2026-11-03

### Added

- Bilibili watch-history learning (`BILI_COOKIE` + `SESSDATA`) that turns watched knowledge videos into local Markdown skill cards.
- Local skill store with search, prompt injection, and owner-only `skill_search` / `skill_read` tools.
- Optional DeepSeek Harness (headless) runtime for both skill summarization and QQ chat tool calls, with `--check` coverage, automatic Node/entry discovery, and a patch that disables DSH native tools.
- GUI Skills tab and "立即学习" action; Bilibili Cookie and learning/DSH toggles in config.
- Persistent rotating log file at `APP_HOME/logs/dafeiyu.log`.
- CLI commands `--learn-now`, `--skills`, `--skill-search`, `--bili-history`.
- QQ face stickers: a curated mood catalog, owner face-usage learning, `[[face:id]]` sending, and a `sticker_search` tool.
- Manual Bilibili learning: paste a video URL or search keywords without watch-history cookie; CLI `--learn-url` / `--learn-search`.
- On-demand screen reading: temporary screenshot + local macOS Vision OCR, owner-only and privacy-filtered.
- New tests for history parsing, skill storage/search, learning loop, privacy guards, screen OCR, and DSH adapter (116 total).

## [1.0.0] - 2026-11-03

### Added

- QQ integration through NapCat / OneBot v11: private chat, allowlisted groups, wake words, auto-reconnect.
- Bilibili integration: hot list, ranking, search (WBI signing), video details, tags, comments, optional subtitles.
- Autonomous browsing loop and proactive messaging with quiet hours, daily limits, and randomized intervals.
- SQLite memory: chat history, long-term facts, seen videos, activity history, and computer-action audit.
- Hot-reload Markdown persona cards with `{{name}}` / `{{owner_qq}}` placeholders, character budget, preview and test commands.
- macOS awareness: frontmost app, window title, idle time; private-only context injection.
- Owner-only safe computer actions: status, open app, open URL, web search, volume, notification, Music control, lock screen.
- Tkinter macOS console (`dafeiyu-gui`) with config, persona editor, logs, and start/stop.
- PyInstaller macOS app build script and generated icon.
- 96 automated tests, including OneBot round-trip, Bilibili parsing, persona hot reload, and owner-only tool guards.
- GitHub-ready project docs, security policy, packaging guide, and CI workflow.
