"""Live hardware HUD. Read-only local telemetry; no AI requests or microphone capture."""

from __future__ import annotations
import math, os, shutil, subprocess, threading, time, collections
from pathlib import Path
import tkinter as tk
from tkinter import simpledialog, messagebox
from .weather import Weather, clock_labels
from PIL import Image, ImageDraw, ImageChops, ImageFilter, ImageTk
from .pages import Pages
import psutil
from . import raya_hud as v3, services
from .operator import Operator
from .. import engine
from ..voice_hud import open_voice

W, H = v3.W, v3.H
CY, ICE, WHITE, MUT = v3.CY, v3.ICE, v3.WHITE, v3.MUT
R = v3.Image.Resampling
CORE_BOX = (650, 217, 998, 565)


class Core:
    def __init__(self):
        self.original = v3.artwork().crop((152, 77, 500, 425))
        self.cx = self.cy = 174
        self.mask = v3.radial_mask(348, 348, 174, 174, 0, 170, 3)
        self.inner = self.original.crop((78, 78, 270, 270))
        self.inner_mask = v3.radial_mask(192, 192, 96, 96, 0, 87, 3)
        self.fixed = v3.static_orbits((348, 348), 174, 174, True)

    def render(self, seconds):
        im = self.original.copy()
        # Rotate only the small sphere, not the rest of the frame.
        rotated = self.inner.rotate(-360 * seconds / v3.CORE_PERIOD_SECONDS, R.BICUBIC)
        im.paste(rotated, (78, 78), self.inner_mask)
        layer = Image.new("RGB", im.size)
        d = ImageDraw.Draw(layer)
        phase = seconds * math.tau / v3.ORBIT_PERIOD_SECONDS
        for j, (rx, ry) in enumerate(v3.orbit_specs(True)):
            n = 3 if j == 0 else 2
            for k in range(n):
                a = phase + j * 0.85 + k * math.tau / n + 0.45
                for step in range(12, 0, -1):
                    q = a - step * 0.022
                    x, y = v3.orbit_point(174, 174, rx, ry, q)
                    bright = (
                        v3.orbit_visibility(q, x, y, 174, 174) * (1 - step / 13) ** 1.7
                    )
                    v3.line(
                        d,
                        [(x, y), v3.orbit_point(174, 174, rx, ry, q + 0.022)],
                        v3.orbit_color((80, 224, 255), bright),
                        2,
                    )
                x, y = v3.orbit_point(174, 174, rx, ry, a)
                bright = v3.orbit_visibility(a, x, y, 174, 174)
                r = (4.6 if j == 0 else 3.3) * (0.65 + 0.35 * bright)
                v3.dot(d, x, y, r, v3.orbit_color((198, 250, 255), bright))
                v3.dot(
                    d, x, y, max(1, r * 0.48), v3.orbit_color((255, 255, 255), bright)
                )
        particles = ImageChops.add(layer, layer.filter(ImageFilter.GaussianBlur(4)))
        return ImageChops.add(ImageChops.add(im, self.fixed), particles)


