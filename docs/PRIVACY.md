# Privacy

Dafeiyu is local-first. This page describes exactly what leaves the machine.

## Stored locally

- QQ chat messages handled by the bot (`messages`)
- Explicit “记住 …” facts (`facts`)
- Bilibili videos seen/shared (`videos`)
- Frontmost app name, window title, idle seconds (`activity`, default 7-day retention)
- macOS action audit entries (`computer_actions`)
- Bilibili watch-history learning state (`learned_videos`)
- Generated Markdown skill cards (`skills/*.md`)
- OCR text is **not** stored; the temporary screenshot is deleted immediately after recognition
- Counts of QQ face IDs used by the owner (no message content) for choosing familiar expressions

Location:

- CLI: `./data/dafeiyu.db` (or `DATABASE_PATH`)
- macOS app: `~/Library/Application Support/Dafeiyu/data/dafeiyu.db`

## Sent to the model provider

To generate a reply, the following is sent to the provider configured by `LLM_BASE_URL`:

- the persona card and dynamic context (time, facts, recent videos)
- recent chat history for the current session
- in **private owner chats**, the last few activity entries
- tool results (Bilibili metadata/comments, activity query results, skill contents, and screen OCR text only when explicitly requested)
- in **private owner chats**, matched skill summaries and full skill content when the model reads one

If `DSH_ENABLED=true` or `DSH_CHAT=true`, prompts are sent to the local DeepSeek Harness process; DSH may call its configured model provider. In chat mode, DSH native file/terminal/web tools are disabled and only the Python allowlisted tools run.

The provider's own retention policy applies. Use a local OpenAI-compatible server if you do not want data to leave the machine.

## Sent to Bilibili

Only requests needed for the requested feature: hot list, ranking, search, video details, tags, comments, and (when learning is enabled) your authenticated watch history. If `BILI_COOKIE` is set, it is sent to Bilibili. Subtitles are fetched only when a cookie is configured.

## Sent to QQ/NapCat

Messages and actions are sent to the local NapCat WebSocket server (`127.0.0.1:3001` by default). NapCat communicates with Tencent's QQ servers under its own terms.

## Not collected

- Screenshots or screen recording
- Keystrokes or clipboard contents
- Passwords, Keychain entries, or files
- Microphone or camera

## Controls

```dotenv
ACTIVITY_ENABLED=false     # stop activity sampling
COMPUTER_ENABLED=false     # disable macOS actions
ACTIVITY_BLOCKLIST=1Password,Keychain Access,Bitwarden,Passwords
LEARNING_ENABLED=false      # stop watch-history learning and skill generation
DSH_ENABLED=false           # keep skill summarization on the configured API instead
```

Delete `skills/` while the bot is stopped to erase generated skills.

Delete `data/dafeiyu.db` while the bot is stopped to erase local history.
