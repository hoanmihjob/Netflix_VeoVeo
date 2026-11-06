# ==========================================
# FILE: sharetool.py (FULL STANDALONE EDITION)
# Netflix Vèo Vèo - Automation & Tools
# ==========================================

import tkinter as tk
import json
import re
import time
import asyncio
import threading
import queue
import os
import sys
import subprocess
from PIL import Image, ImageGrab
import tempfile

try:
    from rapidocr_onnxruntime import RapidOCR
    _ocr_engine = RapidOCR()
except ImportError:
    _ocr_engine = None

from playwright.async_api import async_playwright
from dotenv import load_dotenv

# Kiểm tra thư viện Telethon
try:
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError
except ImportError:
    TelegramClient = None
    SessionPasswordNeededError = Exception

# ==========================================
# 0. QUẢN LÝ ĐƯỜNG DẪN ĐÓNG GÓI (BUNDLE & LOCAL)
# ==========================================

def get_bundle_dir():
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))

def get_app_data_dir():
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
        if sys.platform == "darwin" and ".app/Contents/MacOS" in exe_dir:
            return os.path.abspath(os.path.join(exe_dir, "../../.."))
        return exe_dir
    return os.path.dirname(os.path.abspath(__file__))

BUNDLE_DIR = get_bundle_dir()
APP_DIR = get_app_data_dir()

env_external = os.path.join(APP_DIR, ".env")
env_bundled = os.path.join(BUNDLE_DIR, ".env")
if os.path.exists(env_external):
    load_dotenv(env_external)
elif os.path.exists(env_bundled):
    load_dotenv(env_bundled)

def get_session_path():
    return os.path.join(APP_DIR, "user_telegram")


# ==========================================
# CẤU HÌNH TEMPLATE CHUẨN HOÁ CSKH (DATA-DRIVEN)
# ==========================================

TEMPLATE_FILE = os.path.join(APP_DIR, "templates.json")

# Danh mục các định dạng chuẩn hoá (dễ dàng thêm bớt định dạng tại đây)
FORMAT_SCHEMAS = [
    {
        "id": "capcut",
        "title": "Format Capcut - Email + Pass",
        "default": (
            "Email: {email}\n"
            "Password: {password}\n\n"
            "Pls remove the space after @ before logging in po."
        )
    },
    {
        "id": "netflix",
        "title": "Format Netflix - Email + Username",
        "default": (
            "Email: {email}\n"
            "Pls remove the space after @ before logging in po.\n\n"
            "Account to get code:\n"
            "Username: {username}\n"
            "Password: {password}"
        )
    },
    {
        "id": "chatgpt",
        "title": "Format ChatGPT - Email + Pass + 2FA",
        "default": (
            "Email: {email}\n"
            "Password: {password}\n"
            "2FA Secret: {code_2fa}\n"
            "Get 2FA code at: https://2fa.live\n\n"
            "Pls remove the space after @ before logging in po."
        )
    }
]

DEFAULT_TEMPLATES = {cfg["id"]: cfg["default"] for cfg in FORMAT_SCHEMAS}
DEFAULT_TEMPLATES["netflix_default_pass"] = "1234567891011"

