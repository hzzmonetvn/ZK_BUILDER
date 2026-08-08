#!/usr/bin/env python3
"""
Telegram Action Bot for ZK Builder
Operates strictly in target group (-1003989834547) with interactive inline button workflows.
"""

from __future__ import annotations

import copy
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

# Configuration
env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(env_file):
    with open(env_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TARGET_GROUP_ID = os.getenv(
    "TARGET_GROUP_ID",
    os.getenv("TELEGRAM_TO", os.getenv("ALLOWED_CHAT", "-1003989834547"))
).strip()

GITHUB_TOKEN = (
    os.getenv("ACTIONS_PAT", "").strip()
    or os.getenv("GH_TOKEN", "").strip()
    or os.getenv("GITHUB_TOKEN", "").strip()
)
REPOSITORY = os.getenv("GH_REPOSITORY", os.getenv("GITHUB_REPOSITORY", "")).strip()

# Active interactive wizard sessions in memory & JSON file: key = f"{chat_id}_{message_id}"
SESSIONS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot_sessions.json")

def load_sessions() -> dict[str, dict[str, Any]]:
    if os.path.exists(SESSIONS_FILE):
        try:
            with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_sessions() -> None:
    try:
        with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(SESSIONS, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

SESSIONS: dict[str, dict[str, Any]] = load_sessions()

URL_RE = re.compile(r"https?://[^\s+]+", re.IGNORECASE)


def request_json(
    url: str,
    *,
    method: str = "GET",
    data: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
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


def is_allowed_chat(chat_id: int | str) -> bool:
    return str(chat_id) == TARGET_GROUP_ID


def split_text(text: str, max_length: int = 4000) -> list[str]:
    """Splits long message text into chunks smaller than max_length."""
    if len(text) <= max_length:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, max_length)
        if split_at == -1:
            split_at = max_length
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


def send_message(
    chat_id: int | str,
    text: str,
    reply_markup: dict[str, Any] | None = None,
    reply_to_message_id: int | None = None,
) -> dict[str, Any]:
    chunks = split_text(text)
    res = {}
    for i, chunk in enumerate(chunks):
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup and i == len(chunks) - 1:
            payload["reply_markup"] = reply_markup
        if reply_to_message_id and i == 0:
            payload["reply_to_message_id"] = reply_to_message_id
        res = tg_api("sendMessage", payload)
    return res


def edit_message(
    chat_id: int | str,
    message_id: int,
    text: str,
    reply_markup: dict[str, Any] | None = None,
) -> None:
    chunks = split_text(text)
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": chunks[0],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        tg_api("editMessageText", payload)
    except Exception as exc:
        print(f"Edit message error: {exc}", file=sys.stderr)


def answer_callback(callback_id: str, text: str = "", show_alert: bool = False) -> None:
    payload: dict[str, Any] = {"callback_query_id": callback_id}
    if text:
        payload["text"] = text
    if show_alert:
        payload["show_alert"] = True
    try:
        tg_api("answerCallbackQuery", payload)
    except Exception:
        pass


ADMIN_IDS = {"5523842976"}
extra_admins = os.getenv("ADMIN_IDS", "").strip()
if extra_admins:
    for aid in re.split(r"[,\s]+", extra_admins):
        if aid:
            ADMIN_IDS.add(aid)


def is_authorized_user(click_user_id: int | str, requester_user_id: int | str | None) -> bool:
    click_str = str(click_user_id)
    if click_str in ADMIN_IDS:
        return True
    if requester_user_id and click_str == str(requester_user_id):
        return True
    return False


def dispatch_github_workflow(workflow_file: str, inputs: dict[str, str]) -> str:
    if not REPOSITORY:
        raise RuntimeError("Thiếu cấu hình GH_REPOSITORY / GITHUB_REPOSITORY")
    if not GITHUB_TOKEN:
        raise RuntimeError("Thiếu GITHUB_TOKEN / ACTIONS_PAT")

    encoded_workflow = urllib.parse.quote(workflow_file, safe="")
    url = f"https://api.github.com/repos/{REPOSITORY}/actions/workflows/{encoded_workflow}/dispatches"
    payload = {"ref": "main", "inputs": inputs}
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "zk-builder-telegram-bot",
    }
    request_json(url, method="POST", data=payload, headers=headers)
    return f"https://github.com/{REPOSITORY}/actions/workflows/{encoded_workflow}"


def cancel_github_workflow_runs(workflow_file: str) -> list[int]:
    if not REPOSITORY or not GITHUB_TOKEN:
        raise RuntimeError("Thiếu REPOSITORY hoặc GITHUB_TOKEN")

    encoded_workflow = urllib.parse.quote(workflow_file, safe="")
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "zk-builder-telegram-bot",
    }

    cancelled_ids = []
    for status in ["in_progress", "queued", "requested", "waiting"]:
        url = f"https://api.github.com/repos/{REPOSITORY}/actions/workflows/{encoded_workflow}/runs?status={status}&per_page=10"
        try:
            res = request_json(url, method="GET", headers=headers)
            runs = res.get("workflow_runs", [])
            for run in runs:
                run_id = run.get("id")
                if run_id and run_id not in cancelled_ids:
                    cancel_url = f"https://api.github.com/repos/{REPOSITORY}/actions/runs/{run_id}/cancel"
                    try:
                        request_json(cancel_url, method="POST", headers=headers)
                        cancelled_ids.append(run_id)
                    except Exception as e:
                        print(f"Failed to cancel run {run_id}: {e}", file=sys.stderr)
        except Exception as e:
            print(f"Failed to list runs for status {status}: {e}", file=sys.stderr)

    return cancelled_ids


