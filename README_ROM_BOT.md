# ZK ROM Telegram Bot

Bot này lấy danh sách ROM từ Google Drive mỗi 6 giờ, lưu quyền theo từng Telegram ID và từng device, rồi trả link ROM qua lệnh `/get <device>`.

## Chức năng

- Sync Google Drive folder định kỳ mỗi `21600` giây (6 giờ).
- Admin cấp quyền bằng `/add <id tele> <device> [device2 ...]`.
- Một user có thể có nhiều device, mỗi device là một dòng quyền riêng.
- User lấy ROM bằng `/get <device>`.
- User chưa được cấp quyền sẽ nhận thông báo liên hệ `@HzzMonet`.
- Database dùng SQLite, không mất quyền khi bot restart.

## File name hỗ trợ

Bot tự nhận device, region và ngày build từ tên kiểu:

```text
ZKOS_NEZHA_OS3.0.307.0.WPACNXM_CN260526.zip
ZKOS_NEZHA_OS3.0.307.0.WPACNXM_EU260504.zip
```

Trong ví dụ trên:

- Device: `nezha`
- Region: `CN` hoặc `EU`
- Ngày build: `2026-05-26` hoặc `2026-05-04`

## Cài đặt

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-rom-bot.txt
cp rom-bot.env.example .env
nano .env
python3 telegram_rom_bot.py
```

## Biến môi trường

| Biến | Bắt buộc | Ghi chú |
| --- | --- | --- |
| `BOT_TOKEN` | Có | Token lấy từ `@BotFather` |
| `ADMIN_IDS` | Có | ID Telegram admin, ngăn cách bằng dấu phẩy hoặc khoảng trắng |
| `DRIVE_FOLDER_ID` | Không | Mặc định là folder ROM đã cấu hình |
| `GOOGLE_API_KEY` | Khuyến nghị | Dùng Google Drive API ổn định hơn scrape HTML public folder |
| `CONTACT_USERNAME` | Không | Mặc định `@HzzMonet` |
| `DB_PATH` | Không | Mặc định `rom_bot.sqlite3` |
| `SYNC_INTERVAL_SECONDS` | Không | Mặc định `21600` |
| `MAX_FILES_PER_REPLY` | Không | Mặc định `10` |

Nên dùng `GOOGLE_API_KEY`. Nếu không có, bot sẽ thử đọc public Google Drive HTML, nhưng cách này phụ thuộc giao diện Google Drive nên kém ổn định hơn API chính thức.

## Lệnh user

```text
/id
/devices
/get nezha
/get popsicle
```

## Lệnh admin

```text
/add 123456789 nezha
/add 123456789 nezha popsicle pandora
/remove 123456789 nezha
/sync
/users
```

## Chạy bằng systemd

Tạo file `/etc/systemd/system/zk-rom-bot.service`:

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

Sau đó chạy:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now zk-rom-bot
sudo journalctl -u zk-rom-bot -f
```

## Lưu ý bảo mật

- Không commit file `.env` và `rom_bot.sqlite3` lên GitHub.
- Chỉ thêm admin thật sự cần quyền cấp/xóa user.
- Nếu user đổi máy hoặc bán máy, dùng `/remove <id> <device>` để gỡ quyền.
