# Contributing

Thanks for helping improve Dafeiyu.

## Development setup

```bash
git clone <your-fork-url>
cd dafeiyu-qq
bash scripts/setup.sh
.venv/bin/pip install -r requirements-dev.txt
```

## Run the tests

```bash
.venv/bin/python -m pytest -q
```

The suite does not need a real QQ account or API key. Computer/activity tests mock macOS calls.

## Run locally

```bash
cp .env.example .env
# fill LLM_API_KEY, OWNER_QQ, ONEBOT_ACCESS_TOKEN
.venv/bin/python -m dafeiyu --check
.venv/bin/python -m dafeiyu
```

Or build the macOS app:

```bash
bash scripts/build_app.sh
open dist/Dafeiyu.app
```

## Project layout

- `dafeiyu/agent.py` — LLM tool loop, owner-only computer tools
- `dafeiyu/persona.py` — hot-reload persona card loader
- `dafeiyu/bilibili.py` — Bilibili public endpoints, rate limiting and WBI signing
- `dafeiyu/onebot.py` — OneBot v11 WebSocket client
- `dafeiyu/computer.py` — fixed, validated macOS actions
- `dafeiyu/activity.py` — frontmost-app sampler
- `dafeiyu/gui.py` — Tkinter macOS console
- `personas/dafeiyu.md` — default persona card
- `tests/` — unit and integration tests

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the data flow.

## Pull requests

- Keep changes focused and include tests for behavior changes.
- Do not commit `.env`, `data/`, credentials, cookies, or personal activity logs.
- Do not add arbitrary shell execution or broaden computer actions without an explicit security discussion.
- Run the test suite before opening a PR.
- Use clear commit messages; a Conventional Commits style is welcome but not required.

## Persona changes

Persona-only changes are welcome. Keep the card focused on identity and style; tool mechanics belong in code. See the "人格调试" section of the README.
