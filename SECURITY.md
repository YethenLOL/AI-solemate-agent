# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| 1.x | ✅ |

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Use GitHub's private vulnerability reporting:

1. Open the repository's **Security** tab.
2. Click **Report a vulnerability**.
3. Include reproduction steps, affected version/commit, and impact.

If private reporting is unavailable, contact the maintainer listed in the repository profile. We aim to acknowledge reports within 72 hours.

## Threat model

This project runs locally and controls a QQ account through NapCat, calls a model API, and (optionally) reads the active window and performs a small set of macOS actions.

Security-relevant boundaries:

- **Owner-only computer actions.** `computer` and `recent_activity` tools are rejected unless the session is `private:<OWNER_QQ>`.
- **No arbitrary shell.** The app never executes model-provided shell strings. Actions are fixed operations with validated arguments.
- **Activity data stays local.** Only the frontmost app name, window title, and idle seconds are stored, with blocklists and a retention limit.
- **Prompt injection.** Bilibili titles/comments and web content are treated as data, not instructions. The persona and system prompt instruct the model accordingly, but no LLM is immune to prompt injection. Do not grant the bot more privileges than you are comfortable with.
- **Secrets.** `LLM_API_KEY`, cookies, and OneBot tokens live in `.env` / the user's Application Support directory and must never be committed. `.env` and `data/` are gitignored.

## Known platform risks

- NapCat uses a non-official QQ protocol. Account restriction or suspension is possible. Use a spare account.
- The app is ad-hoc signed by default. Public distribution should use Developer ID signing and notarization.
- Third-party model providers receive the chat context required to generate replies; review their data policies.