def parse_urls_from_text(text: str) -> list[str]:
    return URL_RE.findall(text)


def default_build_data(url: str, url2: str = "") -> dict[str, Any]:
    return {
        "url": url,
        "url2": url2,
        "branch": "hzz",
        "mode": "auto",
        "options": {
            "OPTION_TOOLBOX": True,
            "OPTION_JAMESDSP": True,
            "OPTION_DEVICE_FEATURES": True,
            "OPTION_WIFI_BONDING": True,
            "OPTION_THERMAL": True,
            "OPTION_INIT_RC": True,
            "OPTION_ZK_MODS": True,
            "OPTION_FAST_CHARGE": True,
            "OPTION_REMOVE_AI": True,
            "OPTION_PATCH_JARS": True,
            "OPTION_PATCH_APKS": True,
            "OPTION_SELINUX_PATCH": True,
        },
        "uploads": {
            "UPLOAD_GOFILE": False,
            "UPLOAD_PIXELDRAIN": True,
        },
    }


def default_cp_data(url1: str, url2: str) -> dict[str, Any]:
    return {
        "url1": url1,
        "url2": url2,
        "partitions": {
            "system": True,
            "vendor": True,
            "product": True,
            "system_ext": True,
            "odm": False,
            "mi_ext": False,
        },
    }


# ==============================================================================
# MENU RENDERERS & WIZARD STEPS
# ==============================================================================

