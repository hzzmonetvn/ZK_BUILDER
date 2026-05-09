#!/usr/bin/env python3
"""Telegram bot that dispatches only the ZK BUILDER FORK GitHub Actions workflow."""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
ALLOWED_CHAT = os.getenv("TELEGRAM_TO", "").strip()
GITHUB_TOKEN = (
    os.getenv("ACTIONS_PAT", "").strip()
    or os.getenv("GH_TOKEN", "").strip()
    or os.getenv("GITHUB_TOKEN", "").strip()
)
REPOSITORY = os.getenv("GH_REPOSITORY", os.getenv("GITHUB_REPOSITORY", "")).strip()
WORKFLOW_FILE = os.getenv("WORKFLOW_FILE", "ZK BUILDER FORK.yml").strip()
WORKFLOW_REF = os.getenv("WORKFLOW_REF", "main").strip()

HELP_TEXT = """🤖 <b>ZK Builder Bot</b>

Bot này chỉ chạy workflow:
<code>.github/workflows/ZK BUILDER FORK.yml</code>

Lệnh build:
<code>/build OTA_URL</code>
<code>/build OTA_URL FASTBOOT_URL</code>
<code>/build OTA_URL branch=hzz gofile=false pixeldrain=true</code>
<code>/build OTA_URL FASTBOOT_URL branch=hzz</code>

Mặc định:
• branch=hzz
• gofile=false
• pixeldrain=true
"""

URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def request_json(url: str, *, method: str = "GET", data: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
    body = None
    req_headers = headers.copy() if headers else {}
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, method=method, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            if not raw:
                return {}
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc


def tg_api(method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return request_json(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}",
        method="POST" if payload is not None else "GET",
        data=payload,
    )


def send_message(chat_id: int | str, text: str, reply_markup: dict[str, Any] | None = None) -> None:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    tg_api("sendMessage", payload)


def parse_bool(value: str, default: bool) -> bool:
    if value.lower() in {"1", "true", "yes", "y", "on"}:
        return True
    if value.lower() in {"0", "false", "no", "n", "off"}:
        return False
    return default


def parse_build_command(text: str) -> dict[str, str]:
    parts = text.split()
    if len(parts) < 2:
        raise ValueError("Thiếu OTA_URL. Gõ /help để xem ví dụ.")

    ota_url = ""
    fastboot_url = ""
    branch = "hzz"
    gofile = False
    pixeldrain = True

    positional: list[str] = []
    for item in parts[1:]:
        if "=" in item:
            key, value = item.split("=", 1)
            key = key.strip().lower().lstrip("-")
            value = value.strip()
            if key in {"branch", "br"}:
                branch = value or branch
            elif key in {"gofile", "upload_gofile"}:
                gofile = parse_bool(value, gofile)
            elif key in {"pixeldrain", "upload_pixeldrain", "pd"}:
                pixeldrain = parse_bool(value, pixeldrain)
            elif key in {"url2", "fastboot"}:
                fastboot_url = value
            else:
                raise ValueError(f"Không hiểu option: {key}")
        else:
            positional.append(item)

    if positional:
        ota_url = positional[0]
    if len(positional) >= 2:
        if URL_RE.match(positional[1]):
            fastboot_url = positional[1]
        else:
            branch = positional[1]
    if len(positional) >= 3:
        branch = positional[2]

    if not URL_RE.match(ota_url):
        raise ValueError("OTA_URL phải bắt đầu bằng http:// hoặc https://")
    if fastboot_url and not URL_RE.match(fastboot_url):
        raise ValueError("FASTBOOT_URL phải bắt đầu bằng http:// hoặc https://")
    if not branch:
        raise ValueError("BRANCH không được để trống")

    return {
        "URL": ota_url,
        "URL2": fastboot_url,
        "BRANCH": branch,
        "UPLOAD_GOFILE": "true" if gofile else "false",
        "UPLOAD_PIXELDRAIN": "true" if pixeldrain else "false",
    }


def dispatch_workflow(inputs: dict[str, str]) -> str:
    if not REPOSITORY:
        raise RuntimeError("Thiếu GH_REPOSITORY/GITHUB_REPOSITORY")
    if not GITHUB_TOKEN:
        raise RuntimeError("Thiếu ACTIONS_PAT hoặc GITHUB_TOKEN")

    encoded_workflow = urllib.parse.quote(WORKFLOW_FILE, safe="")
    url = f"https://api.github.com/repos/{REPOSITORY}/actions/workflows/{encoded_workflow}/dispatches"
    payload = {"ref": WORKFLOW_REF, "inputs": inputs}
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "zk-builder-telegram-bot",
    }
    request_json(url, method="POST", data=payload, headers=headers)
    return f"https://github.com/{REPOSITORY}/actions/workflows/{urllib.parse.quote(WORKFLOW_FILE)}"


def is_allowed(chat_id: int | str) -> bool:
    if not ALLOWED_CHAT:
        return True
    return str(chat_id) == ALLOWED_CHAT


def handle_message(message: dict[str, Any]) -> None:
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()
    if not chat_id or not text:
        return

    if not is_allowed(chat_id):
        send_message(chat_id, "⛔ Chat này không được phép dùng bot.")
        return

    command = text.split()[0].split("@", 1)[0].lower()
    if command in {"/start", "/help"}:
        send_message(chat_id, HELP_TEXT)
        return

    if command == "/build":
        try:
            inputs = parse_build_command(text)
            workflow_url = dispatch_workflow(inputs)
            send_message(
                chat_id,
                "✅ <b>Đã gửi lệnh build</b>\n"
                f"📦 <b>OTA:</b> <code>{inputs['URL']}</code>\n"
                f"🌿 <b>Branch:</b> <code>{inputs['BRANCH']}</code>\n"
                f"📤 <b>Gofile:</b> <code>{inputs['UPLOAD_GOFILE']}</code>\n"
                f"📤 <b>Pixeldrain:</b> <code>{inputs['UPLOAD_PIXELDRAIN']}</code>",
                {"inline_keyboard": [[{"text": "🔍 Xem workflow", "url": workflow_url}]]},
            )
        except Exception as exc:  # noqa: BLE001
            send_message(chat_id, f"❌ <b>Lỗi:</b> <code>{str(exc)}</code>")
        return

    send_message(chat_id, "Không hiểu lệnh. Gõ /help để xem hướng dẫn.")


def main() -> int:
    missing = []
    if not TELEGRAM_TOKEN:
        missing.append("TELEGRAM_TOKEN")
    if not REPOSITORY:
        missing.append("GH_REPOSITORY/GITHUB_REPOSITORY")
    if missing:
        print("Missing env: " + ", ".join(missing), file=sys.stderr)
        return 1

    tg_api("deleteWebhook", {"drop_pending_updates": False})
    print(f"Telegram bot started. Repository={REPOSITORY}, workflow={WORKFLOW_FILE}, ref={WORKFLOW_REF}")

    offset = 0
    while True:
        try:
            updates = tg_api("getUpdates", {"timeout": 50, "offset": offset + 1}).get("result", [])
            for update in updates:
                offset = max(offset, int(update.get("update_id", 0)))
                message = update.get("message") or update.get("edited_message")
                if message:
                    handle_message(message)
        except Exception as exc:  # noqa: BLE001
            print(f"Polling error: {exc}", file=sys.stderr)
            time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