def load_custom_templates():
    if os.path.exists(TEMPLATE_FILE):
        try:
            with open(TEMPLATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {**DEFAULT_TEMPLATES, **data}
        except Exception:
            pass
    return DEFAULT_TEMPLATES.copy()

def save_custom_templates(data):
    try:
        with open(TEMPLATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ==========================================
# 1. CÁC HÀM BÓC TÁCH VÀ CHUẨN HOÁ DỮ LIỆU
# ==========================================

def extract_code_from_clipboard_image():
    """
    Tự động đọc ảnh từ Clipboard (Đã vá lỗi mù Clipboard trên macOS).
    """
    if _ocr_engine is None:
        return None

    try:
        # Thử lấy ảnh bằng Pillow chuẩn
        img = ImageGrab.grabclipboard()
        
        # ĐẶC TRỊ MACOS: Nếu Pillow mù, dùng AppleScript ép lấy ảnh từ Clipboard
        if img is None and sys.platform == "darwin":
            tmp_path = os.path.join(tempfile.gettempdir(), "mac_clip_tmp.png")
            script = f'''
            set theFile to open for access POSIX file "{tmp_path}" with write permission
            set eof theFile to 0
            try
                write (the clipboard as «class PNGf») to theFile
                close access theFile
                return "OK"
            on error
                try
                    close access theFile
                end try
                return "NO"
            end try
            '''
            res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
            if "OK" in res.stdout and os.path.exists(tmp_path):
                img = Image.open(tmp_path)

        if img is None:
            return None

        # Chạy nhận diện
        result, _ = _ocr_engine(img)
        if not result:
            return None

        full_text = " ".join([line[1] for line in result])

        match = re.search(r'\b(\d{8})\b', full_text)
        if match:
            return match.group(1)

        match_split = re.search(r'\b(\d{4})[\s\-_]+(\d{4})\b', full_text)
        if match_split:
            return f"{match_split.group(1)}{match_split.group(2)}"

    except Exception as e:
        print(f"Lỗi đọc ảnh OCR: {e}")

    return None

def find_matching_braces(s, start_idx):
    depth = 0
    for i in range(start_idx, len(s)):
        ch = s[i]
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return i
    return None

def extract_cookie_json(s):
    s = str(s or "")
    for match in re.finditer(r"\{", s):
        start_idx = match.start()
        end_idx = find_matching_braces(s, start_idx)
        if end_idx is None:
            continue
        candidate = s[start_idx:end_idx + 1].strip()
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict) and any(k in obj for k in ("cookies", "url", "uri", "origin")):
                return candidate
        except Exception:
            continue
    return None

def extract_header_cookies(s):
    s = str(s or "").strip()
    match = re.search(r'(?:cookies?\s*[:=]\s*)([^\r\n]+)', s, flags=re.I)
    if match:
        cookie_part = match.group(1).strip()
    else:
        idx = s.find("NetflixId=")
        if idx != -1:
            cookie_part = s[idx:].strip()
        else:
            return None

    if cookie_part.startswith("{") or cookie_part.startswith("["):
        return None

    if " | " in cookie_part:
        cookie_part = cookie_part.split(" | ")[0].strip()
    elif "|" in cookie_part:
        cookie_part = cookie_part.split("|")[0].strip()

    cookie_list = []
    for pair in cookie_part.split(";"):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        k, v = k.strip(), v.strip()
        if not k or not v:
            continue
        
        cookie_list.append({
            "domain": ".netflix.com",
            "hostOnly": False,
            "httpOnly": k in ("SecureNetflixId", "NetflixId"),
            "name": k,
            "path": "/",
            "sameSite": "Lax" if k == "NetflixId" else ("Strict" if k == "SecureNetflixId" else "None"),
            "secure": True,
            "session": False,
            "value": v
        })

    if not cookie_list:
        return None

    return json.dumps({"url": "https://www.netflix.com", "cookies": cookie_list}, ensure_ascii=False)

def parse_input_source(raw_text):
    s = str(raw_text or "").strip()
    if not s:
        return None, None

    json_str = extract_cookie_json(s)
    if json_str:
        try:
            data = json.loads(json_str)
            cookies = data.get("cookies", []) if isinstance(data, dict) else data
            if isinstance(cookies, list) and len(cookies) > 0:
                return "JSON", cookies
        except Exception:
            pass

    header_str = extract_header_cookies(s)
    if header_str:
        try:
            data = json.loads(header_str)
            cookies = data.get("cookies", [])
            if isinstance(cookies, list) and len(cookies) > 0:
                return "Metadata", cookies
        except Exception:
            pass

    return None, None

def prepare_cookies_for_playwright(cookies_list):
    allowed_keys = {"name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite"}
    prepared = []
    for raw_cookie in list(cookies_list or []):
        if not isinstance(raw_cookie, dict):
            continue
        cookie = dict(raw_cookie)
        if "domain" not in cookie or not cookie["domain"]:
            cookie["domain"] = ".netflix.com"
        if "path" not in cookie or not cookie["path"]:
            cookie["path"] = "/"
        cookie.pop("url", None)

        if "expirationDate" in cookie and "expires" not in cookie:
            cookie["expires"] = cookie.pop("expirationDate")
        if "expires" in cookie:
            try:
                cookie["expires"] = float(time.time() + (365 * 24 * 60 * 60))
            except Exception:
                cookie.pop("expires", None)

        for k_alt in ("same_site", "SameSite"):
            if k_alt in cookie and "sameSite" not in cookie:
                cookie["sameSite"] = cookie.pop(k_alt)
            else:
                cookie.pop(k_alt, None)

        if "sameSite" in cookie:
            s_val = str(cookie["sameSite"]).strip().lower()
            mapping = {
                "strict": "Strict",
                "lax": "Lax",
                "none": "None",
                "no_restriction": "None",
                "unspecified": "None"
            }
            if s_val in mapping:
                cookie["sameSite"] = mapping[s_val]
            else:
                cookie.pop("sameSite", None)

        if "name" in cookie:
            cookie["name"] = str(cookie["name"])
        if "value" in cookie:
            cookie["value"] = str(cookie["value"])

        for key in list(cookie.keys()):
            if key not in allowed_keys:
                cookie.pop(key, None)

        prepared.append(cookie)
    return prepared

def format_bot_account_text(raw_text: str):
    """
    Chuẩn hoá CSKH thông minh:
    - Phân biệt chính xác ChatGPT và Capcut dựa trên chuẩn mã 2FA Base32 (viết HOA, dài >= 16).
    - Tự động bỏ text thừa cho cả Capcut, ChatGPT và Netflix.
    """
    text = str(raw_text or "").strip()
    if not text or "STATUS =" in text or "NetflixId=" in text:
        return None

    templates = load_custom_templates()

    # 1. Tìm email chuẩn (hỗ trợ dấu +, -, ., _)
    email_match = re.search(r'([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)', text)
    if not email_match:
        return None

    raw_email = email_match.group(1).strip()
    email_formatted = raw_email.replace("@", "@ ", 1)
    email_prefix = raw_email.split("@")[0]

    # ========================================================
    # ĐỊNH DẠNG 1 & 2: CÁC DẠNG DÙNG DẤU GẠCH ĐỨNG | (CHATGPT & CAPCUT)
    # ========================================================
    if "|" in text:
        lines = [line.strip() for line in text.splitlines() if "|" in line]
        target_line = lines[0] if lines else text
        pipe_parts = [p.strip() for p in target_line.split("|")]

        email_idx = -1
        for i, part in enumerate(pipe_parts):
            if raw_email in part:
                email_idx = i
                break

        if email_idx != -1:
            remaining_parts = pipe_parts[email_idx + 1:]

            if len(remaining_parts) >= 1:
                password = remaining_parts[0].split()[0] if remaining_parts[0] else ""

                # Kiểm tra khúc thứ 3 xem có phải mã 2FA Secret của ChatGPT hay không
                # Chuẩn 2FA Secret: toàn chữ HOA A-Z và số 2-7, độ dài tối thiểu 16 ký tự
                is_chatgpt = False
                code_2fa = ""

                if len(remaining_parts) >= 2:
                    candidate_2fa = remaining_parts[1].split()[0] if remaining_parts[1] else ""
                    # Nếu là chữ in HOA và độ dài từ 16 ký tự trở lên -> Chuẩn 2FA ChatGPT
                    if re.fullmatch(r'[A-Z2-7]{16,}', candidate_2fa):
                        is_chatgpt = True
                        code_2fa = candidate_2fa

                if is_chatgpt:
                    template = templates.get("chatgpt", DEFAULT_TEMPLATES.get("chatgpt", ""))
                    return template.format(
                        email=email_formatted,
                        password=password,
                        code_2fa=code_2fa
                    )
                else:
                    # Nếu không phải 2FA chuẩn, thì đoạn thứ 3 chỉ là text copy thừa -> Ép về format Capcut
                    template = templates.get("capcut", DEFAULT_TEMPLATES.get("capcut", ""))
                    return template.format(
                        email=email_formatted,
                        password=password
                    )

    # ========================================================
    # ĐỊNH DẠNG 3: NETFLIX (Email + Username cách nhau khoảng trắng/tab)
    # ========================================================
    tokens = re.split(r'[\s\t,;]+', text)
    username_found = None

    for token in tokens:
        clean_token = token.strip()
        if clean_token == email_prefix:
            username_found = clean_token
            break

    if username_found:
        template = templates.get("netflix", DEFAULT_TEMPLATES.get("netflix", ""))
        pass_val = templates.get("netflix_default_pass", "1234567891011")
        return template.format(
            email=email_formatted,
            username=username_found,
            password=pass_val
        )

    return None

# ==========================================
# 2. THƯ VIỆN VẼ UI BO GÓC CHUYÊN NGHIỆP (CANVAS)
# ==========================================

def get_rounded_rect_coords(x1, y1, x2, y2, radius=10):
    return [
        x1 + radius, y1,
        x2 - radius, y1,
        x2, y1,
        x2, y1 + radius,
        x2, y2 - radius,
        x2, y2,
        x2 - radius, y2,
        x1 + radius, y2,
        x1, y2,
        x1, y2 - radius,
        x1, y1 + radius,
        x1, y1
    ]

def draw_rounded_rect(canvas, x1, y1, x2, y2, radius=10, **kwargs):
    points = get_rounded_rect_coords(x1, y1, x2, y2, radius)
    return canvas.create_polygon(points, smooth=True, **kwargs)

# ==========================================
# 2.1 CỬA SỔ ĐĂNG NHẬP TELEGRAM TRỰC TIẾP TRONG APP
# ==========================================

class TelegramLoginModal:
    def __init__(self, parent_app):
        self.app = parent_app
        self.top = tk.Toplevel(self.app.root)
        self.top.title("Đăng nhập Telegram")
        self.top.geometry("380x250")
        self.top.configure(bg="#11151E")
        self.top.resizable(False, False)
        self.top.transient(self.app.root)
        self.top.grab_set()

        self.step = "PHONE"
        self.phone_val = ""
        self.phone_code_hash = None

        tk.Label(
            self.top, text="Kết nối tài khoản Telegram",
            bg="#11151E", fg="#F3F4F6", font=("Arial", 15, "bold")
        ).pack(pady=(20, 6))

        self.lbl_desc = tk.Label(
            self.top, text="Nhập số điện thoại của bạn (VD: +84912345678):",
            bg="#11151E", fg="#8A94A6", font=("Arial", 12)
        )
        self.lbl_desc.pack(pady=(0, 12))

        self.entry_var = tk.StringVar()
        self.entry = tk.Entry(
            self.top, textvariable=self.entry_var,
            bg="#181D27", fg="#F3F4F6", insertbackground="#F3F4F6",
            font=("Arial", 14), bd=1, relief="solid", justify="center"
        )
        self.entry.pack(ipady=6, ipadx=10, padx=36, fill="x")
        self.entry.focus_set()
        self.entry.bind("<Return>", lambda e: self.on_submit())

        self.lbl_err = tk.Label(
            self.top, text="", bg="#11151E", fg="#EF4444", font=("Arial", 11)
        )
        self.lbl_err.pack(pady=(6, 6))

        self.btn = tk.Button(
            self.top, text="Gửi mã xác nhận", command=self.on_submit,
            bg="#1D4ED8", fg="#FFFFFF", activebackground="#2563EB",
            activeforeground="#FFFFFF", font=("Arial", 13, "bold"),
            bd=0, padx=16, pady=8, cursor="hand2"
        )
        self.btn.pack(pady=(2, 12))

    def set_ui_state(self, loading, err_text=""):
        self.lbl_err.config(text=err_text)
        if loading:
            self.entry.config(state="disabled")
            self.btn.config(state="disabled", text="Đang xử lý...")
        else:
            self.entry.config(state="normal")
            self.btn.config(state="normal")
            if self.step == "PHONE":
                self.btn.config(text="Gửi mã xác nhận")
            elif self.step == "OTP":
                self.btn.config(text="Xác nhận mã OTP")
            else:
                self.btn.config(text="Xác nhận mật khẩu 2FA")
            self.entry.focus_set()

    def on_submit(self):
        val = self.entry_var.get().strip()
        if not val:
            self.lbl_err.config(text="Vui lòng không để trống!")
            return

        if self.step == "PHONE":
            if val.startswith("0"):
                val = "+84" + val[1:]
            elif not val.startswith("+"):
                val = "+" + val
            self.phone_val = val
            self.set_ui_state(True)
            threading.Thread(target=self._send_code_worker, daemon=True).start()
        elif self.step == "OTP":
            self.set_ui_state(True)
            threading.Thread(target=self._verify_otp_worker, args=(val,), daemon=True).start()
        elif self.step == "2FA":
            self.set_ui_state(True)
            threading.Thread(target=self._verify_2fa_worker, args=(val,), daemon=True).start()

    def _send_code_worker(self):
        try:
            api_id = int(os.environ.get("TG_API_ID", 0))
            api_hash = os.environ.get("TG_API_HASH", "")
            session = get_session_path()

            async def _req():
                client = TelegramClient(session, api_id, api_hash)
                await client.connect()
                try:
                    sent = await client.send_code_request(self.phone_val)
                    return sent.phone_code_hash
                finally:
                    await client.disconnect()

            code_hash = asyncio.run(_req())
            self.phone_code_hash = code_hash

            def _next():
                self.step = "OTP"
                self.entry_var.set("")
                self.lbl_desc.config(text="Nhập mã OTP gồm 5 chữ số gửi về Telegram:")
                self.set_ui_state(False)

            self.top.after(0, _next)
        except Exception as e:
            self.top.after(0, lambda: self.set_ui_state(False, f"Lỗi SĐT: {str(e)[:45]}"))

    def _verify_otp_worker(self, otp_code):
        try:
            api_id = int(os.environ.get("TG_API_ID", 0))
            api_hash = os.environ.get("TG_API_HASH", "")
            session = get_session_path()

            async def _sign_in():
                client = TelegramClient(session, api_id, api_hash)
                await client.connect()
                try:
                    await client.sign_in(
                        phone=self.phone_val,
                        code=otp_code,
                        phone_code_hash=self.phone_code_hash
                    )
                    return "OK"
                except SessionPasswordNeededError:
                    return "NEED_2FA"
                finally:
                    await client.disconnect()

            res = asyncio.run(_sign_in())
            if res == "NEED_2FA":
                def _ask_2fa():
                    self.step = "2FA"
                    self.entry_var.set("")
                    self.entry.config(show="*")
                    self.lbl_desc.config(text="Tài khoản có bảo mật 2 lớp. Nhập mật khẩu 2FA:")
                    self.set_ui_state(False)
                self.top.after(0, _ask_2fa)
            else:
                self.top.after(0, self._on_success)
        except Exception as e:
            self.top.after(0, lambda: self.set_ui_state(False, f"Sai mã OTP: {str(e)[:40]}"))

    def _verify_2fa_worker(self, password):
        try:
            api_id = int(os.environ.get("TG_API_ID", 0))
            api_hash = os.environ.get("TG_API_HASH", "")
            session = get_session_path()

            async def _sign_2fa():
                client = TelegramClient(session, api_id, api_hash)
                await client.connect()
                try:
                    await client.sign_in(password=password)
                finally:
                    await client.disconnect()

            asyncio.run(_sign_2fa())
            self.top.after(0, self._on_success)
        except Exception as e:
            self.top.after(0, lambda: self.set_ui_state(False, f"Sai mật khẩu 2FA: {str(e)[:38]}"))

    def _on_success(self):
        self.app.play_sound()
        self.app.check_telegram_connection()
        self.app.set_banner_status("Đăng nhập thành công!", "Tài khoản Telegram đã sẵn sàng lấy code", mode="success")
        self.top.destroy()


# ==========================================
# 3. CLASS CHÍNH CỦA ỨNG DỤNG
# ==========================================

class ModernNetflixApp:
    BG_WINDOW = "#0D1016"
    BG_OUTER_PANEL = "#151922"
    BORDER_OUTER = "#232936"
    
    BG_CARD = "#11151E"
    BORDER_CARD = "#222734"
    
    BG_INPUT = "#181D27"
    BORDER_INPUT = "#2E3545"
    BORDER_ACTIVE = "#3B82F6"
    
    TEXT_WHITE = "#F3F4F6"
    TEXT_MUTED = "#6E788C"
    TEXT_GREEN = "#10B981"
    
    BTN_BG_INACTIVE = "#242B3A"
    BTN_TXT_INACTIVE = "#8A94A6"
    BTN_BG_ACTIVE = "#1D4ED8"
    BTN_BG_HOVER = "#2563EB"
    
    BADGE_WHITE = "#F9FAFB"
    BADGE_TEXT = "#111827"

    def __init__(self, root):
        self.root = root
        self.root.title("Netflix Vèo Vèo")
        self.root.geometry("880x457")
        self.root.withdraw()
        self.root.resizable(True, True)
        self.root.configure(bg=self.BG_WINDOW)

        self.cmd_queue = queue.Queue()
        self.ready_session = None
        self._cskh_timer = None
        self._tv_timer = None

        self.show_chrome = False
        self.code_enabled = False
        self.tv_placeholder_active = True
        self.cskh_placeholder_active = True
        self.getcode_placeholder_active = True
        self.show_getcode = False
        self.tg_authorized = False

        self._build_canvas_ui()
        self.root.after(250, self.check_telegram_connection)
        threading.Thread(target=self._run_async_worker, daemon=True).start()
        
        self.timer_seconds = 10 * 60
        self._timer_after_id = None
        self._timer_after_id = self.root.after(1000, self._tick_spam_timer)

        # Bắt sự kiện bấm nút X đỏ để đóng sạch trình duyệt ngầm
        self.root.protocol("WM_DELETE_WINDOW", self._on_app_close)

    def _on_app_close(self):
        try:
            self.cmd_queue.put({"action": "quit"})
        except Exception:
            pass
        self.root.after(200, self.root.destroy)

    def _build_canvas_ui(self):
        self.cv = tk.Canvas(
            self.root, width=880, height=549,
            bg=self.BG_WINDOW, highlightthickness=0, bd=0
        )
        self.cv.pack(fill="both", expand=True)

        self.outer_panel = draw_rounded_rect(
            self.cv, 14, 14, 866, 535, radius=24,
            fill=self.BG_OUTER_PANEL, outline=self.BORDER_OUTER, width=1.5
        )

        # 1. KHỐI TRÊN TRÁI: Chuẩn hoá (CSKH)
        draw_rounded_rect(
            self.cv, 36, 36, 306, 260, radius=14,
            fill=self.BG_CARD, outline=self.BORDER_CARD, width=1.5
        )
        self.cskh_title_text = self.cv.create_text(
            52, 66, text="Format DATA",
            fill=self.TEXT_WHITE, font=("Arial", 16, "bold"), anchor="w"
        )

        self.cskh_gear_icon = self.cv.create_text(
            278, 66, text="⚙",
            fill=self.TEXT_MUTED, font=("Arial", 22), anchor="center"
        )
        gear_hit = self.cv.create_rectangle(260, 48, 296, 84, fill="", outline="")
        for itm in (self.cskh_gear_icon, gear_hit):
            self.cv.tag_bind(itm, "<Button-1>", lambda e: self._open_template_settings())
            self.cv.tag_bind(itm, "<Enter>", lambda e: self.cv.itemconfig(self.cskh_gear_icon, fill=self.TEXT_WHITE))
            self.cv.tag_bind(itm, "<Leave>", lambda e: self.cv.itemconfig(self.cskh_gear_icon, fill=self.TEXT_MUTED))

        self.cskh_input_box = draw_rounded_rect(
            self.cv, 52, 92, 290, 242, radius=10,
            fill=self.BG_INPUT, outline=self.BORDER_INPUT, width=1.5
        )
        self.cskh_input_text = tk.Text(
            self.cv, bg=self.BG_INPUT, fg=self.TEXT_MUTED,
            insertbackground=self.BG_INPUT, font=("Arial", 14), 
            bd=0, highlightthickness=0, wrap="word"
        )
        self.cskh_input_text.bind("<Button-1>", lambda e: self.cskh_input_text.focus_set())
        self.cskh_input_text.bind("<FocusIn>", self._on_cskh_focus_in)
        self.cskh_input_text.bind("<FocusOut>", self._on_cskh_focus_out)
        self.cskh_input_text.bind("<KeyPress>", self._block_typing_only_paste)
        self.cskh_input_text.bind("<<Paste>>", self._on_cskh_paste)
        self.cskh_input_text.bind("<Command-v>", self._on_cskh_paste)
        self.cskh_input_text.bind("<Control-v>", self._on_cskh_paste)
        self.cv.create_window(64, 104, window=self.cskh_input_text, width=214, height=126, anchor="nw")
        self.cv.tag_bind(self.cskh_input_box, "<Button-1>", lambda e: self.cskh_input_text.focus_set())

        # 2. KHỐI TRÊN PHẢI: Log CODE (TV8) & Icon Kính Soi ⌕
        draw_rounded_rect(
            self.cv, 322, 36, 844, 260, radius=14,
            fill=self.BG_CARD, outline=self.BORDER_CARD, width=1.5
        )
        self.cv.create_text(
            338, 66, text="Log CODE",
            fill=self.TEXT_WHITE, font=("Arial", 16, "bold"), anchor="w"
        )

        self.chk_eye_icon = self.cv.create_text(
            815, 66, text="⌕",
            fill=self.TEXT_MUTED, font=("Arial", 30), anchor="center"
        )
        eye_hit = self.cv.create_rectangle(797, 48, 833, 84, fill="", outline="")
        for item in (self.chk_eye_icon, eye_hit):
            self.cv.tag_bind(item, "<Button-1>", self._toggle_chrome_checkbox)
            self.cv.tag_bind(item, "<Enter>", lambda e: self.cv.itemconfig(
                self.chk_eye_icon, fill="#38BDF8" if self.show_chrome else self.TEXT_WHITE
            ))
            self.cv.tag_bind(item, "<Leave>", lambda e: self.cv.itemconfig(
                self.chk_eye_icon, fill="#3B82F6" if self.show_chrome else self.TEXT_MUTED
            ))

        self.tv_input_box = draw_rounded_rect(
            self.cv, 338, 92, 576, 242, radius=10,
            fill=self.BG_INPUT, outline=self.BORDER_INPUT, width=1.5
        )
        self.tv_input_text = tk.Text(
            self.cv, bg=self.BG_INPUT, fg=self.TEXT_MUTED,
            insertbackground=self.BG_INPUT, font=("Arial", 14),
            bd=0, highlightthickness=0, wrap="word"
        )
        self.tv_input_text.bind("<Button-1>", lambda e: self.tv_input_text.focus_set())
        self.tv_input_text.bind("<FocusIn>", self._on_tv_focus_in)
        self.tv_input_text.bind("<FocusOut>", self._on_tv_focus_out)
        self.tv_input_text.bind("<KeyPress>", self._block_typing_only_paste)
        self.tv_input_text.bind("<<Paste>>", self._on_tv_paste)
        self.tv_input_text.bind("<Command-v>", self._on_tv_paste)
        self.tv_input_text.bind("<Control-v>", self._on_tv_paste)
        self.cv.create_window(350, 104, window=self.tv_input_text, width=214, height=126, anchor="nw")
        self.cv.tag_bind(self.tv_input_box, "<Button-1>", lambda e: self.tv_input_text.focus_set())

        self.code_box_rect = draw_rounded_rect(
            self.cv, 592, 92, 828, 158, radius=10,
            fill=self.BG_INPUT, outline=self.BORDER_INPUT, width=1.5
        )
        self.code_var = tk.StringVar()
        self.code_entry = tk.Entry(
            self.cv, textvariable=self.code_var,
            bg=self.BG_INPUT, fg=self.TEXT_WHITE,
            disabledbackground=self.BG_INPUT, disabledforeground=self.TEXT_MUTED,
            insertbackground=self.TEXT_WHITE,
            font=("Arial", 14), bd=0, highlightthickness=0, state="disabled"
        )
        self.code_var.set("Nhập code ạ...")
        self.code_entry.bind("<Return>", lambda e: self.submit_tv_code())
        self.code_entry.bind("<KeyPress>", self._handle_code_keypress)
        self.code_entry.bind("<<Paste>>", self._on_code_paste)
        self.code_entry.bind("<Command-v>", self._on_code_paste)
        self.code_entry.bind("<Control-v>", self._on_code_paste)
        self.code_var.trace_add("write", self._validate_code_input)
        self.cv.create_window(606, 112, window=self.code_entry, width=208, height=28, anchor="nw")

        self.submit_btn_rect = draw_rounded_rect(
            self.cv, 592, 174, 828, 242, radius=10,
            fill=self.BTN_BG_INACTIVE, outline=""
        )
        self.submit_btn_text = self.cv.create_text(
            710, 208, text="Log in",
            fill=self.BTN_TXT_INACTIVE, font=("Arial", 14, "bold")
        )
        submit_hit = self.cv.create_rectangle(592, 174, 828, 242, fill="", outline="")
        for item in (self.submit_btn_rect, self.submit_btn_text, submit_hit):
            self.cv.tag_bind(item, "<Button-1>", lambda e: self.submit_tv_code())
            self.cv.tag_bind(item, "<Enter>", self._on_submit_hover_in)
            self.cv.tag_bind(item, "<Leave>", self._on_submit_hover_out)

        # 3. KHỐI GIỮA: Get CODE (Telegram OTP) & Telegram Status
        self.getcode_card = draw_rounded_rect(
            self.cv, 36, 278, 844, 420, radius=14,
            fill=self.BG_CARD, outline=self.BORDER_CARD, width=1.5
        )
        
        self.toggle_icon = self.cv.create_text(
            52, 303, text="▶",
            fill=self.TEXT_WHITE, font=("Arial", 13), anchor="w"
        )
        self.getcode_title_label = self.cv.create_text(
            74, 303, text="Get CODE",
            fill=self.TEXT_WHITE, font=("Arial", 16, "bold"), anchor="w"
        )
        self.telegram_status = self.cv.create_text(
            824, 303, text="Telegram: Đang kiểm tra...",
            fill=self.TEXT_GREEN, font=("Arial", 14, "bold"), anchor="e"
        )
        self.cv.tag_bind(self.telegram_status, "<Button-1>", self._on_telegram_status_click)

        toggle_hit = self.cv.create_rectangle(36, 278, 520, 328, fill="", outline="")
        for item in (self.toggle_icon, self.getcode_title_label, toggle_hit):
            self.cv.tag_bind(item, "<Button-1>", self._toggle_getcode)

        self.getcode_input_rect = draw_rounded_rect(
            self.cv, 52, 336, 576, 402, radius=10,
            fill=self.BG_INPUT, outline=self.BORDER_INPUT, width=1.5,
            tags=("getcode_content",)
        )
        self.getcode_email = tk.StringVar()
        self.getcode_entry = tk.Entry(
            self.cv, textvariable=self.getcode_email, bg=self.BG_INPUT,
            fg=self.TEXT_MUTED, insertbackground=self.BG_INPUT,
            font=("Arial", 14), bd=0, highlightthickness=0
        )
        self.getcode_entry.insert(0, "Nhập email ạ...")
        self.getcode_entry.bind("<FocusIn>", self._getcode_focus_in)
        self.getcode_entry.bind("<FocusOut>", self._getcode_focus_out)
        self.getcode_entry.bind("<KeyPress>", self._getcode_keypress)
        self.getcode_entry.bind("<<Paste>>", self._getcode_paste)
        self.getcode_entry.bind("<Command-v>", self._getcode_paste)
        self.getcode_entry.bind("<Control-v>", self._getcode_paste)
        self.getcode_entry.bind("<Button-2>", lambda e: "break")
        self.getcode_entry_win = self.cv.create_window(
            68, 355, window=self.getcode_entry, width=405, height=28, anchor="nw"
        )

        self.otp_badge_rect = draw_rounded_rect(
            self.cv, 484, 351, 560, 387, radius=8,
            fill=self.BADGE_WHITE, outline="",
            tags=("getcode_content",)
        )
        self.otp_badge_txt = self.cv.create_text(
            522, 369, text="______",
            fill=self.BADGE_TEXT, font=("Arial", 12, "bold"),
            tags=("getcode_content",)
        )

        self.getcode_button_rect = draw_rounded_rect(
            self.cv, 592, 336, 828, 402, radius=10,
            fill=self.BTN_BG_INACTIVE, outline="",
            tags=("getcode_content",)
        )
        self.getcode_button_label = self.cv.create_text(
            710, 369, text="Get code", fill=self.BTN_TXT_INACTIVE,
            font=("Arial", 14, "bold"),
            tags=("getcode_content",)
        )
        getcode_button_hit = self.cv.create_rectangle(
            592, 336, 828, 402, fill="", outline="",
            tags=("getcode_content",)
        )
        for item in (self.getcode_button_rect, self.getcode_button_label, getcode_button_hit):
            self.cv.tag_bind(item, "<Button-1>", self._getcode_submit)
            self.cv.tag_bind(item, "<Enter>", lambda e: self.cv.itemconfig(
                self.getcode_button_rect, fill="#2E374A" if not getattr(self, "_getcode_running", False) else "#D97706"))
            self.cv.tag_bind(item, "<Leave>", lambda e: self.cv.itemconfig(
                self.getcode_button_rect, fill=self.BTN_BG_INACTIVE if not getattr(self, "_getcode_running", False) else "#D97706"))

        # 4. FOOTER: STATUS / ALERT BAR & THỜI GIAN SPAM (10P)
        self.status_bar_rect = draw_rounded_rect(
            self.cv, 36, 438, 740, 510, radius=12,
            fill="#153524", outline="#245C3D", width=1.5
        )
        self.status_icon_bg = self.cv.create_oval(52, 456, 88, 492, fill="#22C55E", outline="")
        self.status_icon_txt = self.cv.create_text(70, 474, text="✓", fill="#FFFFFF", font=("Arial", 14, "bold"))
        
        self.status_left_txt = self.cv.create_text(
            104, 474, text="Sẵn sàng",
            fill="#FFFFFF", font=("Arial", 14, "bold"), anchor="w"
        )
        self.status_right_txt = self.cv.create_text(
            726, 474, text="Xin chào tới với Netflix Vèo Vèo!",
            fill="#E5E7EB", font=("Arial", 14), anchor="e"
        )

        self.timer_box_rect = draw_rounded_rect(
            self.cv, 750, 438, 844, 510, radius=12,
            fill=self.BG_CARD, outline=self.BORDER_CARD, width=1.5
        )
        self.timer_title_txt = self.cv.create_text(
            797, 458, text="SPAM",
            fill=self.TEXT_MUTED, font=("Arial", 14)
        )
        self.timer_box_txt = self.cv.create_text(
            797, 482, text="10:00",
            fill=self.TEXT_WHITE, font=("Arial", 14, "bold")
        )
        timer_hit = self.cv.create_rectangle(750, 438, 844, 510, fill="", outline="")
        for itm in (self.timer_box_rect, self.timer_title_txt, self.timer_box_txt, timer_hit):
            self.cv.tag_bind(itm, "<Button-1>", lambda e: self._reset_spam_timer())

        # Nạp placeholder và đưa ô text lên trên cùng
        self.cskh_input_text.delete("1.0", "end")
        self.cskh_input_text.insert("1.0", "Nhập thông tin ạ...")
        self.cskh_input_text.config(fg=self.TEXT_MUTED)
        self.cskh_placeholder_active = True

        self.tv_input_text.delete("1.0", "end")
        self.tv_input_text.insert("1.0", "Nhập link ạ...")
        self.tv_input_text.config(fg=self.TEXT_MUTED)
        self.tv_placeholder_active = True

        self.cskh_input_text.lift()
        self.tv_input_text.lift()

        self._apply_getcode_state()
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w, h = 880, 457
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2)
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.deiconify()
        self.root.lift()

    def _toggle_getcode(self, event=None):
        self.show_getcode = not self.show_getcode
        self._apply_getcode_state()

    def _apply_getcode_state(self):
        offset = 92
        if self.show_getcode:
            self.cv.itemconfig(self.toggle_icon, text="▼")
            self.cv.itemconfigure("getcode_content", state="normal")
            self.cv.itemconfigure(self.getcode_entry_win, state="normal")
            self.cv.coords(self.getcode_card, *get_rounded_rect_coords(36, 278, 844, 420, 14))
            self.cv.coords(self.outer_panel, *get_rounded_rect_coords(14, 14, 866, 535, 24))
            self.cv.coords(self.status_bar_rect, *get_rounded_rect_coords(36, 438, 740, 510, 12))
            self.cv.coords(self.status_icon_bg, 52, 456, 88, 492)
            self.cv.coords(self.status_icon_txt, 70, 474)
            self.cv.coords(self.status_left_txt, 104, 474)
            self.cv.coords(self.status_right_txt, 726, 474)
            self.cv.coords(self.timer_box_rect, *get_rounded_rect_coords(750, 438, 844, 510, 12))
            self.cv.coords(self.timer_title_txt, 797, 458)
            self.cv.coords(self.timer_box_txt, 797, 482)
            self.root.geometry("880x549")
        else:
            self.cv.itemconfig(self.toggle_icon, text="▶")
            self.cv.itemconfigure("getcode_content", state="hidden")
            self.cv.itemconfigure(self.getcode_entry_win, state="hidden")
            self.cv.coords(self.getcode_card, *get_rounded_rect_coords(36, 278, 844, 328, 14))
            self.cv.coords(self.outer_panel, *get_rounded_rect_coords(14, 14, 866, 443, 24))
            self.cv.coords(self.status_bar_rect, *get_rounded_rect_coords(36, 438 - offset, 740, 510 - offset, 12))
            self.cv.coords(self.status_icon_bg, 52, 456 - offset, 88, 492 - offset)
            self.cv.coords(self.status_icon_txt, 70, 474 - offset)
            self.cv.coords(self.status_left_txt, 104, 474 - offset)
            self.cv.coords(self.status_right_txt, 726, 474 - offset)
            self.cv.coords(self.timer_box_rect, *get_rounded_rect_coords(750, 438 - offset, 844, 510 - offset, 12))
            self.cv.coords(self.timer_title_txt, 797, 458 - offset)
            self.cv.coords(self.timer_box_txt, 797, 482 - offset)
            self.root.geometry("880x457")

    # ==========================================
    # QUẢN LÝ BỘ ĐẾM THỜI GIAN SPAM (10 PHÚT)
    # ==========================================

    def _reset_spam_timer(self):
        if self._timer_after_id is not None:
            try:
                self.root.after_cancel(self._timer_after_id)
            except Exception:
                pass
            self._timer_after_id = None
        self.timer_seconds = 10 * 60
        mins, secs = divmod(self.timer_seconds, 60)
        self.cv.itemconfig(self.timer_box_txt, text=f"{mins:02d}:{secs:02d}", fill=self.TEXT_WHITE, font=("Arial", 14, "bold"))
        self.cv.itemconfig(self.timer_title_txt, text="SPAM", fill=self.TEXT_MUTED, font=("Arial", 14))
        self.cv.itemconfig(self.timer_box_rect, outline=self.BORDER_CARD)
        self.set_banner_status("Sẵn sàng", "Đã khởi động lại Thời gian Spam (10p)", mode="success")
        self._timer_after_id = self.root.after(1000, self._tick_spam_timer)

    def _tick_spam_timer(self):
        if self._timer_after_id is not None:
            try:
                self.root.after_cancel(self._timer_after_id)
            except Exception:
                pass
            self._timer_after_id = None

        self.timer_seconds -= 1
        if self.timer_seconds > 0:
            mins, secs = divmod(self.timer_seconds, 60)
            self.cv.itemconfig(
                self.timer_box_txt,
                text=f"{mins:02d}:{secs:02d}",
                fill=self.TEXT_WHITE,
                font=("Arial", 14, "bold")
            )
            self._timer_after_id = self.root.after(1000, self._tick_spam_timer)
        else:
            self._timer_after_id = None
            self.play_sound()
            self.set_banner_status("SPAM", "Hoàn nhắc bạn Spam nha :))", mode="loading")
            self.cv.itemconfig(self.timer_box_txt, text="↻", fill="#38BDF8", font=("Arial", 20, "bold"))
            self.cv.itemconfig(self.timer_title_txt, text="Click Reset", fill="#38BDF8")
            self.cv.itemconfig(self.timer_box_rect, outline="#38BDF8")

    # ==========================================
    # CỬA SỔ CÀI ĐẶT ĐỊNH DẠNG (POPUP)
    # ==========================================

    def _open_template_settings(self):
        """Popup cài đặt định dạng Format DATA chuẩn theo UI Card + nút Apply trắng"""
        templates = load_custom_templates()

        popup_schemas = [
            {
                "id": "capcut",
                "left_title": "1. Capcut format",
                "right_tag": "EMAIL + PASS",
                "default": templates.get("capcut", DEFAULT_TEMPLATES["capcut"])
            },
            {
                "id": "netflix",
                "left_title": "2. Netflix format",
                "right_tag": "EMAIL + USERNAME",
                "default": templates.get("netflix", DEFAULT_TEMPLATES["netflix"])
            },
            {
                "id": "chatgpt",
                "left_title": "3. ChatGPT format",
                "right_tag": "EMAIL + PASS + CODE",
                "default": templates.get("chatgpt", DEFAULT_TEMPLATES["chatgpt"])
            }
        ]

        top = tk.Toplevel(self.root)
        top.title("Format DATA")
        top.resizable(False, False)
        top.configure(bg=self.BG_WINDOW)
        top.withdraw()
        top.grab_set()

        pcv = tk.Canvas(top, width=460, bg=self.BG_WINDOW, highlightthickness=0, bd=0)
        pcv.pack(fill="both", expand=True)

        current_y = 24
        text_widgets = {}
        LINE_H = 17
        CARD_LEFT = 24
        CARD_RIGHT = 436
        INNER_LEFT = 40
        INNER_RIGHT = 420

        for item in popup_schemas:
            fid = item["id"]
            content = item["default"]
            line_count = max(content.count("\n") + 1, 3)
            inner_box_h = line_count * LINE_H + 16
            card_h = 38 + inner_box_h + 16
            card_bottom = current_y + card_h

            draw_rounded_rect(
                pcv, CARD_LEFT, current_y, CARD_RIGHT, card_bottom, radius=14,
                fill=self.BG_CARD, outline=self.BORDER_CARD, width=1.5
            )

            pcv.create_text(
                INNER_LEFT, current_y + 22,
                text=item["left_title"], fill=self.TEXT_WHITE,
                font=("Arial", 14, "bold"), anchor="w"
            )
            pcv.create_text(
                INNER_RIGHT, current_y + 22,
                text=item["right_tag"], fill="#6E788C",
                font=("Arial", 11, "bold"), anchor="e"
            )

            box_top = current_y + 38
            box_bottom = box_top + inner_box_h
            inner_box_rect = draw_rounded_rect(
                pcv, INNER_LEFT, box_top, INNER_RIGHT, box_bottom, radius=10,
                fill=self.BG_INPUT, outline=self.BORDER_INPUT, width=1.5
            )

            txt = tk.Text(
                pcv, bg=self.BG_INPUT, fg=self.TEXT_WHITE,
                insertbackground=self.TEXT_WHITE, font=("Arial", 12),
                bd=0, highlightthickness=0, wrap="word"
            )
            txt.insert("1.0", content)
            pcv.create_window(INNER_LEFT + 10, box_top + 8, window=txt, width=(INNER_RIGHT - INNER_LEFT - 20), height=inner_box_h - 16, anchor="nw")

            txt.bind("<FocusIn>", lambda e, r=inner_box_rect: pcv.itemconfig(r, outline=self.BORDER_ACTIVE))
            txt.bind("<FocusOut>", lambda e, r=inner_box_rect: pcv.itemconfig(r, outline=self.BORDER_INPUT))

            text_widgets[fid] = txt
            current_y = card_bottom + 16

        def _on_apply():
            new_data = {fid: text_widgets[fid].get("1.0", "end").strip() for fid in text_widgets}
            save_custom_templates(new_data)
            self.set_banner_status("Đã lưu định dạng", "Cấu hình Format DATA mới đã có hiệu lực", mode="success")
            top.destroy()

        btn_y = current_y + 6
        btn_w = 170
        btn_h = 44
        btn_x1 = (460 - btn_w) // 2
        btn_x2 = btn_x1 + btn_w

        btn_rect = draw_rounded_rect(
            pcv, btn_x1, btn_y, btn_x2, btn_y + btn_h, radius=10,
            fill="#FFFFFF", outline=""
        )
        btn_txt = pcv.create_text(
            230, btn_y + btn_h // 2, text="Apply",
            fill="#111827", font=("Arial", 14, "bold")
        )
        btn_hit = pcv.create_rectangle(btn_x1, btn_y, btn_x2, btn_y + btn_h, fill="", outline="")

        for itm in (btn_rect, btn_txt, btn_hit):
            pcv.tag_bind(itm, "<Button-1>", lambda e: _on_apply())
            pcv.tag_bind(itm, "<Enter>", lambda e: pcv.itemconfig(btn_rect, fill="#E5E7EB"))
            pcv.tag_bind(itm, "<Leave>", lambda e: pcv.itemconfig(btn_rect, fill="#FFFFFF"))

        total_height = btn_y + btn_h + 24
        pcv.config(height=total_height)
        top.update_idletasks()
        screen_w = top.winfo_screenwidth()
        screen_h = top.winfo_screenheight()
        pos_x = (screen_w - 460) // 2
        pos_y = (screen_h - total_height) // 2

        top.geometry(f"460x{total_height}+{pos_x}+{pos_y}")
        top.deiconify()
        top.lift()

    # ==========================================
    # XỬ LÝ SỰ KIỆN GET CODE (TELEGRAM)
    # ==========================================

    def _on_telegram_status_click(self, event=None):
        if not self.tg_authorized:
            TelegramLoginModal(self)
        else:
            self.check_telegram_connection()

    def _getcode_focus_in(self, event=None):
        self.cv.itemconfig(self.getcode_input_rect, outline=self.BORDER_ACTIVE)
        if self.getcode_placeholder_active:
            self.getcode_entry.delete(0, "end")
            self.getcode_entry.configure(fg=self.TEXT_WHITE)
            self.getcode_placeholder_active = False

    def _getcode_focus_out(self, event=None):
        self.cv.itemconfig(self.getcode_input_rect, outline=self.BORDER_INPUT)
        if not self.getcode_email.get().strip():
            self.getcode_placeholder_active = True
            self.getcode_entry.delete(0, "end")
            self.getcode_entry.insert(0, "Nhập email ạ...")
            self.getcode_entry.configure(fg=self.TEXT_MUTED)

    def _getcode_keypress(self, event):
        if event.keysym == "Return":
            self._getcode_submit()
            return "break"
        modifier = event.state & 0xC
        if event.keysym.lower() == "v" and modifier:
            self._getcode_paste()
            return "break"
        if event.keysym in ("Left", "Right", "Home", "End", "Tab", "ISO_Left_Tab"):
            return None
        return "break"

    def _getcode_paste(self, event=None):
        try:
            raw_pasted = self.root.clipboard_get()
        except tk.TclError:
            raw_pasted = ""

        # Dùng Regex tìm chính xác email (chấp nhận cả dấu +, -, ., _)
        email_match = re.search(r'([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)', raw_pasted)
        if email_match:
            clean_email = email_match.group(1).strip()
        else:
            cleaned = re.sub(r"^/getcode\s*", "", raw_pasted.strip(), flags=re.I)
            clean_email = re.sub(r"\s+", "", cleaned)

        self.getcode_entry.delete(0, "end")
        self.getcode_entry.insert(0, clean_email)
        self.getcode_entry.configure(fg=self.TEXT_WHITE)
        self.getcode_placeholder_active = False

        if clean_email:
            self.root.after(80, self._getcode_submit)
        return "break"

    def _getcode_submit(self, event=None):
        if getattr(self, "_getcode_running", False):
            return "break"

        if not self.tg_authorized:
            self.set_banner_status("Chưa kết nối Telegram", "Hãy đăng nhập Telegram ở cửa sổ vừa bật", mode="error")
            TelegramLoginModal(self)
            return "break"

        raw_val = self.getcode_email.get()
        if self.getcode_placeholder_active or raw_val == "Nhập email ạ...":
            self.set_banner_status("Email không hợp lệ", "Vui lòng kiểm tra lại chuỗi vừa dán", mode="error")
            return "break"

        # Tự động lọc lấy email chuẩn kể cả khi ô chứa text thừa
        email_match = re.search(r'([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)', raw_val)
        if email_match:
            email = email_match.group(1).strip()
        else:
            email = ""

        self.getcode_entry.delete(0, "end")
        self.getcode_entry.insert(0, email)

        if not email:
            self.cv.itemconfig(self.otp_badge_txt, text="ERR", fill="#EF4444")
            self.set_banner_status("Email không hợp lệ", "Vui lòng kiểm tra lại chuỗi vừa dán", mode="error")
            return "break"

        self._getcode_running = True
        self.cv.itemconfig(self.otp_badge_txt, text="...", fill=self.BADGE_TEXT)
        self.cv.itemconfig(self.getcode_button_label, text="Đang lấy...", fill="#FFFFFF")
        self.cv.itemconfig(self.getcode_button_rect, fill="#D97706")
        self.set_banner_status("Đang gửi lệnh Telegram", f"/getcode {email}", mode="loading")

        threading.Thread(target=self._fetch_code_from_bot_thread, args=(email,), daemon=True).start()
        return "break"

    def _fetch_code_from_bot_thread(self, email: str):
        try:
            api_id = int(os.environ.get("TG_API_ID", 0))
            api_hash = os.environ.get("TG_API_HASH", "")
            session = get_session_path()
            bot_target = os.environ.get("TG_BOT_USERNAME", "").strip()

            if not bot_target:
                self.root.after(0, lambda: self._finish_getcode(None, "Thiếu TG_BOT_USERNAME trong cấu hình"))
                return

            if bot_target.lstrip("-").isdigit():
                bot_target = int(bot_target)

            async def _communicate():
                client = TelegramClient(session, api_id, api_hash, timeout=10)
                try:
                    await asyncio.wait_for(client.connect(), timeout=8.0)
                    if not await client.is_user_authorized():
                        self.tg_authorized = False
                        return None, "Phiên Telegram hết hạn, hãy bấm đăng nhập lại"

                    self.root.after(0, lambda: self.set_banner_status("Đang tìm Bot Telegram", f"{bot_target}", mode="loading"))
                    entity = await asyncio.wait_for(client.get_entity(bot_target), timeout=8.0)

                    self.root.after(0, lambda: self.set_banner_status("Đang gửi lệnh", f"/getcode {email}", mode="loading"))
                    sent_msg = await asyncio.wait_for(client.send_message(entity, f"/getcode {email}"), timeout=8.0)
                    last_id = sent_msg.id

                    start_wait = time.time()
                    while time.time() - start_wait < 20:
                        remain = int(20 - (time.time() - start_wait))
                        self.root.after(0, lambda r=remain: self.set_banner_status("Đang đợi bot trả lời...", f"Còn lại {r}s", mode="loading"))
                        await asyncio.sleep(0.35)

                        messages = await client.get_messages(entity, limit=2)
                        for msg in messages:
                            if msg.id > last_id and not msg.out and msg.message:
                                reply_text = msg.message.strip()

                                if "no new emails found" in reply_text.lower():
                                    return None, "Bot báo: Không tìm thấy email mới"

                                match = re.search(r'(?:Code\s*:\s*)?(\d{4,8})', reply_text, re.IGNORECASE)
                                if match:
                                    return match.group(1), reply_text

                                return reply_text, reply_text

                    return None, "Bot Telegram không phản hồi sau 20s"
                except asyncio.TimeoutError:
                    return None, f"Hết thời gian chờ khi kết nối tới {bot_target}"
                finally:
                    await client.disconnect()

            code_result, full_reply = asyncio.run(_communicate())
            self.root.after(0, lambda: self._finish_getcode(code_result, full_reply))

        except Exception as e:
            self.root.after(0, lambda: self._finish_getcode(None, f"Lỗi: {e}"))

    def _finish_getcode(self, code_result, detail_text):
        self._getcode_running = False
        if code_result:
            spaced_code = " ".join(list(str(code_result))) if len(str(code_result)) <= 4 else str(code_result)[:8]
            self.cv.itemconfig(self.otp_badge_txt, text=spaced_code, fill=self.BADGE_TEXT)

            self.cv.itemconfig(self.getcode_button_label, text="Get code", fill=self.TEXT_WHITE)
            self.cv.itemconfig(self.getcode_button_rect, fill=self.BTN_BG_INACTIVE)

            self.root.clipboard_clear()
            self.root.clipboard_append(str(code_result))
            self.root.update_idletasks()

            self.play_sound()
            self.set_banner_status(f"Đã lấy mã: {code_result}", "Đã tự động Copy vào Clipboard!", mode="success")
        else:
            self.cv.itemconfig(self.otp_badge_txt, text="______", fill=self.BADGE_TEXT)
            self.cv.itemconfig(self.getcode_button_label, text="Get code", fill=self.BTN_TXT_INACTIVE)
            self.cv.itemconfig(self.getcode_button_rect, fill=self.BTN_BG_INACTIVE)
            self.set_banner_status("Lấy code thất bại", detail_text, mode="error")

    def check_telegram_connection(self):
        self.cv.itemconfig(self.telegram_status, text="Telegram: Đang kiểm tra...", fill=self.TEXT_MUTED)
        threading.Thread(target=self._telegram_check_thread, daemon=True).start()

    def _telegram_check_thread(self):
        if TelegramClient is None:
            self._telegram_status_update("Telegram: Thiếu thư viện", False)
            return
        try:
            api_id = int(os.environ["TG_API_ID"])
            api_hash = os.environ["TG_API_HASH"]
            session = get_session_path()
            async def check():
                client = TelegramClient(session, api_id, api_hash)
                try:
                    await client.connect()
                    return await client.is_user_authorized()
                finally:
                    await client.disconnect()
            authorized = asyncio.run(check())
            if authorized:
                self._telegram_status_update("Telegram: Đã kết nối ✓", True)
            else:
                self._telegram_status_update("Telegram: Click để đăng nhập", False)
        except Exception:
            self._telegram_status_update("Telegram: Click để đăng nhập", False)

    def _telegram_status_update(self, message, connected):
        self.tg_authorized = connected
        self.root.after(0, lambda: self.cv.itemconfig(
            self.telegram_status, text=message,
            fill=self.TEXT_GREEN if connected else "#F59E0B"))

    # ==========================================
    # QUẢN LÝ TRẠNG THÁI GIAO DIỆN & PLACEHOLDER
    # ==========================================

    def _block_typing_only_paste(self, event):
        modifier = event.state & 0xC
        if event.keysym.lower() == "v" and modifier:
            return None
        return "break"

    def _handle_code_keypress(self, event):
        # 1. Bấm Enter thì gửi mã
        if event.keysym in ("Return", "KP_Enter"):
            self.submit_tv_code()
            return "break"

        # 2. Cho phép các phím tắt Cmd/Ctrl: A (chọn tất cả), C (copy), V (paste), X (cắt), Z (undo)
        modifier = event.state & 0xC
        if modifier and event.keysym.lower() in ("a", "c", "v", "x", "z"):
            return None

        # 3. Cho phép điều hướng, bôi đen bằng Shift + mũi tên, xóa Backspace, Delete
        if event.keysym in ("BackSpace", "Delete", "Left", "Right", "Home", "End", "Tab", "Shift_L", "Shift_R"):
            return None

        # 4. Khi gõ số: Nếu đang bôi đen thì cho phép gõ đè, nếu không bôi đen thì giới hạn tối đa 8 số
        if event.char and event.char.isdigit():
            try:
                has_selection = self.code_entry.selection_present()
            except Exception:
                has_selection = False

            if has_selection or len(self.code_var.get()) < 8:
                return None
            return "break"

        # Chặn các ký tự chữ cái hoặc ký tự đặc biệt
        return "break"

    def _toggle_chrome_checkbox(self, event=None):
        self.show_chrome = not self.show_chrome
        if self.show_chrome:
            self.cv.itemconfig(self.chk_eye_icon, fill="#3B82F6")
            self.set_banner_status("Hiện Chrome", "Trình duyệt sẽ hiển thị quá trình log CODE", mode="success")
        else:
            self.cv.itemconfig(self.chk_eye_icon, fill=self.TEXT_MUTED)
            self.set_banner_status("Ẩn Chrome", "Trình duyệt sẽ chạy ngầm", mode="success")

    def _on_tv_focus_in(self, event=None):
        self.cv.itemconfig(self.tv_input_box, outline=self.BORDER_ACTIVE)
        if self.tv_placeholder_active:
            self.tv_input_text.delete("1.0", "end")
            self.tv_input_text.config(fg=self.TEXT_WHITE)
            self.tv_placeholder_active = False

    def _on_tv_focus_out(self, event=None):
        self.cv.itemconfig(self.tv_input_box, outline=self.BORDER_INPUT)
        content = self.tv_input_text.get("1.0", "end").strip()
        if not content:
            self.tv_placeholder_active = True
            self.tv_input_text.config(fg=self.TEXT_MUTED)
            self.tv_input_text.insert("1.0", "Nhập link ạ...")

    def _on_cskh_focus_in(self, event=None):
        self.cv.itemconfig(self.cskh_input_box, outline=self.BORDER_ACTIVE)
        if self.cskh_placeholder_active:
            self.cskh_input_text.delete("1.0", "end")
            self.cskh_input_text.config(fg=self.TEXT_WHITE)
            self.cskh_placeholder_active = False

    def _on_cskh_focus_out(self, event=None):
        self.cv.itemconfig(self.cskh_input_box, outline=self.BORDER_INPUT)
        content = self.cskh_input_text.get("1.0", "end").strip()
        if not content:
            self.cskh_placeholder_active = True
            self.cskh_input_text.config(fg=self.TEXT_MUTED)
            self.cskh_input_text.insert("1.0", "Nhập thông tin ạ...")

    def _set_code_input_state(self, enabled: bool):
        self.code_enabled = enabled
        if enabled:
            self.cv.itemconfig(self.code_box_rect, outline=self.BORDER_ACTIVE)
            self.cv.itemconfig(self.submit_btn_rect, fill=self.BTN_BG_ACTIVE)
            self.cv.itemconfig(self.submit_btn_text, fill="#FFFFFF")
            self.code_entry.config(state="normal", fg=self.TEXT_WHITE)
            self.code_var.set("")
            self.code_entry.focus_set()
        else:
            self.cv.itemconfig(self.code_box_rect, outline=self.BORDER_INPUT)
            self.cv.itemconfig(self.submit_btn_rect, fill=self.BTN_BG_INACTIVE)
            self.cv.itemconfig(self.submit_btn_text, fill=self.BTN_TXT_INACTIVE)
            self.code_var.set("Nhập code ạ...")
            self.code_entry.config(state="disabled")

    def _on_submit_hover_in(self, event=None):
        if self.code_enabled:
            self.cv.itemconfig(self.submit_btn_rect, fill=self.BTN_BG_HOVER)

    def _on_submit_hover_out(self, event=None):
        if self.code_enabled:
            self.cv.itemconfig(self.submit_btn_rect, fill=self.BTN_BG_ACTIVE)

    def set_banner_status(self, main_text: str, sub_text: str = "", mode: str = "success"):
        themes = {
            "success": ("#153524", "#245C3D", "#22C55E", "✓"),
            "loading": ("#332711", "#664D1E", "#F59E0B", "..."),
            "error":   ("#38151A", "#63222A", "#FF2E38", "✕"),
        }
        bg, border, icon_bg, icon_char = themes.get(mode, themes["success"])
        self.cv.itemconfig(self.status_bar_rect, fill=bg, outline=border)
        self.cv.itemconfig(self.status_icon_bg, fill=icon_bg)
        self.cv.itemconfig(self.status_icon_txt, text=icon_char)
        self.cv.itemconfig(self.status_left_txt, text=main_text)
        self.cv.itemconfig(self.status_right_txt, text=sub_text)

    def play_sound(self):
        try:
            bundled_sound = os.path.join(BUNDLE_DIR, "alert.aiff")
            if sys.platform == "darwin":
                sound_file = bundled_sound if os.path.exists(bundled_sound) else "/System/Library/Sounds/Glass.aiff"
                subprocess.Popen(["afplay", sound_file])
            elif sys.platform == "win32":
                import winsound
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            else:
                self.root.bell()
        except Exception:
            pass

    def _on_code_paste(self, event=None):
        if not self.code_enabled:
            return "break"
        
        # 1. Ưu tiên kiểm tra xem Clipboard có chứa ảnh không (OCR Flow)
        self.set_banner_status("Đang quét Clipboard...", "Kiểm tra ảnh chụp màn hình", mode="loading")
        self.root.update_idletasks()
        
        code_from_img = extract_code_from_clipboard_image()
        if code_from_img and len(code_from_img) == 8:
            self.code_var.set(code_from_img)
            self.set_banner_status(f"Nhận diện ảnh: {code_from_img}", "Đã bóc tách mã TV thành công!", mode="success")
            self.root.after(100, self.submit_tv_code)
            return "break"

        # 2. Nếu không có ảnh hoặc OCR thất bại, quay về luồng dán Text bình thường
        try:
            raw_pasted = self.root.clipboard_get()
        except tk.TclError:
            raw_pasted = ""
            
        clean_code = re.sub(r'[^0-9]', '', raw_pasted)[:8]
        if clean_code:
            self.code_var.set(clean_code)
            self.set_banner_status("Đã dán text", f"Mã: {clean_code}", mode="success")
            if len(clean_code) == 8:
                self.root.after(100, self.submit_tv_code)
        else:
            self.set_banner_status("Lỗi dán dữ liệu", "Không tìm thấy mã TV trong ảnh hoặc text", mode="error")
            
        return "break"

    def _validate_code_input(self, *args):
        if not self.code_enabled:
            return
        val = self.code_var.get()
        clean_val = re.sub(r'[^0-9]', '', val)[:8]
        if val != clean_val:
            self.code_var.set(clean_val)

    # ==========================================
    # WORKFLOW 1: LOG CODE (JSON / METADATA)
    # ==========================================

    def _on_tv_paste(self, event=None):
        try:
            pasted_text = self.root.clipboard_get()
        except tk.TclError:
            pasted_text = ""
        self.tv_input_text.delete("1.0", "end")
        self.tv_input_text.insert("1.0", pasted_text)
        self.tv_input_text.config(fg=self.TEXT_WHITE)
        self.tv_placeholder_active = False
        self.root.after(80, self._process_tv_link)
        return "break"

    def _process_tv_link(self):
        if self._tv_timer is not None:
            self.root.after_cancel(self._tv_timer)
            self._tv_timer = None
        raw = self.tv_input_text.get("1.0", "end").strip()
        if not raw or raw == "Nhập link ạ....":
            return
        source_type, cookies_list = parse_input_source(raw)
        if not source_type or not cookies_list:
            self._set_code_input_state(False)
            self._start_tv_error_countdown("Die link/cookies",)
            return
        self._set_code_input_state(False)
        self.set_banner_status(f"Đang kiểm tra {source_type}", "Đang đăng nhập vào trang TV8 ngầm", mode="loading")
        self.cmd_queue.put({
            "action": "warmup",
            "cookies": cookies_list,
            "type": source_type,
            "headless": not self.show_chrome
        })

    def submit_tv_code(self):
        if not self.code_enabled:
            return
        code = self.code_var.get().strip()
        if len(code) != 8 or not code.isdigit():
            self.set_banner_status("Mã chưa đủ 8 số", "Vui lòng nhập đúng 8 chữ số trên TV", mode="error")
            return
        self.code_entry.config(state="disabled")
        self.cv.itemconfig(self.submit_btn_rect, fill=self.BTN_BG_INACTIVE)
        self.set_banner_status(f"Đang gửi mã {code}", "Đang kích hoạt thiết bị trên Netflix", mode="loading")
        self.cmd_queue.put({"action": "submit", "code": code})

    def _start_tv_clear_countdown(self, seconds_left=5):
        if self._tv_timer is not None:
            self.root.after_cancel(self._tv_timer)
            self._tv_timer = None
        if seconds_left > 0:
            self.set_banner_status("Submit Code DONE", f"Tự động xoá dữ liệu sau {seconds_left}s   ạ!", mode="success")
            self._tv_timer = self.root.after(1000, lambda: self._start_tv_clear_countdown(seconds_left - 1))
        else:
            self.tv_input_text.delete("1.0", "end")
            self.tv_placeholder_active = True
            self.tv_input_text.config(fg=self.TEXT_MUTED)
            self.tv_input_text.insert("1.0", "Nhập link ạ...")
            self._set_code_input_state(False)
            self.root.focus_set()
            self.set_banner_status("Sẵn sàng", "Hãy dán JSON hoặc Metadata tiếp theo", mode="success")

    def _start_tv_error_countdown(self, main_err="Lỗi Log CODE", sub_err="", seconds_left=5):
        if self._tv_timer is not None:
            self.root.after_cancel(self._tv_timer)
            self._tv_timer = None
        if seconds_left > 0:
            self.set_banner_status(main_err, f"{sub_err} Tự động xoá dữ liệu sau {seconds_left}s ạ!", mode="error")
            self._tv_timer = self.root.after(1000, lambda: self._start_tv_error_countdown(main_err, sub_err, seconds_left - 1))
        else:
            self.tv_input_text.delete("1.0", "end")
            self.tv_placeholder_active = True
            self.tv_input_text.config(fg=self.TEXT_MUTED)
            self.tv_input_text.insert("1.0", "Nhập link...")
            self._set_code_input_state(False)
            self.root.focus_set()
            self.set_banner_status("Sẵn sàng", "Hãy dán JSON hoặc Metadata tiếp theo", mode="success")

    # ==========================================
    # WORKFLOW 2: CHUẨN HOÁ ĐỊNH DẠNG (CSKH)
    # ==========================================

    def _on_cskh_paste(self, event=None):
        if getattr(self, "_cskh_processing", False):
            return "break"
        self._cskh_processing = True

        try:
            pasted_text = self.root.clipboard_get()
        except tk.TclError:
            pasted_text = ""

        self.cskh_input_text.delete("1.0", "end")
        self.cskh_input_text.insert("1.0", pasted_text)
        self.cskh_input_text.config(fg=self.TEXT_WHITE)
        self.cskh_placeholder_active = False

        self.root.after(80, self._process_cskh_text)
        return "break"

    def _process_cskh_text(self):
        try:
            raw = self.cskh_input_text.get("1.0", "end").strip()
            if not raw or raw in ("Nhập thông tin...", "Nhập thông tin đi ạ..."):
                return

            formatted = format_bot_account_text(raw)
            if formatted:
                self.cskh_input_text.delete("1.0", "end")
                self.cskh_input_text.insert("1.0", formatted)
                self.root.clipboard_clear()
                self.root.clipboard_append(formatted)
                self.root.update_idletasks()
                self.play_sound()
                self._start_cskh_clear_countdown(5)
            else:
                self._start_cskh_error_countdown(5)
        finally:
            # Luôn mở lại cờ cho lần dán tiếp theo sau 300ms
            self.root.after(300, lambda: setattr(self, "_cskh_processing", False))

    def _start_cskh_clear_countdown(self, seconds_left=5):
        if self._cskh_timer is not None:
            self.root.after_cancel(self._cskh_timer)
            self._cskh_timer = None
        if seconds_left > 0:
            self.cv.itemconfig(self.cskh_title_text, text=f"Chuẩn hoá ({seconds_left}s)", fill="#38BDF8")
            self.set_banner_status("Đã chuẩn hoá & Tự động Copy", f"Tự xoá sau {seconds_left} ạ", mode="success")
            self._cskh_timer = self.root.after(1000, lambda: self._start_cskh_clear_countdown(seconds_left - 1))
        else:
            self.cv.itemconfig(self.cskh_title_text, text="Format DATA", fill=self.TEXT_WHITE)
            self.cskh_input_text.delete("1.0", "end")
            self.cskh_placeholder_active = True
            self.cskh_input_text.config(fg=self.TEXT_MUTED)
            self.cskh_input_text.insert("1.0", "Nhập thông tin ạ...")
            self.root.focus_set()
            self.set_banner_status("Đã xoá ô Format DATA", "Sẵn sàng nhận dữ liệu tiếp theo", mode="success")

    def _start_cskh_error_countdown(self, seconds_left=5):
        if self._cskh_timer is not None:
            self.root.after_cancel(self._cskh_timer)
            self._cskh_timer = None

        # Giữ nguyên tiêu đề và màu trắng chuẩn của Format DATA
        self.cv.itemconfig(self.cskh_title_text, text="Format DATA", fill=self.TEXT_WHITE)

        if seconds_left > 0:
            self.set_banner_status("Sai định dạng CSKH", f"Tự động xoá dữ liệu sau {seconds_left}s ạ!", mode="error")
            self._cskh_timer = self.root.after(1000, lambda: self._start_cskh_error_countdown(seconds_left - 1))
        else:
            self.cskh_input_text.delete("1.0", "end")
            self.cskh_placeholder_active = True
            self.cskh_input_text.config(fg=self.TEXT_MUTED)
            self.cskh_input_text.insert("1.0", "Nhập thông tin...")
            self.root.focus_set()
            self.set_banner_status("Sẵn sàng", "Hãy dán thông tin CSKH, link TV8 hoặc email", mode="success")

    # ==========================================
    # 3. PLAYWRIGHT WORKER (TỰ NHẬN CHROME/EDGE CÓ SẴN)
    # ==========================================

    async def _launch_smart_browser(self, p, headless):
        args = ["--window-size=520,440"]
        for ch in ("chrome", "msedge"):
            try:
                return await p.chromium.launch(channel=ch, headless=headless, args=args)
            except Exception:
                pass
        return await p.chromium.launch(headless=headless, args=args)

    def _run_async_worker(self):
        asyncio.run(self._worker_loop())

    async def _create_standby_session(self, browser):
        """Khởi tạo sẵn Context + Page sạch chạy nền để chờ nạp Cookie tức thì"""
        try:
            context = await browser.new_context(
                viewport={"width": 500, "height": 400},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
            await context.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "media"] else route.continue_())
            pages = context.pages
            page = pages[0] if pages else await context.new_page()
            return {"context": context, "page": page}
        except Exception:
            return None

    async def _worker_loop(self):
        async with async_playwright() as p:
            current_headless = True
            browser = None
            try:
                browser = await self._launch_smart_browser(p, current_headless)
            except Exception:
                self.root.after(0, lambda: self.set_banner_status("Thiếu trình duyệt", "Vui lòng cài đặt Google Chrome để dùng Log CODE", mode="error"))
            
            # Pre-warming: Tạo sẵn session dự phòng đầu tiên
            standby_session = None
            if browser:
                standby_session = await self._create_standby_session(browser)

            while True:
                try:
                    cmd = self.cmd_queue.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.1)
                    continue

                action = cmd.get("action")
                if action == "warmup":
                    req_headless = cmd.get("headless", True)
                    # Nếu người dùng đổi chế độ Hiện/Ẩn Chrome thì reset lại browser
                    if browser is None or req_headless != current_headless:
                        await self._close_ready_session()
                        if standby_session:
                            try:
                                await standby_session["context"].close()
                            except Exception:
                                pass
                            standby_session = None

                        if browser:
                            try:
                                await browser.close()
                            except Exception:
                                pass
                        current_headless = req_headless
                        try:
                            browser = await self._launch_smart_browser(p, current_headless)
                            standby_session = await self._create_standby_session(browser)
                        except Exception:
                            self.root.after(0, lambda: self.set_banner_status("Thiếu Google Chrome", "Máy chưa cài đặt Google Chrome!", mode="error"))
                            continue

                    # Sử dụng standby session có sẵn hoặc tạo mới nếu chưa kịp tạo
                    active_session = standby_session
                    standby_session = None # Đã dùng nên giải phóng slot chờ

                    if not active_session:
                        active_session = await self._create_standby_session(browser)

                    # Xử lý warmup trên session đã chuẩn bị sẵn
                    await self._handle_warmup(browser, active_session, cmd["cookies"], cmd.get("type", "JSON"))

                    # Tự động nạp sẵn session mới ngay sau đó để đón lượt dán tiếp theo
                    if browser and not standby_session:
                        standby_session = await self._create_standby_session(browser)

                elif action == "submit":
                    await self._handle_submit(cmd["code"])
                    # Sau khi submit xong, âm thầm chuẩn bị sẵn session tiếp theo
                    if browser and not standby_session:
                        standby_session = await self._create_standby_session(browser)

                elif action == "quit":
                    await self._close_ready_session()
                    if standby_session:
                        try:
                            await standby_session["context"].close()
                        except Exception:
                            pass
                    if browser:
                        try:
                            await browser.close()
                        except Exception:
                            pass
                    break

    async def _close_ready_session(self):
        if self.ready_session:
            try:
                await self.ready_session["context"].close()
            except Exception:
                pass
            self.ready_session = None

    async def _unlock_profile_gate_if_needed(self, page):
        profiles = page.locator('.list-profiles li.profile, ul.choose-profile li, [data-uia="action-select-profile"]')
        count = await profiles.count()
        if count == 0:
            return "NO_GATE"
        for i in range(count):
            prof = profiles.nth(i)
            txt = (await prof.inner_text() or "").lower()
            if "add" in txt or "thêm" in txt:
                continue
            has_lock = await prof.locator('[data-uia*="lock"], .profile-lock, [class*="lock"], svg[class*="lock"]').count() > 0
            if has_lock:
                continue
            await prof.click(force=True, timeout=3000)
            await page.wait_for_timeout(1200)
            pin_box = await page.locator('input[type="tel"], input[data-uia*="pin"], .pin-input-container').count()
            if pin_box > 0:
                continue
            return "UNLOCKED"
        return "ALL_LOCKED"

    async def _handle_warmup(self, browser, session, cookies_list, source_type):
        await self._close_ready_session()
        context = session["context"] if session else None
        page = session["page"] if session else None
        try:
            prepared_cookies = prepare_cookies_for_playwright(cookies_list)
            if not prepared_cookies:
                self.root.after(0, lambda: self._start_tv_error_countdown("Die link/cookies", "Tài khoản lỗi hoặc đã logout", 5))
                if context:
                    await context.close()
                return

            # Nạp cookie trực tiếp vào context đã chờ sẵn
            await context.add_cookies(prepared_cookies)
            
            # Điều hướng thẳng đến trang kích hoạt TV8
            await page.goto("https://www.netflix.com/tv8", timeout=25000, wait_until="domcontentloaded")
            await page.wait_for_timeout(800)
            current_url = page.url.lower()
            if any(k in current_url for k in ["/login", "/clearcookies", "/logout"]):
                self.root.after(0, lambda: self._start_tv_error_countdown("Die link/cookies", "Tài khoản lỗi hoặc đã logout", 5))
                await context.close()
                return
            input_count = await page.locator('input[type="text"], input[type="tel"], input[type="number"], input:not([type="hidden"])').count()
            if input_count == 0:
                gate_status = await self._unlock_profile_gate_if_needed(page)
                if gate_status == "ALL_LOCKED":
                    self.root.after(0, lambda: self.set_banner_status("All locked", "Tất cả Profile đều dính mã PIN", mode="error"))
                    await context.close()
                    return
                elif gate_status == "UNLOCKED":
                    if "tv8" not in page.url.lower():
                        await page.goto("https://www.netflix.com/tv8", timeout=20000, wait_until="domcontentloaded")
            if "/login" in page.url.lower() or await page.locator('a[href*="/login"], button:has-text("Sign In")').count() > 0:
                self.root.after(0, lambda: self._start_tv_error_countdown("Die link/cookies", "Tài khoản lỗi hoặc đã logout", 5))
                await context.close()
                return
            await page.wait_for_selector('input:not([type="hidden"])', state="visible", timeout=12000)
            self.ready_session = {
                "context": context,
                "page": page
            }
            def unlock_ui():
                self._set_code_input_state(True)
                self.set_banner_status(f"{source_type} hợp lệ", "Nhập mã TV để tiếp tục", mode="success")
            self.root.after(0, unlock_ui)
        except Exception:
            self.root.after(0, lambda: self._start_tv_error_countdown("Die link/cookies", "Tài khoản lỗi hoặc đã logout", 5))
            if context:
                await context.close()

    async def _handle_submit(self, code):
        if not self.ready_session:
            self.root.after(0, lambda: self._start_tv_error_countdown("Die link/cookies", "Tài khoản lỗi hoặc đã logout", 5))
            return
        page = self.ready_session["page"]
        try:
            inputs = page.locator('input:not([type="hidden"])')
            first_input = inputs.first
            await first_input.click(force=True)
            await page.keyboard.press("Meta+A")
            await page.keyboard.press("Backspace")
            for _ in range(8):
                await page.keyboard.press("Backspace")

            await first_input.click(force=True)
            await page.keyboard.type(code, delay=25)
            await page.wait_for_timeout(150)
            submit_selectors = [
                'button[type="submit"]',
                'button[data-uia*="submit"]',
                'button[data-uia*="continue"]',
                'button.tv8-button',
                'button:has-text("Continue")',
                'button:has-text("Tiếp tục")',
                'button:has-text("Tiếp")',
                'button:has-text("Activate")'
            ]
            clicked = False
            for sel in submit_selectors:
                btn = page.locator(sel).first
                if await btn.count() > 0 and await btn.is_visible():
                    await btn.click(force=True)
                    clicked = True
                    break
            if not clicked:
                await page.keyboard.press("Enter")
            outcome = "UNKNOWN"
            for _ in range(55):
                await asyncio.sleep(0.15)
                current_url = page.url.lower()
                if "tv8" not in current_url and "/login" not in current_url:
                    outcome = "DONE"
                    break
                dom_status = await page.evaluate('''() => {
                    const bodyText = (document.body.innerText || '').toLowerCase();
                    const successWords = [
                        'all set', 'connected', 'success', 'enjoy netflix on your tv',
                        'thành công', 'đã kết nối', 'hoàn tất', 'listo', 'conectado'
                    ];
                    for (const w of successWords) {
                        if (bodyText.includes(w)) return 'DONE';
                    }
                    if (document.querySelector('[data-uia*="success"], .success-icon, .checkmark')) {
                        return 'DONE';
                    }
                    const errorSelectors = [
                        '[data-uia*="error"]',
                        '[data-uia*="message"]',
                        '.ui-message-error',
                        '.error-message',
                        '.error',
                        'div[role="alert"]'
                    ];
                    for (const sel of errorSelectors) {
                        const els = document.querySelectorAll(sel);
                        for (const el of els) {
                            const style = window.getComputedStyle(el);
                            if (style && style.display !== 'none' && style.visibility !== 'hidden' && el.offsetHeight > 0) {
                                const txt = (el.innerText || el.textContent || '').trim();
                                if (txt.length > 2) return 'ERROR';
                            }
                        }
                    }
                    const errWords = [
                        'invalid code', 'incorrect code', 'code has expired', 'try again',
                        'something went wrong', 'not valid', 'mã không hợp lệ', 'thử lại',
                        'código no válido', 'código incorrecto'
                    ];
                    for (const ew of errWords) {
                        if (bodyText.includes(ew)) return 'ERROR';
                    }
                    const visibleInputs = Array.from(document.querySelectorAll('input:not([type="hidden"])')).filter(
                        el => el.offsetHeight > 0
                    );
                    if (visibleInputs.length === 0) return 'DONE';
                    return 'WAIT';
                }''')
                if dom_status == "DONE":
                    outcome = "DONE"
                    break
                elif dom_status == "ERROR":
                    outcome = "ERROR"
                    break
            if outcome == "DONE":
                self.play_sound()
                await self._close_ready_session()
                self.root.after(0, lambda: self._start_tv_clear_countdown(5))
            else:
                self.root.after(0, lambda: self._start_tv_error_countdown("Code Error", "Mã sai hoặc hết hạn", 5))
        except Exception as e:
            if "execution context was destroyed" in str(e).lower() or "target closed" in str(e).lower():
                self.play_sound()
                await self._close_ready_session()
                self.root.after(0, lambda: self._start_tv_clear_countdown(5))
            else:
                self.root.after(0, lambda: self.set_banner_status("Code Error", "Vui lòng kiểm tra lại mã TV", mode="error"))
                self.root.after(0, self._reopen_code_input)

    def _reopen_code_input(self):
        self.code_enabled = True
        self.code_entry.config(state="normal", fg=self.TEXT_WHITE)
        self.cv.itemconfig(self.submit_btn_rect, fill=self.BTN_BG_ACTIVE)
        self.cv.itemconfig(self.submit_btn_text, fill="#FFFFFF")
        self.code_entry.selection_range(0, "end")
        self.code_entry.focus_set()

if __name__ == "__main__":
    root = tk.Tk()
    app = ModernNetflixApp(root)
    root.mainloop()