def render_build_step(session: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    step = session["step"]
    data = session["data"]
    url = data["url"]

    if step == 1:
        # Step 1: Select Mode
        text = (
            f"🔨 <b>Cấu hình Build ROM — Bước 1/2: Chọn Mode</b>\n"
            f"📦 <b>ROM URL:</b> <code>{url}</code>\n\n"
            f"Vui lòng chọn Build Mode:"
        )
        kbd = {
            "inline_keyboard": [
                [
                    {"text": f"{'▶ ' if data['mode']=='auto' else ''}🧩 auto", "callback_data": "b_mode:auto"},
                    {"text": f"{'▶ ' if data['mode']=='stock' else ''}🧩 stock", "callback_data": "b_mode:stock"},
                    {"text": f"{'▶ ' if data['mode']=='eu' else ''}🧩 eu", "callback_data": "b_mode:eu"},
                ],
                [
                    {"text": "◀️ Quay lại", "callback_data": "b_back"},
                    {"text": "❌ Hủy", "callback_data": "b_cancel"},
                ],
            ]
        }
        return text, kbd

    elif step == 2:
        # Step 2: Toggle Upload & Confirm Start
        text = (
            f"🔨 <b>Cấu hình Build ROM — Bước 2/2: Upload & Xác nhận</b>\n"
            f"📦 <b>ROM URL:</b> <code>{url}</code>\n"
            f"🧩 <b>Mode:</b> <code>{data['mode']}</code>\n\n"
            f"Chọn kênh Upload file sau khi build:"
        )
        ups = data["uploads"]

        def up_btn(label: str, key: str) -> dict[str, str]:
            status = "✅" if ups.get(key, False) else "❌"
            return {"text": f"{status} {label}", "callback_data": f"b_up:{key}"}

        kbd = {
            "inline_keyboard": [
                [up_btn("Gofile", "UPLOAD_GOFILE"), up_btn("Pixeldrain", "UPLOAD_PIXELDRAIN")],
                [{"text": "🚀 BẮT ĐẦU BUILD ROM", "callback_data": "b_start_build"}],
                [
                    {"text": "◀️ Quay lại", "callback_data": "b_back"},
                    {"text": "❌ Hủy", "callback_data": "b_cancel"},
                ],
            ]
        }
        return text, kbd

    return "Lỗi bước", {"inline_keyboard": []}


def render_cp_step(session: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    data = session["data"]
    parts = data["partitions"]

    text = (
        f"🔍 <b>Cấu hình So sánh ROM (Compare)</b>\n"
        f"🔗 <b>ROM 1:</b> <code>{data['url1']}</code>\n"
        f"🔗 <b>ROM 2:</b> <code>{data['url2']}</code>\n\n"
        f"Chọn các phân vùng cần so sánh:"
    )

    def p_btn(p_name: str) -> dict[str, str]:
        status = "✅" if parts.get(p_name, False) else "❌"
        return {"text": f"{status} {p_name}", "callback_data": f"c_part:{p_name}"}

    kbd = {
        "inline_keyboard": [
            [p_btn("system"), p_btn("vendor")],
            [p_btn("product"), p_btn("system_ext")],
            [p_btn("odm"), p_btn("mi_ext")],
            [{"text": "🚀 BẮT ĐẦU SO SÁNH", "callback_data": "c_start_compare"}],
            [
                {"text": "◀️ Quay lại", "callback_data": "c_back"},
                {"text": "❌ Hủy", "callback_data": "c_cancel"},
            ],
        ]
    }
    return text, kbd


def push_history(session: dict[str, Any]) -> None:
    session["history"].append(
        {
            "step": session["step"],
            "data": copy.deepcopy(session["data"]),
        }
    )


def pop_history(session: dict[str, Any]) -> bool:
    if session["history"]:
        prev = session["history"].pop()
        session["step"] = prev["step"]
        session["data"] = prev["data"]
        return True
    return False


# ==============================================================================
# CALLBACK QUERY HANDLER
# ==============================================================================

def handle_callback_query(callback: dict[str, Any]) -> None:
    callback_id = callback.get("id", "")
    message = callback.get("message", {})
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    message_id = message.get("message_id")
    data_str = callback.get("data", "")

    if not chat_id or not is_allowed_chat(chat_id):
        answer_callback(callback_id)
        return

    click_user_id = callback.get("from", {}).get("id")

    # Direct workflow cancel callback from final message
    if data_str.startswith("cancel_wf:"):
        parts = data_str.split(":", 2)
        workflow_file = parts[1]
        req_uid = parts[2] if len(parts) > 2 else None

        if not is_authorized_user(click_user_id, req_uid):
            answer_callback(
                callback_id,
                "⚠️ Bạn không có quyền hủy workflow này!\n(Chỉ người tạo lệnh hoặc Admin 5523842976 mới được thao tác)",
                show_alert=True,
            )
            return

        answer_callback(callback_id, "Đang gửi lệnh hủy workflow tới GitHub...")
        try:
            cancelled_ids = cancel_github_workflow_runs(workflow_file)
            if cancelled_ids:
                ids_str = ", ".join(f"<code>{cid}</code>" for cid in cancelled_ids)
                edit_message(
                    chat_id,
                    message_id,
                    f"🛑 <b>ĐÃ HỦY WORKFLOW TRÊN GITHUB ACTIONS!</b>\n\n"
                    f"📦 <b>Workflow:</b> <code>{workflow_file}</code>\n"
                    f"📌 <b>Cancelled Run ID:</b> {ids_str}"
                )
            else:
                answer_callback(callback_id, "⚠️ Không tìm thấy Workflow nào đang chạy để hủy.")
        except Exception as exc:
            answer_callback(callback_id, f"❌ Lỗi hủy workflow: {exc}")
        return

    session_key = f"{chat_id}_{message_id}"
    session = SESSIONS.get(session_key)

    if not session:
        answer_callback(callback_id, "⚠️ Phiên làm việc đã hết hạn hoặc bị hủy.")
        edit_message(chat_id, message_id, "❌ <b>Phiên làm việc đã hết hạn. Vui lòng gửi lại lệnh mới.</b>")
        return

    req_uid = session.get("user_id")
    stype = session["type"]

    # ---------------- BUILD WORKFLOW CALLBACKS ----------------
    if stype == "build":
        if data_str == "b_cancel":
            if not is_authorized_user(click_user_id, req_uid):
                answer_callback(
                    callback_id,
                    "⚠️ Bạn không có quyền hủy lệnh này!\n(Chỉ người tạo lệnh hoặc Admin 5523842976 mới được hủy)",
                    show_alert=True,
                )
                return
            answer_callback(callback_id, "Đã hủy")
            SESSIONS.pop(session_key, None)
            save_sessions()
            edit_message(chat_id, message_id, "❌ <b>Đã hủy lệnh Build ROM.</b>")
            return

        elif data_str == "b_back":
            if not session["history"]:
                if not is_authorized_user(click_user_id, req_uid):
                    answer_callback(
                        callback_id,
                        "⚠️ Bạn không có quyền hủy lệnh này!\n(Chỉ người tạo lệnh hoặc Admin 5523842976 mới được hủy)",
                        show_alert=True,
                    )
                    return
                answer_callback(callback_id, "Đã hủy")
                SESSIONS.pop(session_key, None)
                save_sessions()
                edit_message(chat_id, message_id, "❌ <b>Đã hủy lệnh Build ROM.</b>")
                return

            answer_callback(callback_id, "Quay lại")
            pop_history(session)
            save_sessions()
            text, kbd = render_build_step(session)
            edit_message(chat_id, message_id, text, kbd)
            return

        elif data_str.startswith("b_mode:"):
            mode = data_str.split(":", 1)[1]
            push_history(session)
            session["data"]["mode"] = mode
            session["step"] = 2
            answer_callback(callback_id, f"Mode: {mode}")
            text, kbd = render_build_step(session)
            edit_message(chat_id, message_id, text, kbd)
            return

        elif data_str.startswith("b_opt:"):
            opt_key = data_str.split(":", 1)[1]
            cur = session["data"]["options"].get(opt_key, False)
            session["data"]["options"][opt_key] = not cur
            answer_callback(callback_id, f"{opt_key}: {'ON' if not cur else 'OFF'}")
            text, kbd = render_build_step(session)
            edit_message(chat_id, message_id, text, kbd)
            return

        elif data_str == "b_next_upload":
            push_history(session)
            session["step"] = 3
            answer_callback(callback_id)
            text, kbd = render_build_step(session)
            edit_message(chat_id, message_id, text, kbd)
            return

        elif data_str.startswith("b_up:"):
            up_key = data_str.split(":", 1)[1]
            cur = session["data"]["uploads"].get(up_key, False)
            session["data"]["uploads"][up_key] = not cur
            answer_callback(callback_id, f"{up_key}: {'ON' if not cur else 'OFF'}")
            save_sessions()
            text, kbd = render_build_step(session)
            edit_message(chat_id, message_id, text, kbd)
            return

        elif data_str == "b_start_build":
            answer_callback(callback_id, "Đang khởi chạy workflow...")
            bdata = session["data"]
            inputs = {
                "URL": bdata["url"],
                "BRANCH": bdata["branch"],
                "MODE": bdata["mode"],
                "UPLOAD_GOFILE": "true" if bdata["uploads"]["UPLOAD_GOFILE"] else "false",
                "UPLOAD_PIXELDRAIN": "true" if bdata["uploads"]["UPLOAD_PIXELDRAIN"] else "false",
            }
            inputs["OPTION_SHOW_LOG"] = "false"
            for k, v in bdata["options"].items():
                inputs[k] = "true" if v else "false"

            try:
                wf_url = dispatch_github_workflow("ZK BUILDER FORK.yml", inputs)
                SESSIONS.pop(session_key, None)
                save_sessions()

                summary_text = (
                    f"✅ <b>ĐÃ KÍCH HOẠT WORKFLOW BUILD ROM!</b>\n\n"
                    f"📦 <b>URL:</b> <code>{bdata['url']}</code>\n"
                    f"🌿 <b>Branch:</b> <code>{bdata['branch']}</code>\n"
                    f"🧩 <b>Mode:</b> <code>{bdata['mode']}</code>\n"
                    f"📤 <b>Upload:</b> Gofile: <code>{inputs['UPLOAD_GOFILE']}</code> | Pixeldrain: <code>{inputs['UPLOAD_PIXELDRAIN']}</code>\n\n"
                    f"🔍 Theo dõi tiến trình build chi tiết trên GitHub Actions."
                )
                req_user_id = session.get("user_id", "")
                kbd = {
                    "inline_keyboard": [
                        [{"text": "🔍 Xem Workflow trên GitHub", "url": wf_url}],
                        [{"text": "🛑 HỦY WORKFLOW GITHUB", "callback_data": f"cancel_wf:ZK BUILDER FORK.yml:{req_user_id}"}]
                    ]
                }
                edit_message(chat_id, message_id, summary_text, kbd)
            except Exception as exc:
                edit_message(chat_id, message_id, f"❌ <b>Lỗi kích hoạt workflow:</b> <code>{exc}</code>")
            return

    # ---------------- COMPARE WORKFLOW CALLBACKS ----------------
    elif stype == "cp":
        if data_str == "c_cancel" or data_str == "c_back":
            if not is_authorized_user(click_user_id, req_uid):
                answer_callback(
                    callback_id,
                    "⚠️ Bạn không có quyền hủy lệnh này!\n(Chỉ người tạo lệnh hoặc Admin 5523842976 mới được hủy)",
                    show_alert=True,
                )
                return
            answer_callback(callback_id, "Đã hủy")
            SESSIONS.pop(session_key, None)
            save_sessions()
            edit_message(chat_id, message_id, "❌ <b>Đã hủy lệnh So sánh ROM.</b>")
            return

        elif data_str.startswith("c_part:"):
            p_name = data_str.split(":", 1)[1]
            cur = session["data"]["partitions"].get(p_name, False)
            session["data"]["partitions"][p_name] = not cur
            answer_callback(callback_id, f"Phân vùng {p_name}: {'BẬT' if not cur else 'TẮT'}")
            text, kbd = render_cp_step(session)
            edit_message(chat_id, message_id, text, kbd)
            return

        elif data_str == "c_start_compare":
            cdata = session["data"]
            selected_parts = [p for p, v in cdata["partitions"].items() if v]
            if not selected_parts:
                answer_callback(callback_id, "⚠️ Phải chọn ít nhất 1 phân vùng!", text="")
                return

            answer_callback(callback_id, "Đang khởi chạy so sánh...")
            inputs = {
                "rom_url_1": cdata["url1"],
                "rom_url_2": cdata["url2"],
                "partitions": ",".join(selected_parts),
            }

            try:
                wf_url = dispatch_github_workflow("kang.yml", inputs)
                SESSIONS.pop(session_key, None)
                save_sessions()

                summary_text = (
                    f"✅ <b>ĐÃ KÍCH HOẠT WORKFLOW SO SÁNH ROM!</b>\n\n"
                    f"🔗 <b>ROM 1:</b> <code>{cdata['url1']}</code>\n"
                    f"🔗 <b>ROM 2:</b> <code>{cdata['url2']}</code>\n"
                    f"📁 <b>Phân vùng:</b> <code>{','.join(selected_parts)}</code>\n\n"
                    f"🔍 Theo dõi tiến trình so sánh chi tiết trên GitHub Actions."
                )
                req_user_id = session.get("user_id", "")
                kbd = {
                    "inline_keyboard": [
                        [{"text": "🔍 Xem Workflow trên GitHub", "url": wf_url}],
                        [{"text": "🛑 HỦY WORKFLOW GITHUB", "callback_data": f"cancel_wf:kang.yml:{req_user_id}"}]
                    ]
                }
                edit_message(chat_id, message_id, summary_text, kbd)
            except Exception as exc:
                edit_message(chat_id, message_id, f"❌ <b>Lỗi kích hoạt workflow:</b> <code>{exc}</code>")
            return


# ==============================================================================
# MESSAGE HANDLER
# ==============================================================================

def handle_text_message(message: dict[str, Any]) -> None:
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()
    msg_id = message.get("message_id")

    if not chat_id or not text or not is_allowed_chat(chat_id):
        return

    # Check if there is an active session running in this chat
    active_keys = [k for k in SESSIONS if k.startswith(f"{chat_id}_")]

    urls = parse_urls_from_text(text)
    lower_text = text.lower()

    is_build_cmd = lower_text.startswith("build") or lower_text.startswith("/build")
    is_cp_cmd = lower_text.startswith("cp") or lower_text.startswith("/cp")

    # 1. Process "build" command
    if is_build_cmd:
        if not urls:
            send_message(
                chat_id,
                "❌ <b>Thiếu link ROM.</b>\n\n"
                "💡 <b>Cú pháp đúng:</b>\n"
                "<code>build + [link]</code>",
                reply_to_message_id=msg_id,
            )
            return

        bdata = default_build_data(urls[0], urls[1] if len(urls) > 1 else "")
        temp_session = {
            "chat_id": chat_id,
            "type": "build",
            "step": 1,
            "history": [],
            "data": bdata,
        }
        init_text, init_kbd = render_build_step(temp_session)
        sent = send_message(chat_id, init_text, init_kbd, reply_to_message_id=msg_id)

        sent_msg_id = sent.get("result", {}).get("message_id")
        if sent_msg_id:
            session_key = f"{chat_id}_{sent_msg_id}"
            temp_session["message_id"] = sent_msg_id
            SESSIONS[session_key] = temp_session
            save_sessions()
        return

    # 2. Process "cp" command
    if is_cp_cmd:
        if len(urls) < 2:
            send_message(
                chat_id,
                "❌ <b>Cần đủ 2 link ROM để so sánh.</b>\n\n"
                "💡 <b>Cú pháp đúng:</b>\n"
                "<code>cp + [link1] + [link2]</code>",
                reply_to_message_id=msg_id,
            )
            return

        cdata = default_cp_data(urls[0], urls[1])
        temp_session = {
            "chat_id": chat_id,
            "type": "cp",
            "step": 1,
            "history": [],
            "data": cdata,
        }
        init_text, init_kbd = render_cp_step(temp_session)
        sent = send_message(chat_id, init_text, init_kbd, reply_to_message_id=msg_id)

        sent_msg_id = sent.get("result", {}).get("message_id")
        if sent_msg_id:
            session_key = f"{chat_id}_{sent_msg_id}"
            temp_session["message_id"] = sent_msg_id
            SESSIONS[session_key] = temp_session
            save_sessions()
        return

    # 3. Non-command messages: ignore completely (do not spam group)
    return


# ==============================================================================
# MAIN LOOP
# ==============================================================================

def main() -> int:
    if not TELEGRAM_TOKEN:
        print("Error: TELEGRAM_TOKEN is required", file=sys.stderr)
        return 1

    tg_api("deleteWebhook", {"drop_pending_updates": True})
    print(
        f"Bot started successfully.\n"
        f"Target Group ID: {TARGET_GROUP_ID}\n"
        f"Repository: {REPOSITORY}"
    )

    offset = 0
    # Skip all historical pending updates
    try:
        init_res = tg_api("getUpdates", {"offset": -1, "timeout": 0})
        init_updates = init_res.get("result", [])
        if init_updates:
            offset = int(init_updates[-1].get("update_id", 0))
            print(f"Skipped past updates up to ID: {offset}")
    except Exception as exc:
        print(f"Error fetching initial updates offset: {exc}", file=sys.stderr)
    while True:
        try:
            updates = tg_api("getUpdates", {"timeout": 50, "offset": offset + 1}).get("result", [])
            for update in updates:
                offset = max(offset, int(update.get("update_id", 0)))
                callback_query = update.get("callback_query")
                if callback_query:
                    handle_callback_query(callback_query)
                    continue

                message = update.get("message") or update.get("edited_message")
                if message:
                    handle_text_message(message)
        except Exception as exc:
            print(f"Polling error: {exc}", file=sys.stderr)
            time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
