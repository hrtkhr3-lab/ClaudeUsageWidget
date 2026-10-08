"""Claude 使用状況ウィジェット

Claude Code のログイン情報 (~/.claude/.credentials.json) を使って
プランの使用率 (5時間枠 / 週間) を取得し、デスクトップに小さく表示する。
今日の Claude Code の応答数・出力トークンはローカルのログから集計する。
"""
import ctypes
import json
import os
import queue
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path
import tkinter as tk

APP = "ClaudeUsageWidget"
HOME = Path.home()
CRED = HOME / ".claude" / ".credentials.json"
PROJECTS = HOME / ".claude" / "projects"
CONF_DIR = Path(os.environ.get("APPDATA", HOME)) / APP
CONF = CONF_DIR / "config.json"
API = "https://api.anthropic.com/api/oauth/usage"
USAGE_PAGE = "https://claude.ai/settings/usage"

API_INTERVAL_MS = 5 * 60 * 1000   # 使用率の取得間隔
LOCAL_INTERVAL_MS = 60 * 1000     # ローカルログの集計間隔
TICK_MS = 30 * 1000               # 残り時間表示の更新間隔

COLOR = {
    "bg": "#FFFFFF",
    "border": "#E4E2DC",
    "text": "#1F1F1E",
    "muted": "#8C8982",
    "track": "#F0EEE9",
    "accent": "#D97757",
    "danger": "#D64545",
    "pill_bg": "#FBEEE8",
    "hover": "#F4F2EE",
}
CLAWD = {  # clawd-tray と同じ色
    "body": "#D77757", "shade": "#B4593D", "eye": "#1B1714",
    "sweat": "#9FD3F5", "pink": "#E99AA0", "zed": "#B5B1A8",
}
ANIM_MS = 250                     # Clawd のコマ送り間隔
ACTIVE_SEC = 90                   # ログがこの秒数以内に更新されたら「作業中」
FONT = "Yu Gothic UI"
NUM_FONT = "Segoe UI Semibold"
WEEKDAYS = "月火水木金土日"

# 表示する枠 (API のキー, 表示名)
LIMITS = [
    ("five_hour", "5時間枠"),
    ("seven_day", "週間（全モデル）"),
    ("seven_day_opus", "週間（Opus）"),
    ("seven_day_sonnet", "週間（Sonnet）"),
]


# ---------------------------------------------------------------- Windows ---

def single_instance():
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, "Local\\" + APP)
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        sys.exit(0)
    return handle


def enable_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def work_area():
    r = RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)
    return r.left, r.top, r.right, r.bottom


def virtual_screen():
    gsm = ctypes.windll.user32.GetSystemMetrics
    x, y = gsm(76), gsm(77)
    return x, y, x + gsm(78), y + gsm(79)


def round_corners(root):
    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        pref = ctypes.c_int(2)  # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4)
    except Exception:
        pass


# ------------------------------------------------------------------- data ---