class Monitor:
    def __init__(self):
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.data = {}
        self.hist = {
            k: collections.deque(maxlen=30) for k in ("cpu", "gpu", "ram", "net")
        }
        self.gpu_cmd = shutil.which("nvidia-smi")
        if not self.gpu_cmd and os.name == "nt":
            for p in (
                Path(os.environ.get("SystemRoot", "C:/Windows"))
                / "System32/nvidia-smi.exe",
                Path(os.environ.get("ProgramFiles", "C:/Program Files"))
                / "NVIDIA Corporation/NVSMI/nvidia-smi.exe",
            ):
                if p.is_file():
                    self.gpu_cmd = str(p)
                    break
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def loop(self):
        psutil.cpu_percent(None)
        prev = None
        last = time.monotonic()
        gpu = None
        gpu_at = 0
        while not self.stop.wait(1):
            now = time.monotonic()
            dt = max(0.001, now - last)
            try:
                cpu = psutil.cpu_percent(None)
                ram = psutil.virtual_memory()
                try:
                    net = psutil.net_io_counters()
                except (OSError, psutil.Error):
                    net = None
                down = up = None
                if net is not None and prev is not None:
                    down = max(0, net.bytes_recv - prev.bytes_recv) * 8 / dt / 1e6
                    up = max(0, net.bytes_sent - prev.bytes_sent) * 8 / dt / 1e6
                prev = net
                last = now
                if now - gpu_at >= 2:
                    gpu_at = now
                    gpu = None
                    if self.gpu_cmd:
                        try:
                            proc = subprocess.run(
                                [
                                    self.gpu_cmd,
                                    "--query-gpu=utilization.gpu,name",
                                    "--format=csv,noheader,nounits",
                                ],
                                capture_output=True,
                                text=True,
                                timeout=2,
                                creationflags=getattr(
                                    subprocess, "CREATE_NO_WINDOW", 0
                                ),
                            )
                            if proc.returncode == 0:
                                row = proc.stdout.strip().splitlines()[0].split(",", 1)
                                gpu = max(0, min(100, float(row[0])))
                        except (
                            OSError,
                            ValueError,
                            IndexError,
                            subprocess.TimeoutExpired,
                        ):
                            pass
                snapshot = {
                    "cpu": cpu,
                    "gpu": gpu,
                    "ram": ram.percent,
                    "used": (ram.total - ram.available) / 2**30,
                    "total": ram.total / 2**30,
                    **({"down": down, "up": up} if down is not None else {}),
                    "at": time.monotonic(),
                }
                with self.lock:
                    self.data = snapshot
                    for key, val in [
                        ("cpu", cpu),
                        ("gpu", gpu),
                        ("ram", ram.percent),
                        ("net", down + up if down is not None else None),
                    ]:
                        self.hist[key].append(val)
            except (OSError, psutil.Error, AttributeError):
                with self.lock:
                    self.data = {}

    def snapshot(self):
        with self.lock:
            return dict(self.data), {k: list(v) for k, v in self.hist.items()}

    def close(self):
        self.stop.set()
        self.thread.join()


# Regions correspond exactly to the design's 1648 x 928 coordinates.
# Replace all example numerical and status fields; preserve frame artwork.
def clean_background():
    ref = Image.open(v3.HERE / "reference.png").convert("RGB")
    # Keep original resolution and artwork; paint the live data fields on a
    # native-coordinate overlay so none of the mock values remain visible.
    d = ImageDraw.Draw(ref)
    sx = ref.width / W
    sy = ref.height / H

    def clear(box, fill="#061824"):
        d.rectangle(
            tuple(round(a * (sx if i % 2 == 0 else sy)) for i, a in enumerate(box)),
            fill=fill,
        )

    for y in (145, 194, 242, 290):
        clear((157, y, 484, y + 43))
    for y in (410, 459, 508):
        clear((91, y, 461, y + 38))
    clear((43, 624, 479, 733), "#041724")
    clear((1130, 137, 1618, 328), "#041724")
    clear((1132, 399, 1619, 530), "#041724")
    clear((1131, 595, 1357, 731), "#041724")
    clear((1391, 595, 1620, 731), "#041724")
    for x, r in ((552, 699), (738, 893), (934, 1084)):
        clear((x, 677, r, 723), "#062031")
    clear((1377, 24, 1487, 53), "#062231")
    clear((164, 869, 704, 903), "#041622")
    clear((1574, 0, 1647, 59), "#03101c")
    clear((939, 864, 1639, 910), "#041622")
    clear((192, 789, 1083, 825))
    clear((1168, 784, 1231, 828))
    clear((1336, 784, 1401, 828))
    return ref


