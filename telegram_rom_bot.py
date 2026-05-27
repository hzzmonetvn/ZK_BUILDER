#!/usr/bin/env python3
import html, json, logging, os, re, signal, sqlite3, threading, time
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import quote
import requests
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

FOLDER_ID_DEFAULT = '1eeL23I9mF0LdUXZSuyDsNSZhk4FlvhsZ'
CONTACT_DEFAULT = '@HzzMonet'
SYNC_DEFAULT = 21600
ROM_RE = re.compile(r'^(ZK[A-Za-z0-9-]*)_([A-Za-z0-9]+)_(.+?)_([A-Za-z]{2})(\d{6})\.zip$', re.I)
FOLDER_RE = re.compile(r'/folders/([A-Za-z0-9_-]+)')


def env(name, default=''):
    return os.getenv(name, default).strip()


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def norm_device(s):
    return re.sub(r'[^a-z0-9_-]', '', s.strip().lower())


def folder_id(value):
    m = FOLDER_RE.search(value or '')
    return m.group(1) if m else value


def yymmdd(s):
    return f'20{s[:2]}-{s[2:4]}-{s[4:6]}'


def drive_view(fid):
    return f'https://drive.google.com/file/d/{quote(fid)}/view'


def drive_dl(fid):
    return f'https://drive.google.com/uc?export=download&id={quote(fid)}'


def parse_name(name):
    m = ROM_RE.match(name or '')
    if not m:
        return None
    return {'device': norm_device(m.group(2)), 'region': m.group(4).upper(), 'build_date': yymmdd(m.group(5))}


def admin_ids():
    raw = env('ADMIN_IDS')
    out = set()
    for part in re.split(r'[,\s]+', raw):
        if part:
            out.add(int(part))
    return out


def fmt_size(n):
    if n in (None, ''):
        return '?'
    try:
        n = float(n)
    except Exception:
        return '?'
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if n < 1024 or unit == 'TB':
            return f'{n:.2f} {unit}' if unit != 'B' else f'{int(n)} B'
        n /= 1024


