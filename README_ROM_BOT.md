# ZK ROM Telegram Bot

This bot syncs ROM files from a Google Drive folder every 6 hours, stores access permissions per Telegram ID and per device, and returns ROM links with `/get <device>`.

## Features

- Syncs the Google Drive folder every `21600` seconds (6 hours).
- Admin grants access with `/add <telegram_id> <device> [device2 ...]`.
- One user can own multiple devices; each device is stored as a separate permission.
- Users get ROM links with `/get <device>`.
- Unauthorized users are told to contact `@HzzMonet`.
- Uses SQLite, so permissions survive bot restarts.
- Bot replies are in English by default.

## Supported file names

The bot detects device, region, and build date from file names like:

```text
ZKOS_NEZHA_OS3.0.307.0.WPACNXM_CN260526.zip
ZKOS_NEZHA_OS3.0.307.0.WPACNXM_EU260504.zip
```

For the examples above:

- Device: `nezha`
- Region: `CN` or `EU`
- Build date: `2026-05-26` or `2026-05-04`

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-rom-bot.txt
cp rom-bot.env.example .env
nano .env
python3 telegram_rom_bot.py
```

## Environment variables

| Variable | Required | Notes |
| --- | --- | --- |
| `BOT_TOKEN` | Yes | Token from `@BotFather` |
| `ADMIN_IDS` | Yes | Admin Telegram IDs, separated by comma or space |
| `DRIVE_FOLDER_ID` | No | Defaults to the configured ROM folder |
| `GOOGLE_API_KEY` | Recommended | Uses the official Google Drive API; more stable than the public HTML fallback |
| `CONTACT_USERNAME` | No | Defaults to `@HzzMonet` |
| `DB_PATH` | No | Defaults to `rom_bot.sqlite3` |
| `SYNC_INTERVAL_SECONDS` | No | Defaults to `21600` |
| `MAX_FILES_PER_REPLY` | No | Defaults to `10` |

`GOOGLE_API_KEY` is recommended. Without it, the bot will try a best-effort public Google Drive HTML fallback, which can break if Google changes the Drive page layout.

## User commands

```text
/id
/devices
/get nezha
/get popsicle
```

## Admin commands

```text
/add 123456789 nezha
/add 123456789 nezha popsicle pandora
/remove 123456789 nezha
/sync
/users
```

## Run with systemd

Create `/etc/systemd/system/zk-rom-bot.service`:

```ini
[Unit]
Description=ZK ROM Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=/opt/ZK_BUILDER
EnvironmentFile=/opt/ZK_BUILDER/.env
ExecStart=/opt/ZK_BUILDER/.venv/bin/python /opt/ZK_BUILDER/telegram_rom_bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Then run:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now zk-rom-bot
sudo journalctl -u zk-rom-bot -f
```

## Security notes

- Do not commit `.env` or `rom_bot.sqlite3` to GitHub.
- Only add trusted Telegram IDs to `ADMIN_IDS`.
- If a user changes or sells a device, run `/remove <telegram_id> <device>`.