def load_conf():
    try:
        return json.loads(CONF.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_conf(conf):
    try:
        CONF_DIR.mkdir(parents=True, exist_ok=True)
        CONF.write_text(json.dumps(conf, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def fetch_usage():
    """(data, plan, error) を返す"""
    try:
        cred = json.loads(CRED.read_text(encoding="utf-8"))["claudeAiOauth"]
    except Exception:
        return None, None, "Claude Code にログインしてください"
    plan = (cred.get("subscriptionType") or "").capitalize() or None
    if cred.get("expiresAt", 0) / 1000 < time.time():
        return None, plan, "認証の期限切れ · Claude Code 使用時に再開"
    req = urllib.request.Request(API, headers={
        "Authorization": "Bearer " + cred.get("accessToken", ""),
        "anthropic-beta": "oauth-2025-04-20",
        "User-Agent": APP + "/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            return json.loads(res.read().decode("utf-8")), plan, None
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return None, plan, "認証の期限切れ · Claude Code 使用時に再開"
        if e.code == 429:
            return None, plan, "取得制限中 · しばらく後に再取得"
        return None, plan, f"取得エラー ({e.code})"
    except Exception:
        return None, plan, "オフライン"


def parse_time(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def local_today():
    """今日の Claude Code の応答数と出力トークン数"""
    start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    t0 = start.timestamp()
    seen = set()
    replies = output = 0
    for f in PROJECTS.rglob("*.jsonl"):
        try:
            if f.stat().st_mtime < t0:
                continue
            with open(f, encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    if '"usage"' not in line or '"assistant"' not in line:
                        continue
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    msg = d.get("message") or {}
                    usage = msg.get("usage")
                    if d.get("type") != "assistant" or not usage:
                        continue
                    ts = parse_time(d.get("timestamp") or "")
                    if not ts or ts.timestamp() < t0:
                        continue
                    key = msg.get("id") or d.get("uuid")
                    if key in seen:
                        continue
                    seen.add(key)
                    replies += 1
                    output += usage.get("output_tokens", 0) or 0
        except OSError:
            continue
    return replies, output


def last_activity():
    """Claude Code のログが最後に書かれた時刻 (epoch 秒)"""
    latest = 0.0
    for f in PROJECTS.rglob("*.jsonl"):
        try:
            latest = max(latest, f.stat().st_mtime)
        except OSError:
            pass
    return latest


def fmt_tokens(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def fmt_reset(iso):
    t = parse_time(iso or "")
    if not t:
        return ""
    t = t.astimezone()
    now = datetime.now().astimezone()
    left = max(0, int((t - now).total_seconds()))
    d, rem = divmod(left, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        left_s = f"あと{d}日{h}時間"
    elif h:
        left_s = f"あと{h}時間{m}分"
    else:
        left_s = f"あと{m}分"
    if t.date() == now.date():
        when = t.strftime("%H:%M")
    else:
        when = f"{t.month}/{t.day}({WEEKDAYS[t.weekday()]}) {t.strftime('%H:%M')}"
    return f"{when} にリセット · {left_s}"


# ------------------------------------------------------------------ Clawd ---

class Clawd:
    """ヘッダーに住む小さな Clawd（clawd-tray の Art.Clawd / DrawScene と同じ形）。
    格子は横 20 × 縦 13 ドット。上の 3 段と右端に汗や Zz、体は x 0〜17・y 3〜12。"""
    COLS, ROWS, TOP = 20, 13, 3
    Z = ("###", "..#", ".#.", "#..", "###")

    def __init__(self, parent, unit, bg):
        self.u = unit
        self.c = tk.Canvas(parent, width=round(self.COLS * unit), height=round(self.ROWS * unit),
                           bg=bg, highlightthickness=0, bd=0)
        self.frame = 0
        self.mood = "resting"
        self.hover = False
        self.c.bind("<Enter>", lambda e: setattr(self, "hover", True))
        self.c.bind("<Leave>", lambda e: setattr(self, "hover", False))

    def dot(self, color, x, y, w=1, h=1, ox=0.0, oy=0.0, u=None):
        u = u or self.u
        X, Y = round(ox + x * u), round(oy + y * u)
        W, H = round(ox + (x + w) * u) - X, round(oy + (y + h) * u) - Y
        self.c.create_rectangle(X, Y, X + max(1, W), Y + max(1, H), fill=color, width=0)

    def draw(self):
        self.frame += 1
        f, u = self.frame, self.u
        t = f * 0.25
        blink = (t % 4.3) < 0.25
        p = dict(dx=0, dy=0, closed=False, squash=False, legs="stand", arm_r="side", blush=False)
        oy = self.TOP * u
        self.c.delete("all")
        mood = "talking" if self.hover else self.mood
        if mood == "working":      # 足踏みしながら、ときどき左右を見る
            p.update(legs="a" if (f // 2) % 2 == 0 else "b",
                     dx=(0, 1, 0, -1)[(f // 12) % 4], closed=blink)
        elif mood == "tired":      # 汗をかいて、ゆっくり足踏み
            p.update(legs="a" if (f // 3) % 2 == 0 else "b", dy=1, closed=blink,
                     squash=(f // 3) % 2 == 0)
            k = f % 6
            if k < 4:
                self.dot(CLAWD["sweat"], 15, k)
        elif mood == "away":       # 眠る（Zz）
            p.update(closed=True, legs="tuck", squash=(t % 3) < 1.4)
            oy += u
            z = (f // 3) % 4
            if z < 3:
                zu = max(1, int(u * 0.6))
                self.dot_glyph(self.Z, round((15 + z) * u), round((2 - z) * u * 0.5), zu)
        elif mood == "talking":    # 手を振る
            p.update(arm_r="up" if (f // 2) % 2 == 0 else "side", blush=True)
        else:                      # 呼吸とまばたき
            p.update(squash=(t % 3.2) < 0.5, closed=blink,
                     dx=(0, 0, 1, 0, 0, -1)[(f // 10) % 6])
        self.body(0, oy, p)

    def dot_glyph(self, rows, ox, oy, u):
        for j, row in enumerate(rows):
            for i, ch in enumerate(row):
                if ch == "#":
                    self.dot(CLAWD["zed"], i, j, ox=ox, oy=oy, u=u)

    def body(self, ox, oy, p):
        col = CLAWD["body"]
        P = lambda x, y, c, w, h: self.dot(c, x, y, w, h, ox, oy)
        sq = 1 if p["squash"] else 0
        legs = {"a": (4, 11), "b": (6, 13), "tuck": ()}.get(p["legs"], (4, 6, 11, 13))
        for lx in legs:
            P(lx, 8, col, 1, 2)
        P(3, sq, col, 12, 8 - sq)
        P(2, 4, col, 1, 2)
        P(1, 4, col, 1, 2)
        if p["arm_r"] == "up":
            P(15, 2, col, 1, 2)
            P(16, 0, col, 1, 2)
        else:
            P(15, 4, col, 1, 2)
            P(16, 4, col, 1, 2)
        dx = max(-1, min(1, p["dx"]))
        ey = max(sq + 1, min(5, 2 + sq + p["dy"]))
        ec = CLAWD["shade"] if p["closed"] else CLAWD["eye"]
        eh = 1 if p["closed"] else 2
        P(5 + dx, ey + 2 - eh, ec, 1, eh)
        P(12 + dx, ey + 2 - eh, ec, 1, eh)
        if p["blush"]:
            P(4, 5, CLAWD["pink"], 1, 1)
            P(13, 5, CLAWD["pink"], 1, 1)


# --------------------------------------------------------------------- UI ---

class Widget:
    def __init__(self):
        self.conf = load_conf()
        self.data = self.conf.get("last")
        self.plan = self.conf.get("plan")
        self.error = None
        self.updated_at = self.conf.get("last_at")
        self.local = None
        self.results = queue.Queue()
        self.busy = False

        self.root = root = tk.Tk()
        root.withdraw()
        root.title("Claude 使用状況")
        root.overrideredirect(True)
        root.configure(bg=COLOR["border"])
        root.attributes("-topmost", bool(self.conf.get("topmost", False)))
        self.s = root.winfo_fpixels("1i") / 96.0
        self.width = self.px(256)

        self.frame = tk.Frame(root, bg=COLOR["bg"], padx=self.px(14), pady=self.px(11))
        self.frame.pack(fill="both", expand=True, padx=1, pady=1)
        self.build_header()
        self.body = tk.Frame(self.frame, bg=COLOR["bg"])
        self.body.pack(fill="x")
        self.build_footer()
        self.build_menu()

        root.bind_all("<ButtonPress-1>", self.drag_start)
        root.bind_all("<B1-Motion>", self.drag_move)
        root.bind_all("<ButtonRelease-1>", self.drag_end)
        root.bind_all("<Button-3>", self.show_menu)

        self.render()
        self.place_window()
        root.deiconify()
        round_corners(root)

        root.after(100, self.poll_results)
        root.after(300, self.refresh_api)
        root.after(500, self.refresh_local)
        root.after(TICK_MS, self.tick)
        root.after(ANIM_MS, self.animate)

    def px(self, v):
        return int(round(v * self.s))

    # ---- layout
    def build_header(self):
        bg = COLOR["bg"]
        head = tk.Frame(self.frame, bg=bg)
        head.pack(fill="x", pady=(0, self.px(6)))
        self.clawd = Clawd(head, 2 * self.s, bg)
        self.clawd.c.pack(side="left", padx=(0, self.px(6)))
        tk.Label(head, text="Claude 使用状況", bg=bg, fg=COLOR["text"],
                 font=(FONT, 10, "bold")).pack(side="left")
        self.plan_label = tk.Label(head, text="", bg=COLOR["pill_bg"], fg=COLOR["accent"],
                                   font=(FONT, 7, "bold"), padx=self.px(5), pady=0)
        self.close_btn = self.icon_button(head, "✕", self.quit)
        self.refresh_btn = self.icon_button(head, "⟳", self.refresh_now)

    def icon_button(self, parent, text, command):
        b = tk.Label(parent, text=text, bg=COLOR["bg"], fg=COLOR["muted"],
                     font=("Segoe UI Symbol", 9), padx=self.px(4), cursor="hand2")
        b.pack(side="right")
        b.bind("<Enter>", lambda e: b.configure(bg=COLOR["hover"], fg=COLOR["text"]))
        b.bind("<Leave>", lambda e: b.configure(bg=COLOR["bg"], fg=COLOR["muted"]))
        b.bind("<ButtonRelease-1>", lambda e: command() if not self.dragged else None)
        return b

    def build_footer(self):
        bg = COLOR["bg"]
        tk.Frame(self.frame, bg=COLOR["border"], height=1).pack(fill="x", pady=(self.px(8), self.px(7)))
        line = tk.Frame(self.frame, bg=bg)
        line.pack(fill="x")
        tk.Label(line, text="今日の Claude Code", bg=bg, fg=COLOR["muted"],
                 font=(FONT, 8)).pack(side="left")
        self.local_label = tk.Label(line, text="集計中…", bg=bg, fg=COLOR["text"], font=(FONT, 8))
        self.local_label.pack(side="right")
        self.status_label = tk.Label(self.frame, text="", bg=bg, fg=COLOR["muted"],
                                     font=(FONT, 7), anchor="w")
        self.status_label.pack(fill="x", pady=(self.px(2), 0))

    def build_menu(self):
        self.topmost_var = tk.BooleanVar(value=bool(self.conf.get("topmost", False)))
        m = self.menu = tk.Menu(self.root, tearoff=0, font=(FONT, 9))
        m.add_command(label="今すぐ更新", command=self.refresh_now)
        m.add_command(label="使用状況ページを開く", command=lambda: webbrowser.open(USAGE_PAGE))
        m.add_separator()
        m.add_checkbutton(label="常に最前面に表示", variable=self.topmost_var,
                          command=self.toggle_topmost)
        m.add_command(label="位置を右下に戻す", command=self.reset_position)
        m.add_separator()
        m.add_command(label="終了", command=self.quit)

    def render(self):
        bg = COLOR["bg"]
        if self.plan:
            self.plan_label.configure(text=self.plan)
            self.plan_label.pack(side="left", padx=(self.px(6), 0))
        for w in self.body.winfo_children():
            w.destroy()

        rows = [(name, self.data.get(key)) for key, name in LIMITS
                if self.data and self.data.get(key)]
        if not rows:
            tk.Label(self.body, text="使用率を取得しています…" if not self.error else "使用率を取得できません",
                     bg=bg, fg=COLOR["muted"], font=(FONT, 8), anchor="w").pack(fill="x", pady=self.px(6))
        stale = self.error is not None
        bar_w = self.width - self.px(28) - 2
        bar_h = self.px(6)
        for i, (name, item) in enumerate(rows):
            pct = max(0.0, min(100.0, float(item.get("utilization") or 0)))
            color = COLOR["danger"] if pct >= 80 else COLOR["accent"]
            if stale:
                color = COLOR["muted"]
            row = tk.Frame(self.body, bg=bg)
            row.pack(fill="x", pady=(self.px(6) if i else 0, 0))
            top = tk.Frame(row, bg=bg)
            top.pack(fill="x")
            tk.Label(top, text=name, bg=bg, fg=COLOR["muted"], font=(FONT, 8)).pack(side="left", anchor="s")
            tk.Label(top, text=f"{pct:.0f}%", bg=bg, fg=COLOR["text"],
                     font=(NUM_FONT, 11)).pack(side="right", anchor="s")
            c = tk.Canvas(row, width=bar_w, height=bar_h, bg=bg, highlightthickness=0, bd=0)
            c.pack(fill="x", pady=(self.px(2), self.px(3)))
            r = bar_h / 2
            y = bar_h / 2
            c.create_line(r, y, bar_w - r, y, width=bar_h, fill=COLOR["track"], capstyle="round")
            fill_w = (bar_w - bar_h) * pct / 100
            if pct > 0:
                c.create_line(r, y, r + fill_w, y, width=bar_h, fill=color, capstyle="round")
            tk.Label(row, text=fmt_reset(item.get("resets_at")), bg=bg, fg=COLOR["muted"],
                     font=(FONT, 7), anchor="w").pack(fill="x")

        if self.local:
            replies, output = self.local
            self.local_label.configure(text=f"{replies} 応答 · 出力 {fmt_tokens(output)}")

        if self.error:
            self.status_label.configure(text="⚠ " + self.error, fg=COLOR["danger"])
        elif self.updated_at:
            t = datetime.fromtimestamp(self.updated_at).strftime("%H:%M")
            self.status_label.configure(text=f"{t} に更新 · 右クリックでメニュー", fg=COLOR["muted"])
        self.root.update_idletasks()
        self.root.geometry(f"{self.width}x{self.root.winfo_reqheight()}")
        if self.conf.get("x") is None:
            # 位置未保存なら高さが変わっても右下に合わせる
            x, y = self.default_position()
            self.root.geometry(f"+{x}+{y}")
        self.clamp_position()

    # ---- position
    def place_window(self):
        self.root.update_idletasks()
        x, y = self.conf.get("x"), self.conf.get("y")
        if x is None or y is None:
            x, y = self.default_position()
        self.root.geometry(f"{self.width}x{self.root.winfo_reqheight()}+{x}+{y}")
        self.clamp_position()

    def default_position(self):
        l, t, r, b = work_area()
        margin = self.px(16)
        h = self.root.winfo_reqheight()
        return r - self.width - margin, b - h - margin

    def clamp_position(self):
        self.root.update_idletasks()
        l, t, r, b = virtual_screen()
        w, h = self.width, self.root.winfo_reqheight()
        x = min(max(self.root.winfo_x(), l), r - w)
        y = min(max(self.root.winfo_y(), t), b - h)
        if (x, y) != (self.root.winfo_x(), self.root.winfo_y()):
            self.root.geometry(f"+{x}+{y}")

    def reset_position(self):
        self.conf.pop("x", None)
        self.conf.pop("y", None)
        save_conf(self.conf)
        x, y = self.default_position()
        self.root.geometry(f"+{x}+{y}")

    def drag_start(self, e):
        self.dragged = False
        self._drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def drag_move(self, e):
        if not hasattr(self, "_drag"):
            return
        self.dragged = True
        dx, dy = self._drag
        self.root.geometry(f"+{e.x_root - dx}+{e.y_root - dy}")

    def drag_end(self, e):
        if getattr(self, "dragged", False):
            self.conf["x"], self.conf["y"] = self.root.winfo_x(), self.root.winfo_y()
            save_conf(self.conf)
        self.root.after(1, lambda: setattr(self, "dragged", False))

    dragged = False

    # ---- menu actions
    def show_menu(self, e):
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()

    def toggle_topmost(self):
        on = self.topmost_var.get()
        self.root.attributes("-topmost", on)
        self.conf["topmost"] = on
        save_conf(self.conf)

    def quit(self):
        self.root.destroy()

    # ---- updates
    def run_bg(self, kind, fn):
        threading.Thread(target=lambda: self.results.put((kind, fn())), daemon=True).start()

    def refresh_api(self):
        if not self.busy:
            self.busy = True
            self.refresh_btn.configure(fg=COLOR["accent"])
            self.run_bg("api", fetch_usage)
        self.root.after(API_INTERVAL_MS, self.refresh_api)

    def refresh_local(self):
        self.run_bg("local", local_today)
        self.root.after(LOCAL_INTERVAL_MS, self.refresh_local)

    def animate(self):
        self.anim_count = getattr(self, "anim_count", 0) + 1
        if self.anim_count % 20 == 1:  # 5 秒ごとに Claude Code が動いているか見る
            self.run_bg("activity", last_activity)
        self.clawd.mood = self.current_mood()
        self.clawd.draw()
        self.root.after(ANIM_MS, self.animate)

    def current_mood(self):
        if self.error:
            return "away"
        if self.data and any(float((self.data.get(k) or {}).get("utilization") or 0) >= 80
                             for k, _ in LIMITS):
            return "tired"
        if time.time() - getattr(self, "active_at", 0) < ACTIVE_SEC:
            return "working"
        return "resting"

    def refresh_now(self):
        if not self.busy:
            self.busy = True
            self.refresh_btn.configure(fg=COLOR["accent"])
            self.run_bg("api", fetch_usage)
        self.run_bg("local", local_today)

    def poll_results(self):
        changed = False
        while True:
            try:
                kind, result = self.results.get_nowait()
            except queue.Empty:
                break
            if kind == "activity":
                self.active_at = result
                continue
            changed = True
            if kind == "api":
                self.busy = False
                self.refresh_btn.configure(fg=COLOR["muted"])
                data, plan, error = result
                self.plan = plan or self.plan
                self.error = error
                if data:
                    self.data = data
                    self.updated_at = time.time()
                    self.conf.update(last=data, last_at=self.updated_at, plan=self.plan)
                    save_conf(self.conf)
            else:
                self.local = result
        if changed:
            self.render()
        self.root.after(200, self.poll_results)

    def tick(self):
        self.render()
        self.root.after(TICK_MS, self.tick)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    _mutex = single_instance()
    enable_dpi_awareness()
    Widget().run()