class LiveApp:
    NOTICE_MS = 4000

    def __init__(self, profile, monitor=None, weather=None):
        self.profile = profile
        prefs = services.prefs(profile)
        fps, fullscreen, city = prefs["fps"], prefs["fullscreen"], prefs["city"]
        weather_enabled = prefs["weather"] and not engine.config(profile)["offline"]
        self.data = services.snapshot(profile)
        self.voice_state = "Idle"
        self.data_at = 0
        self.operator = Operator(self)
        if os.name == "nt":
            try:
                import ctypes

                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except (AttributeError, OSError):
                pass
        self.root = tk.Tk()
        self.root.title("R.A.Y.A • REACTOR HUD V6.1 — Canlı donanım")
        self.root.geometry("1648x928")
        self.root.minsize(824, 464)
        self.canvas = tk.Canvas(self.root, bg="#020a13", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.bg = clean_background()
        self.core = Core()
        self.core_back = (
            Image.open(v3.HERE / "reference.png")
            .convert("RGB")
            .resize((W, H), R.LANCZOS)
            .crop(CORE_BOX)
        )
        self.monitor = monitor or Monitor()
        self.weather = weather or Weather(city, weather_enabled)
        self.fps = fps
        self.start = time.monotonic()
        self.paused = prefs["paused"]
        self.frozen = 0
        self.full = fullscreen
        self.closed = False
        self.layout_after = None
        self.anim_after = None
        self.metric_after = None
        self.s = 1
        self.ox = self.oy = 0
        self.photos = {}
        self.items = {}
        self.render_times = collections.deque(maxlen=120)
        self.notice = ""
        self.notice_after = None
        self.selected = "Ana Sayfa"
        self.last_size = None
        self.pages = Pages(self)
        self.entry = tk.Entry(
            self.canvas,
            bg="#062031",
            fg=WHITE,
            insertbackground=CY,
            relief="flat",
            highlightthickness=0,
        )
        self.entry.bind("<Return>", self.submit)
        self.placeholder = "R.A.Y.A’ya bir komut yazın…"
        self.entry.insert(0, self.placeholder)
        self.entry.configure(fg=MUT)
        self.entry.bind("<FocusIn>", self.input_focus)
        self.entry.bind("<FocusOut>", self.input_blur)
        self.root.bind("<F11>", self.fullscreen)
        self.root.bind("<Escape>", self.escape)
        self.root.bind("<space>", self.pause)
        self.root.bind("<F6>", self.toggle_fps)
        self.root.bind("<F8>", self.stats)
        self.canvas.bind("<Configure>", self.resize)
        self.canvas.bind("<Button-1>", self.click)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        for i, (name, _, _, _) in enumerate(v3.NAV, 1):
            self.root.bind(f"<Control-Key-{i}>", lambda e, n=name: self.navigate(n))
        self.root.attributes("-fullscreen", self.full)
        self.previous_poll = tk._tkinter.getbusywaitinterval()
        self.frame_polling()
        self.root.update_idletasks()
        self.layout()
        self.animate()
        self.telemetry()

    def xy(self, x, y):
        return self.ox + x * self.s, self.oy + y * self.s

    def rect(self, box):
        return (*self.xy(*box[:2]), *self.xy(*box[2:]))

    def text(self, x, y, text, size=15, color=WHITE, anchor="w", bold=False, tag="ui"):
        return self.canvas.create_text(
            *self.xy(x, y),
            text=text,
            anchor=anchor,
            fill=color,
            font=(
                "Segoe UI",
                -max(9, round(size * self.s)),
                "bold" if bold else "normal",
            ),
            tags=tag,
        )

    def bar(self, x, y, w, value, tag="metrics"):
        self.canvas.create_rectangle(
            *self.rect((x, y, x + w, y + 7)), fill="#113348", outline="", tags=tag
        )
        if value is not None:
            self.canvas.create_rectangle(
                *self.rect((x, y, x + w * max(0, min(100, value)) / 100, y + 7)),
                fill=CY,
                outline="",
                tags=tag,
            )

    def resize(self, e):
        if self.layout_after:
            self.root.after_cancel(self.layout_after)
        self.layout_after = self.root.after(80, self.layout)

    def image(self, key, im, x, y):
        if key == "core" and key in self.photos:
            self.photos[key].paste(im)
            return
        ph = ImageTk.PhotoImage(im, master=self.root)
        self.photos[key] = ph
        if key not in self.items:
            self.items[key] = self.canvas.create_image(
                *self.xy(x, y), image=ph, anchor="nw"
            )
        else:
            self.canvas.itemconfigure(self.items[key], image=ph)
            self.canvas.coords(self.items[key], *self.xy(x, y))

    def layout(self):
        self.layout_after = None
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        self.s = min(w / W, h / H)
        self.ox = (w - W * self.s) / 2
        self.oy = (h - H * self.s) / 2
        if self.s <= 0:
            return
        self.canvas.delete("all")
        self.items.clear()
        self.photos.clear()
        self.image(
            "bg",
            self.bg
            if (w, h) == self.bg.size
            else self.bg.resize(
                (max(1, round(W * self.s)), max(1, round(H * self.s))), R.LANCZOS
            ),
            0,
            0,
        )
        self.core_size = (max(1, round(348 * self.s)),) * 2
        if self.selected == "Ana Sayfa":
            self.draw_core(self.elapsed())
        self.static_text()
        self.draw_page()
        self.draw_metrics()
        self.draw_footer()
        self.draw_notice()
        self.entry.configure(font=("Segoe UI", -max(10, round(18 * self.s))))
        self.entry.place(
            x=self.xy(200, 793)[0],
            y=self.xy(200, 793)[1],
            width=max(1, round(877 * self.s)),
            height=max(1, round(31 * self.s)),
        )

    def draw_nav(self):
        self.canvas.delete("nav")
        # Render vector icons and active highlight once per navigation, not per frame.
        im = Image.new("RGB", (921, 58), "#061a28")
        d = ImageDraw.Draw(im)
        for n, ic, l, r in v3.NAV:
            x = l - 369
            right = r - 369
            if n == self.selected:
                v3.rr(d, (x, 9, right, 55), 10, "#08405c", "#149fc6")
                v3.line(d, [(x + 12, 54), (right - 12, 54)], CY, 2)
            v3.icon(d, ic, x + 30, 31, 22, CY if n == self.selected else ICE)
            v3.txt(d, x + 49, 31, n, 15, ICE if n == self.selected else WHITE)
        self.image(
            "nav",
            im.resize(
                (max(1, round(921 * self.s)), max(1, round(58 * self.s))), R.LANCZOS
            ),
            369,
            0,
        )
        self.canvas.tag_raise(self.items["nav"])
        self.canvas.delete("window_controls")
        for x, label in ((1587, "−"), (1609, "□"), (1632, "×")):
            self.text(x, 23, label, 22, ICE, "center", tag="window_controls")

    def draw_page(self):
        self.pages.draw(self.selected)
        if "core" in self.items:
            self.canvas.itemconfigure(
                self.items["core"],
                state="normal" if self.selected == "Ana Sayfa" else "hidden",
            )
        self.draw_nav()

    def frame_polling(self):
        # Some CPython/Tk builds idle for 20 ms between events. Only use the
        # shorter poll while the reactor is animating; restore it on shutdown.
        active = self.selected == "Ana Sayfa" and not self.paused
        tk._tkinter.setbusywaitinterval(
            min(2, self.previous_poll) if active else self.previous_poll
        )

    def settings_window(self):
        from ..ai_settings import open_settings

        open_settings(self.root, self.profile)

    def cloud_window(self):
        from ..cloud_hud import open_center

        open_center(self.root, self.profile, background=self.operator.control)

    def navigate(self, name):
        if name not in [n[0] for n in v3.NAV]:
            return
        if name != self.selected:
            self.pages.record("Sekme açıldı: " + name)
        self.selected = name
        self.frame_polling()
        self.clear_notice()
        self.static_text()
        if name == "Ana Sayfa" and "core" not in self.items:
            self.draw_core(self.elapsed())
        self.draw_page()
        self.draw_metrics()
        self.draw_footer()
        self.draw_notice()

    def redraw_settings(self):
        if self.selected == "Ayarlar":
            self.pages.draw("Ayarlar")

    def elapsed(self):
        return self.frozen if self.paused else time.monotonic() - self.start

    def draw_core(self, t):
        im = self.core_back.copy()
        im.paste(self.core.render(t), (0, 0), self.core.mask)
        self.image("core", im.resize(self.core_size, R.BICUBIC), *CORE_BOX[:2])

    def static_text(self):
        self.canvas.delete("ui")
        self.text(1431, 38, "Yerel Core", 15, ICE, "center", True)
        self.text(178, 886, "Yerel izleme  |  Konuşma ve sonuçlar", 15, ICE)
        for x in [1175, 1186, 1198, 1210, 1222, 1343, 1355, 1367, 1379, 1390]:
            self.canvas.create_line(
                *self.xy(x, 803),
                *self.xy(x, 809),
                fill="#26748b",
                width=max(1, round(3 * self.s)),
                tags="ui",
            )
        if self.selected != "Ana Sayfa":
            return
        data = self.data
        routes = list(data["routes"].items())
        for i, y in enumerate((430, 479, 528)):
            alias, r = routes[i] if i < len(routes) else ("—", {})
            self.text(94, y, alias[:16], 16, WHITE, bold=True)
            self.text(247, y, r.get("state", "Yapılandırılmadı")[:26], 15, MUT)
        self.canvas.create_oval(
            *self.rect((56, 630, 157, 731)),
            outline="#143d52",
            width=max(2, round(9 * self.s)),
            tags="ui",
        )
        self.text(106, 678, "—", 28, CY, "center", True)
        self.text(106, 708, "Kota", 14, MUT, "center")
        self.text(196, 651, "Kalan: " + data["quota"], 15, MUT)
        self.text(
            196,
            683,
            "İstek: " + str(sum(u["attempts"] for u in data["usage"].values())),
            15,
            WHITE,
        )
        self.text(196, 712, "AI Merkezi • ayrıntılar", 15, MUT)
        counts = data["counts"]
        for x, r, label in (
            (1133, 1235, f"Aktif ({counts.get('in_progress', 0)})"),
            (1242, 1367, f"Bekleyen ({counts.get('pending', 0)})"),
            (1373, 1530, f"Tamamlanan ({counts.get('completed', 0)})"),
        ):
            self.canvas.create_rectangle(
                *self.rect((x, 140, r, 171)),
                fill="#092939",
                outline="#14536b",
                tags="ui",
            )
            self.text((x + r) / 2, 155, label, 14, ICE, "center")
        self.text(
            1150,
            207,
            f"{len(data['goals'])} hedef • {len(data['tasks'])} geliştirme görevi",
            16,
            ICE,
            bold=True,
        )
        for i, g in enumerate(list(data["goals"].values())[:3]):
            self.text(1150, 239 + i * 32, g["title"][:42], 15, MUT)
        audit = [f"{r['operation']} • {r['state']}" for r in data["requests"].values()]
        for i, msg in enumerate((audit or ["İşlem kaydı yok"])[:3]):
            self.text(1148, 417 + i * 32, msg[:45], 15, ICE)
        self.text(1148, 619, "Harcama politikası", 14, WHITE)
        self.text(1148, 645, "Sıkı" if data["strict"] else "Eski profil", 14, MUT)
        self.text(1148, 675, "Sandbox", 14, WHITE)
        self.text(1148, 705, data["sandbox"], 14, MUT)
        self.text(1402, 621, self.pages.events[-1][1][:25], 14, ICE)
        self.text(1402, 650, "Onay: " + str(counts.get("pending", 0)), 14, MUT)
        self.text(
            1402,
            679,
            {
                "Idle": "Ses: boşta",
                "Thinking": "Düşünüyor",
                "Speaking": "Konuşuyor",
                "Listening": "Dinliyor",
            }.get(self.voice_state, "—"),
            14,
            MUT,
        )
        for x, label, sub in (
            (626, "Aktif Mod", "Yerel Core"),
            (821, "Geliştirme görevleri", str(len(data["tasks"]))),
            (1013, "Kayıtlı yetenek", str(len(data["skills"]))),
        ):
            self.text(x, 688, label, 13, MUT, "center")
            self.text(x, 712, sub, 16, ICE, "center", True)

    def draw_metrics(self):
        self.canvas.delete("metrics")
        data, hist = self.monitor.snapshot()
        fresh = data and time.monotonic() - data.get("at", 0) < 6
        if not fresh:
            data = {}
            hist = {}

        def pct(k):
            v = data.get(k)
            return "—" if v is None else f"%{v:.0f}"

        if self.selected == "Ana Sayfa":
            for k, y in [("cpu", 164), ("gpu", 212), ("ram", 260)]:
                self.text(373, y, pct(k), 16, WHITE, "e", tag="metrics")
                self.bar(164, y + 11, 210, data.get(k))
                if k == "ram" and "used" in data:
                    self.text(
                        164,
                        y,
                        f"{data['used']:.1f} / {data['total']:.1f} GB",
                        15,
                        tag="metrics",
                    )
                self.graph(hist.get(k, []), 403, y - 7, 75, 27, 100)
            self.text(
                164,
                314,
                f"↓ {data['down']:.1f} Mbps" if "down" in data else "↓ —",
                15,
                ICE,
                tag="metrics",
            )
            self.text(
                309,
                314,
                f"↑ {data['up']:.1f} Mbps" if "up" in data else "↑ —",
                15,
                ICE,
                tag="metrics",
            )
            vals = hist.get("net", [])
            self.graph(
                vals,
                403,
                300,
                75,
                30,
                max(1, max((n for n in vals if n is not None), default=1)),
            )

    def draw_footer(self):
        self.canvas.delete("footer")
        now, date, day = clock_labels()
        data, status = self.weather.snapshot()
        city = str(data.get("city", self.weather.city))[:23]
        fresh = data and time.monotonic() - data.get("at", 0) < 1800
        if fresh:
            main = f"{city}  •  {data['temperature']:.0f}°C"
            sub = (
                f"{data['condition']} · Open-Meteo"
                if status == "Güncel"
                else "Son veri · Open-Meteo"
            )
        else:
            main = f"{city}  •  —°C"
            sub = status if not data else "Veri güncel değil"
        for l, r, top, bottom in (
            (944, 1217, main, sub),
            (1227, 1380, now, "Yerel saat"),
            (1390, 1625, date, day),
        ):
            self.canvas.create_rectangle(
                *self.rect((l, 866, r, 906)),
                fill="#061c2d",
                outline="#1a4d65",
                tags="footer",
            )
            self.text((l + r) / 2, 878, top, 15, ICE, "center", True, tag="footer")
            self.text((l + r) / 2, 895, bottom, 12, MUT, "center", tag="footer")

    def change_weather_city(self):
        value = simpledialog.askstring(
            "Hava durumu",
            "Türkiye’de bir şehir yazın. Hava durumunu kapatmak için boş bırakın.",
            initialvalue=self.weather.city,
            parent=self.root,
        )
        if value is None:
            return
        value = value.strip()[:60]
        if value and engine.config(self.profile)["offline"]:
            self.toast("Çevrimdışı mod: hava durumu için ağ izni gerekli.")
            return
        if value and not messagebox.askyesno(
            "Hava durumu izni",
            "Yalnızca ticari olmayan kullanım: şehri Open-Meteo hizmetine gönder ve hava durumunu güncelle?",
            parent=self.root,
        ):
            return
        if services.protected(self.profile, value):
            return self.toast("Korunan şehir değeri")
        services.prefs(
            self.profile, city=value or self.weather.city, weather=bool(value)
        )
        self.weather.configure(value or self.weather.city, bool(value))
        self.draw_footer()

    def graph(self, vals, x, y, w, h, cap):
        if len(vals) < 2:
            return
        points = []
        for i, v in enumerate(vals):
            if v is None:
                if len(points) >= 4:
                    self.canvas.create_line(*points, fill=CY, width=1, tags="metrics")
                points = []
                continue
            points.extend(self.xy(x + w * i / 29, y + h - h * max(0, min(1, v / cap))))
        if len(points) >= 4:
            self.canvas.create_line(
                *points, fill=CY, width=max(1, round(self.s)), tags="metrics"
            )

    def refresh_data(self, force=False):
        now = time.monotonic()
        revision = (
            self.profile.db.total_changes,
            self.profile.db.execute("PRAGMA data_version").fetchone()[0],
        )
        if (
            force
            or revision != getattr(self, "revision", None)
            or now - self.data_at > 5
        ):
            self.revision = revision
            self.data_at = now
            data = services.snapshot(self.profile)
            if data != self.data:
                changes = [
                    r
                    for ident, r in data["requests"].items()
                    if self.data["requests"].get(ident) != r
                ]
                self.data = data
                if changes:
                    self.toast(changes[0]["operation"] + " • " + changes[0]["state"])
                self.static_text()
                self.pages.draw(self.selected)
            if self.weather.enabled and data["offline"]:
                self.weather.configure(self.weather.city, False)

    def voice_changed(self, state):
        if self.closed:
            return
        self.voice_state = state
        if self.selected == "Ana Sayfa":
            self.static_text()

    def telemetry(self):
        if self.closed:
            return
        self.refresh_data()
        self.draw_metrics()
        self.draw_footer()
        self.metric_after = self.root.after(1000, self.telemetry)

    def animate(self):
        if self.closed:
            return
        start = time.perf_counter()
        if (
            self.selected == "Ana Sayfa"
            and not self.paused
            and self.root.state() != "iconic"
            and self.layout_after is None
        ):
            self.draw_core(self.elapsed())
            self.render_times.append((time.perf_counter() - start) * 1000)
        period = 1 / self.fps
        if self.selected != "Ana Sayfa" or self.paused:
            self.next_frame = start
            delay = 100
        else:
            self.next_frame = (
                max(start - period, getattr(self, "next_frame", start)) + period
            )
            delay = max(1, round((self.next_frame - time.perf_counter()) * 1000))
        self.anim_after = self.root.after(delay, self.animate)

    def draw_notice(self):
        self.canvas.delete("notice")
        if self.notice:
            self.canvas.create_rectangle(
                *self.rect((480, 744, 1168, 773)),
                fill="#092335",
                outline="#14738c",
                tags="notice",
            )
            self.text(824, 759, self.notice[:95], 14, ICE, "center", tag="notice")

    def cancel_notice_timer(self):
        if self.notice_after is not None:
            self.root.after_cancel(self.notice_after)
            self.notice_after = None

    def clear_notice(self):
        self.cancel_notice_timer()
        self.notice = ""
        if not self.closed:
            self.canvas.delete("notice")

    def toast(self, s):
        if self.closed:
            return
        self.cancel_notice_timer()
        self.notice = s
        if s:
            self.pages.record(s[:95])
            if self.selected == "Ana Sayfa":
                self.static_text()
        self.draw_notice()
        if s:
            self.notice_after = self.root.after(self.NOTICE_MS, self.clear_notice)

    def input_focus(self, e=None):
        if self.entry.get() == self.placeholder:
            self.entry.delete(0, "end")
            self.entry.configure(fg=WHITE)

    def input_blur(self, e=None):
        if not self.entry.get():
            self.entry.insert(0, self.placeholder)
            self.entry.configure(fg=MUT)

    def submit(self, e=None):
        if self.entry.get().strip() and self.entry.get() != self.placeholder:
            text = self.entry.get().strip()
            self.entry.delete(0, "end")
            self.operator.submit(text)
        return "break"

    def click(self, e):
        x = (e.x - self.ox) / self.s
        y = (e.y - self.oy) / self.s
        if 944 < x < 1217 and 866 < y < 906:
            self.change_weather_city()
        elif 1438 < x < 1518 and 779 < y < 834:
            self.submit()
        elif 1245 < x < 1321 and 769 < y < 844:
            open_voice(
                self.root, self.profile, on_state=self.voice_changed, strict_only=True
            )
        elif y < 60 and 369 < x < 1286:
            for n, ic, l, r in v3.NAV:
                if l <= x <= r:
                    self.navigate(n)
                    break
        elif 164 < x < 704 and 869 < y < 905:
            self.operator.show("")
        elif 1080 < x < 1133 and 779 < y < 834:
            self.toast("Dosya yükleme bağlı değil.")
        elif 1618 < x < 1645 and y < 40:
            self.close()
        elif 1598 < x < 1618 and y < 40:
            self.fullscreen()
        elif 1578 < x < 1598 and y < 40:
            self.set_full(False)
            self.root.iconify()
        elif self.selected != "Ana Sayfa" and 74 < y < 755:
            self.pages.click(x, y)
        elif self.selected == "Ana Sayfa" and 20 < x < 496 and 89 < y < 344:
            self.toast("Canlı CPU/RAM/ağ: psutil; GPU: NVIDIA-SMI. — = veri yok.")

    def pause(self, e=None):
        if self.root.focus_get() == self.entry:
            return
        self.toggle_pause()
        return "break"

    def toggle_pause(self):
        if self.paused:
            self.start = time.monotonic() - self.frozen
        else:
            self.frozen = self.elapsed()
        self.paused = not self.paused
        self.frame_polling()
        services.prefs(self.profile, paused=self.paused)
        self.pages.record(
            "Çekirdek animasyonu " + ("duraklatıldı" if self.paused else "sürdürüldü")
        )
        self.redraw_settings()

    def set_fps(self, fps):
        self.fps = fps
        services.prefs(self.profile, fps=fps)
        self.pages.record(f"Animasyon hedefi: {fps} FPS")
        self.redraw_settings()
        self.toast(f"Animasyon hedefi: {fps} FPS")

    def toggle_fps(self, e=None):
        self.set_fps(30 if self.fps == 60 else 60)

    def stats(self, e=None):
        v = list(self.render_times)
        self.toast(
            f"Render ort.: {sum(v) / len(v):.1f} ms | FPS hedefi: {self.fps}"
            if v
            else "Render ölçümü bekleniyor"
        )

    def set_full(self, full):
        self.full = bool(full)
        services.prefs(self.profile, fullscreen=self.full)
        self.root.attributes("-fullscreen", self.full)
        self.redraw_settings()

    def fullscreen(self, e=None):
        self.set_full(not self.full)

    def escape(self, e=None):
        if self.full:
            self.fullscreen()
        else:
            self.close()

    def close(self):
        if self.closed:
            return
        self.clear_notice()
        self.closed = True
        self.operator.close()
        for a in (self.layout_after, self.anim_after, self.metric_after):
            if a:
                try:
                    self.root.after_cancel(a)
                except tk.TclError:
                    pass
        self.photos.clear()
        tk._tkinter.setbusywaitinterval(self.previous_poll)
        self.root.destroy()
        self.monitor.close()
        self.weather.close()
        self.pages.actions.clear()
        self.pages.a = None
        self.operator.a = None
        self.operator.window = None
        self.operator.done = None

    def run(self):
        self.root.mainloop()
