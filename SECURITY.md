# Security Policy

## Supported Versions

Only the latest release is supported with security updates.

| Version | Supported |
|---------|-----------|
| Latest  | Yes       |
| Older   | No        |

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly:

1. **Do not** open a public issue
2. Use [GitHub's private vulnerability reporting](https://github.com/klopfer-hello/ForgasGuildCalender-Sync/security/advisories/new)
3. Include a description of the vulnerability, steps to reproduce, and any potential impact

You can expect an initial response within 7 days.

## Scope

This project handles the following sensitive data:

- **Google OAuth tokens** (`token.json`) — stored locally in `%APPDATA%` / `~/.config`
- **Discord bot token** — stored in `config.json`
- **Google OAuth client secrets** (`client_secrets.json`) — stored locally, gitignored

These files are never transmitted except to their respective APIs (Google, Discord). The application does not collect telemetry or send data to any third-party service beyond Google Calendar and Discord.

## Auto-Update

The auto-update feature downloads executables exclusively from the [official GitHub releases](https://github.com/klopfer-hello/ForgasGuildCalender-Sync/releases) of this repository via the GitHub API. Downloaded file sizes are verified against the Content-Length header.