class Bot:
    def __init__(self):
        self.token = env('BOT_TOKEN')
        if not self.token:
            raise SystemExit('BOT_TOKEN is required')
        self.admins = admin_ids()
        if not self.admins:
            raise SystemExit('ADMIN_IDS is required')
        self.folder = folder_id(env('DRIVE_FOLDER_ID', FOLDER_ID_DEFAULT))
        self.gkey = env('GOOGLE_API_KEY')
        self.contact = env('CONTACT_USERNAME', CONTACT_DEFAULT) or CONTACT_DEFAULT
        self.db_path = env('DB_PATH', 'rom_bot.sqlite3')
        self.sync_interval = int(env('SYNC_INTERVAL_SECONDS', str(SYNC_DEFAULT)))
        self.max_files = int(env('MAX_FILES_PER_REPLY', '10'))
        self.base = f'https://api.telegram.org/bot{self.token}'
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.init_db()

    @contextmanager
    def db(self):
        with self.lock:
            con = sqlite3.connect(self.db_path)
            con.row_factory = sqlite3.Row
            try:
                yield con
                con.commit()
            finally:
                con.close()

    def init_db(self):
        with self.db() as con:
            con.execute('create table if not exists auth_users(telegram_id integer not null, device text not null, added_by integer, added_at text, primary key(telegram_id, device))')
            con.execute('create table if not exists rom_files(file_id text primary key, name text not null unique, device text not null, region text, build_date text, view_link text not null, download_link text not null, modified_time text, size integer, synced_at text)')
            con.execute('create index if not exists idx_auth_user on auth_users(telegram_id)')
            con.execute('create index if not exists idx_rom_device on rom_files(device)')

    def tg(self, method, data, timeout=30):
        r = requests.post(f'{self.base}/{method}', data=data, timeout=timeout)
        r.raise_for_status()
        js = r.json()
        if not js.get('ok'):
            raise RuntimeError(js)
        return js

    def send(self, chat_id, text, preview=True):
        if len(text) > 3900:
            text = text[:3880] + '\n...'
        self.tg('sendMessage', {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML', 'disable_web_page_preview': not preview})

    def is_admin(self, uid):
        return uid in self.admins

    def require_admin(self, chat_id, uid):
        if self.is_admin(uid):
            return True
        self.send(chat_id, '❌ Lệnh này chỉ dành cho admin.')
        return False

    def authed(self, uid, device):
        if self.is_admin(uid):
            return True
        with self.db() as con:
            return con.execute('select 1 from auth_users where telegram_id=? and device=?', (uid, device)).fetchone() is not None

    def sync_once(self):
        files = self.drive_files_api() if self.gkey else self.drive_files_public()
        ts = now()
        with self.db() as con:
            for f in files:
                con.execute('insert into rom_files(file_id,name,device,region,build_date,view_link,download_link,modified_time,size,synced_at) values(?,?,?,?,?,?,?,?,?,?) on conflict(file_id) do update set name=excluded.name,device=excluded.device,region=excluded.region,build_date=excluded.build_date,view_link=excluded.view_link,download_link=excluded.download_link,modified_time=excluded.modified_time,size=excluded.size,synced_at=excluded.synced_at', (f['id'], f['name'], f['device'], f.get('region'), f.get('build_date'), f['view'], f['download'], f.get('modifiedTime'), f.get('size'), ts))
        logging.info('synced %s rom files', len(files))
        return len(files)

    def drive_files_api(self):
        url = 'https://www.googleapis.com/drive/v3/files'
        params = {'key': self.gkey, 'q': f"'{self.folder}' in parents and trashed = false", 'pageSize': 1000, 'fields': 'nextPageToken,files(id,name,modifiedTime,size,webViewLink,webContentLink)', 'orderBy': 'modifiedTime desc,name', 'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true'}
        out = []
        while True:
            r = requests.get(url, params=params, timeout=40)
            r.raise_for_status()
            js = r.json()
            for it in js.get('files', []):
                p = parse_name(it.get('name'))
                if p:
                    out.append({**p, 'id': it['id'], 'name': it['name'], 'view': it.get('webViewLink') or drive_view(it['id']), 'download': it.get('webContentLink') or drive_dl(it['id']), 'modifiedTime': it.get('modifiedTime'), 'size': it.get('size')})
            if not js.get('nextPageToken'):
                return out
            params['pageToken'] = js['nextPageToken']

    def drive_files_public(self):
        logging.warning('GOOGLE_API_KEY empty; using best-effort public folder HTML fallback')
        r = requests.get(f'https://drive.google.com/drive/folders/{self.folder}', headers={'User-Agent': 'Mozilla/5.0'}, timeout=40)
        r.raise_for_status()
        out = {}
        for m in re.finditer(r'\["([A-Za-z0-9_-]{20,})","([^"]+?\.zip)"', r.text):
            fid, name = m.group(1), bytes(m.group(2), 'utf-8').decode('unicode_escape')
            p = parse_name(name)
            if p:
                out[fid] = {**p, 'id': fid, 'name': name, 'view': drive_view(fid), 'download': drive_dl(fid)}
        return list(out.values())

    def cmd_help(self, chat, uid):
        msg = ['🤖 <b>ZK ROM Bot</b>', '', 'User:', '• /id - xem Telegram ID', '• /devices - xem máy đã được cấp quyền', '• /get &lt;device&gt; - lấy ROM, ví dụ <code>/get nezha</code>']
        if self.is_admin(uid):
            msg += ['', 'Admin:', '• /add &lt;id tele&gt; &lt;device&gt; [device2 ...]', '• /remove &lt;id tele&gt; &lt;device&gt; [device2 ...]', '• /sync - sync Drive ngay', '• /users - xem danh sách quyền']
        self.send(chat, '\n'.join(msg))

    def cmd_add(self, chat, uid, args):
        if not self.require_admin(chat, uid): return
        if len(args) < 2 or not args[0].lstrip('-').isdigit():
            return self.send(chat, 'Dùng: <code>/add &lt;id tele&gt; &lt;device&gt; [device2 ...]</code>')
        tid, devs = int(args[0]), [norm_device(x) for x in args[1:]]
        with self.db() as con:
            for d in devs:
                con.execute('insert or ignore into auth_users values(?,?,?,?)', (tid, d, uid, now()))
        self.send(chat, f'✅ Đã cấp quyền cho <code>{tid}</code>: <code>{html.escape(", ".join(devs))}</code>')

    def cmd_remove(self, chat, uid, args):
        if not self.require_admin(chat, uid): return
        if len(args) < 2 or not args[0].lstrip('-').isdigit():
            return self.send(chat, 'Dùng: <code>/remove &lt;id tele&gt; &lt;device&gt; [device2 ...]</code>')
        tid, devs = int(args[0]), [norm_device(x) for x in args[1:]]
        with self.db() as con:
            for d in devs:
                con.execute('delete from auth_users where telegram_id=? and device=?', (tid, d))
        self.send(chat, f'✅ Đã gỡ quyền của <code>{tid}</code>: <code>{html.escape(", ".join(devs))}</code>')

    def cmd_get(self, chat, uid, args):
        if len(args) != 1:
            return self.send(chat, 'Dùng: <code>/get &lt;device&gt;</code>, ví dụ <code>/get nezha</code>')
        dev = norm_device(args[0])
        if not self.authed(uid, dev):
            return self.send(chat, f'❌ Bạn chưa được cấp quyền cho <code>{dev}</code>. Liên hệ {html.escape(self.contact)}')
        with self.db() as con:
            rows = con.execute('select * from rom_files where device=? order by case when build_date is null then 1 else 0 end, build_date desc, modified_time desc, name desc limit ?', (dev, self.max_files)).fetchall()
        if not rows:
            return self.send(chat, f'⚠️ Chưa tìm thấy ROM cho <code>{dev}</code>. Admin có thể dùng /sync để cập nhật lại.')
        lines = [f'📱 <b>{html.escape(dev.upper())}</b> - ROM mới nhất:', '']
        for i, r in enumerate(rows, 1):
            url = r['download_link'] or r['view_link']
            lines.append(f'{i}. <a href="{html.escape(url)}">{html.escape(r["name"])}</a>')
            lines.append(f'   Region: <code>{html.escape(r["region"] or "--")}</code> | Date: <code>{html.escape(r["build_date"] or "?")}</code> | Size: <code>{fmt_size(r["size"])}</code>')
        self.send(chat, '\n'.join(lines), preview=False)

    def cmd_devices(self, chat, uid):
        with self.db() as con:
            rows = con.execute('select device from auth_users where telegram_id=? order by device', (uid,)).fetchall()
        if not rows:
            return self.send(chat, f'Bạn chưa được cấp quyền. Liên hệ {html.escape(self.contact)}')
        self.send(chat, 'Thiết bị đã được cấp quyền: ' + ', '.join(f'<code>{r["device"]}</code>' for r in rows))

    def cmd_sync(self, chat, uid):
        if not self.require_admin(chat, uid): return
        try:
            n = self.sync_once()
            self.send(chat, f'✅ Sync xong. Đã lưu/cập nhật <code>{n}</code> file ROM.')
        except Exception as e:
            logging.exception('sync failed')
            self.send(chat, f'❌ Sync lỗi: <code>{html.escape(str(e))}</code>')

    def cmd_users(self, chat, uid):
        if not self.require_admin(chat, uid): return
        with self.db() as con:
            rows = con.execute('select telegram_id, group_concat(device, ", ") devices from auth_users group by telegram_id order by telegram_id').fetchall()
        if not rows:
            return self.send(chat, 'Chưa có user nào được cấp quyền.')
        self.send(chat, '\n'.join(['👥 <b>Danh sách quyền</b>', ''] + [f'• <code>{r["telegram_id"]}</code>: <code>{html.escape(r["devices"])}</code>' for r in rows]))

    def handle(self, upd):
        m = upd.get('message') or {}
        text = (m.get('text') or '').strip()
        if not text: return
        chat = m.get('chat', {}).get('id')
        uid = m.get('from', {}).get('id')
        if chat is None or uid is None: return
        parts = text.split()
        cmd = parts[0].split('@', 1)[0].lower()
        args = parts[1:]
        if cmd in ('/start', '/help'): self.cmd_help(chat, uid)
        elif cmd == '/id': self.send(chat, f'Telegram ID của bạn: <code>{uid}</code>')
        elif cmd == '/add': self.cmd_add(chat, uid, args)
        elif cmd == '/remove': self.cmd_remove(chat, uid, args)
        elif cmd == '/get': self.cmd_get(chat, uid, args)
        elif cmd == '/devices': self.cmd_devices(chat, uid)
        elif cmd == '/sync': self.cmd_sync(chat, uid)
        elif cmd == '/users': self.cmd_users(chat, uid)
        else: self.send(chat, 'Lệnh không hợp lệ. Gõ /help để xem hướng dẫn.')

    def sync_loop(self):
        while not self.stop.wait(self.sync_interval):
            try: self.sync_once()
            except Exception: logging.exception('periodic sync failed')

    def run(self):
        logging.info('starting bot folder=%s db=%s', self.folder, self.db_path)
        self.tg('deleteWebhook', {'drop_pending_updates': False})
        try: self.sync_once()
        except Exception: logging.exception('initial sync failed')
        threading.Thread(target=self.sync_loop, daemon=True).start()
        offset = None
        while not self.stop.is_set():
            try:
                data = {'timeout': 50, 'allowed_updates': json.dumps(['message'])}
                if offset is not None: data['offset'] = offset
                for upd in self.tg('getUpdates', data, timeout=60).get('result', []):
                    offset = max(offset or 0, int(upd['update_id']) + 1)
                    self.handle(upd)
            except Exception:
                logging.exception('polling error')
                time.sleep(5)


def main():
    logging.basicConfig(level=env('LOG_LEVEL', 'INFO').upper(), format='%(asctime)s %(levelname)s %(message)s')
    bot = Bot()
    signal.signal(signal.SIGTERM, lambda *_: bot.stop.set())
    signal.signal(signal.SIGINT, lambda *_: bot.stop.set())
    bot.run()

if __name__ == '__main__':
    main()
