"""Native Canvas pages for the standalone HUD; no fabricated Core data."""

from __future__ import annotations
import time
from . import raya_hud as style

CY, ICE, WHITE, MUT = style.CY, style.ICE, style.WHITE, style.MUT


class Pages:
    def __init__(self, app):
        self.a = app
        self.actions = []
        self.events = [(time.strftime("%H:%M:%S"), "Yerel HUD başlatıldı")]

    def record(self, message):
        self.events.append((time.strftime("%H:%M:%S"), message))
        self.events = self.events[-40:]

    def rect(self, box, fill, outline="", width=1):
        return self.a.canvas.create_rectangle(
            *self.a.rect(box),
            fill=fill,
            outline=outline,
            width=max(1, round(width * self.a.s)),
            tags="page",
        )

    def poly(self, pts, fill, outline="", width=1):
        return self.a.canvas.create_polygon(
            *[p for x, y in pts for p in self.a.xy(x, y)],
            fill=fill,
            outline=outline,
            width=max(1, round(width * self.a.s)),
            tags="page",
        )

    def line(self, pts, color="#17465e", width=1):
        self.a.canvas.create_line(
            *[p for x, y in pts for p in self.a.xy(x, y)],
            fill=color,
            width=max(1, round(width * self.a.s)),
            tags="page",
        )

    def text(self, x, y, s, size=17, color=WHITE, bold=False, anchor="w"):
        return self.a.text(x, y, s, size, color, anchor, bold, tag="page")

    def panel(self, x, y, r, b, title, code=""):
        self.poly(
            [
                (x + 15, y),
                (r - 15, y),
                (r, y + 15),
                (r, b - 15),
                (r - 15, b),
                (x + 15, b),
                (x, b - 15),
                (x, y + 15),
            ],
            "#051a29",
            "#167c9d",
        )
        self.line([(x + 15, y), (x + 150, y)], CY, 2)
        self.line([(x + 15, b), (r - 15, b)], "#0c4b65")
        self.text(x + 22, y + 28, title, 19, ICE, True)
        self.line([(x + 18, y + 51), (r - 18, y + 51)])
        if code:
            self.text(r - 20, y + 28, code, 13, MUT, anchor="e")

    def badge(self, x, y, s, color="#ffba50"):
        self.text(x, y, "●  " + s, 14, color, True)

    def button(self, box, label, fn, active=False):
        self.rect(box, "#0b3b51" if active else "#092537", CY if active else "#22627b")
        x, y, r, b = box
        self.text((x + r) / 2, (y + b) / 2, label, 16, ICE, True, "center")
        self.actions.append((box, fn))

    def row(self, x, y, r, name, description, status="Core gerekli"):
        self.text(x, y, name, 18, WHITE, True)
        self.text(x, y + 27, description, 15, MUT)
        self.text(r, y, status, 14, "#ffba50", anchor="e")
        self.line([(x, y + 48), (r, y + 48)], "#12394b")

    def empty(self, x, y, title, desc):
        self.text(x, y, "◇", 46, "#1d7594", anchor="center")
        self.text(x, y + 46, title, 22, ICE, True, "center")
        self.text(x, y + 80, desc, 15, MUT, anchor="center")

    def draw(self, name):
        self.a.canvas.delete("page")
        self.actions = []
        if name == "Ana Sayfa":
            return
        self.rect((0, 74, 1648, 755), "#03101c")
        for x in range(0, 1648, 42):
            self.line([(x, 75), (x, 754)], "#071e2b")
        for y in range(80, 755, 42):
            self.line([(0, y), (1648, y)], "#071e2b")
        titles = {
            "Yetenekler": (
                "YETENEK KÜTÜPHANESİ",
                "Yetenek keşfi, ayrıntılar ve çalıştırma izinleri",
            ),
            "Görevler": ("GÖREV KONTROL MERKEZİ", "Aktif işler, inceleme ve geçmiş"),
            "AI Merkezi": (
                "YAPAY ZEKÂ KONTROL MERKEZİ",
                "Sağlayıcılar, model kataloğu, kullanım ve maliyet politikası",
            ),
            "İş Akışı": (
                "İŞ AKIŞI STÜDYOSU",
                "Planlama, bağımlılıklar, yürütme ve doğrulama",
            ),
            "Güvenlik": (
                "GÜVENLİK MERKEZİ",
                "İzinler, anahtar kasası ve yürütme güvenliği",
            ),
            "Ayarlar": ("SİSTEM AYARLARI", "Yerel HUD ayarları ve çalışma durumu"),
        }
        title, sub = titles[name]
        self.text(32, 106, title, 28, ICE, True)
        self.text(34, 138, sub, 16, MUT)
        self.badge(1330, 110, "YEREL HUD", CY)
        self.text(1616, 139, "Core profili bağlı", 14, MUT, anchor="e")
        self.line([(32, 157), (1616, 157)], "#125571", 2)
        {
            "Yetenekler": self.skills,
            "Görevler": self.tasks,
            "AI Merkezi": self.ai,
            "İş Akışı": self.flows,
            "Güvenlik": self.security,
            "Ayarlar": self.settings,
        }[name]()

    def skills(self):
        skills = self.a.data["skills"]
        self.panel(25, 176, 365, 735, "KATEGORİLER", "01")
        self.row(
            48,
            252,
            340,
            "Tüm yetenekler",
            "Kayıtlı yerel metin işlemleri",
            str(len(skills)),
        )
        self.panel(383, 176, 1144, 735, "YETENEK LİSTESİ", str(len(skills)))
        chosen = getattr(self, "skill_key", None)
        if chosen not in skills:
            chosen = next(iter(skills), None)

        def select(key):
            self.skill_key = key
            self.draw("Yetenekler")

        for i, (key, m) in enumerate(list(skills.items())[:5]):
            y = 250 + i * 71
            self.row(408, y, 1119, key, m["operation"], "Kayıtlı")
            self.actions.append(((404, y - 16, 1120, y + 43), lambda k=key: select(k)))
        if not skills:
            self.empty(
                764, 380, "Kayıtlı yetenek yok", "Core üzerinden bir yetenek kaydedin."
            )
        else:
            self.button(
                (408, 657, 675, 708),
                "Tüm yetenekler",
                lambda: self.a.operator.choose(
                    "Yetenekler", [(k, k) for k in skills], select
                ),
            )
        self.panel(1162, 176, 1623, 735, "YETENEK AYRINTISI")
        if chosen:
            m = skills[chosen]
            for i, (k, v) in enumerate(
                [
                    ("İşlem", m["operation"]),
                    ("Girdi → çıktı", m["input"] + " → " + m["output"]),
                    ("İzinler", ", ".join(m["permissions"]) or "Ek izin yok"),
                    ("Çalışma ortamı", m["runtime"]),
                ]
            ):
                self.row(1186, 255 + i * 89, 1597, k, v, "")
            self.button(
                (1186, 648, 1597, 705),
                "Çalıştır / incele",
                lambda: self.a.operator.skill(chosen),
            )

    def task_rows(self):
        d = self.a.data
        return [
            (
                k,
                i,
                v.get("title") or v.get("operation") or v.get("kind") or i,
                v.get("state") or v.get("phase"),
            )
            for k in ("requests", "tasks", "goals")
            for i, v in d[k].items()
        ]

    def pick_task(self, rows):
        self.a.operator.choose(
            "Görev inceleme",
            [((k, i), f"{title} • {state} • {i}") for k, i, title, state in rows],
            lambda pair: self.a.operator.manage(
                "dev_tasks" if pair[0] == "tasks" else pair[0], pair[1]
            ),
        )

    def tasks(self):
        d = self.a.data
        for i, (name, count) in enumerate(
            [
                ("AKTİF", sum(g["state"] == "active" for g in d["goals"].values())),
                ("BEKLEYEN ONAY", d["counts"].get("pending", 0)),
                ("TAMAMLANAN", d["counts"].get("completed", 0)),
                ("HATA", d["counts"].get("failed", 0)),
            ]
        ):
            x = 25 + i * 403
            self.panel(x, 176, x + 385, 301, name)
            self.text(x + 24, 263, str(count), 32, ICE, True)
        self.panel(25, 320, 1080, 735, "GÖREV KUYRUĞU", "Core kayıtları")
        rows = self.task_rows()
        for i, (kind, ident, title, state) in enumerate(rows[:4]):
            self.row(48, 405 + i * 68, 1059, title[:52], ident, state)
        if not rows:
            self.empty(
                550,
                500,
                "Görev kaydı yok",
                "Hedefler, geliştirme görevleri ve onaylar burada görünür.",
            )
        self.panel(1098, 320, 1623, 735, "GÖREV İNCELEME")
        self.text(1122, 400, "Plan, kapsam ve izinler", 22, ICE, True)
        self.text(1122, 448, "Her istek kimlik ve özet ile incelenir.", 16, MUT)
        self.button(
            (1122, 598, 1597, 656),
            "Kayıt seç / sonucu gör",
            lambda: self.pick_task(rows),
        )

    def ai(self):
        d = self.a.data
        self.panel(25, 176, 482, 735, "SAĞLAYICILAR", str(len(d["routes"])))
        for i, (alias, r) in enumerate(list(d["routes"].items())[:4]):
            self.row(49, 256 + i * 82, 457, alias[:24], r["model"][:38], r["provider"])
        if not d["routes"]:
            self.empty(250, 370, "Rota yok", "Sağlayıcı ayarlarından yapılandırın.")
        self.panel(500, 176, 1109, 735, "MODEL KATALOĞU")
        for i, (alias, r) in enumerate(list(d["routes"].items())[:4]):
            self.row(
                524,
                254 + i * 83,
                1080,
                alias[:18],
                r["state"][:59],
                str(r["model_count"]) + " model",
            )
        self.button((49, 648, 457, 704), "Sağlayıcı ayarları", self.a.settings_window)
        self.button((524, 648, 1080, 704), "AI kontrol merkezi", self.a.cloud_window)
        self.panel(1127, 176, 1623, 420, "KOTALAR & MALİYET")
        for i, (k, v) in enumerate(
            [
                ("Kota gözlemi", len(d["quotas"])),
                ("Deneme", sum(v["attempts"] for v in d["usage"].values())),
                ("Kalan", d["quota"]),
            ]
        ):
            self.text(1152, 256 + i * 55, k, 17, MUT)
            self.text(1597, 256 + i * 55, str(v), 19, ICE, anchor="e")
        self.panel(1127, 438, 1623, 735, "ROTA & GÜVENLİK")
        self.text(
            1152,
            518,
            "Sıkı harcama" if d["strict"] else "Eski profil • üretim kapalı",
            20,
            ICE,
            True,
        )
        self.text(1152, 563, "Katalog erişimi ve üretim izni ayrı izlenir.", 16, MUT)
        self.text(1152, 605, "Canlı çıkarım: istek makbuzuna bakın.", 16, MUT)

    def flows(self):
        self.panel(25, 176, 1110, 735, "İŞ AKIŞI ALANI", "Core geliştirme")
        for i, (s, d) in enumerate(
            [
                ("PLAN", "Görevi tanımla"),
                ("ONAY", "Kapsamı incele"),
                ("YÜRÜT", "Yalıtılmış test"),
                ("DOĞRULA", "Öneriyi incele"),
            ]
        ):
            x = 60 + i * 260
            self.rect((x, 347, x + 210, 458), "#082638", "#226f89")
            self.text(x + 105, 375, s, 18, ICE, True, "center")
            self.text(x + 105, 416, d, 15, MUT, anchor="center")
            if i < 3:
                self.line([(x + 210, 403), (x + 258, 403)], "#1b9bc3", 2)
                self.text(x + 234, 387, "›", 24, CY, anchor="center")
        self.text(
            568, 535, "Özel akış tasarımcısı kullanılamıyor", 17, MUT, anchor="center"
        )
        self.panel(1128, 176, 1623, 735, "YÜRÜTME DETAYI")
        rows = [r for r in self.task_rows() if r[0] == "tasks"]
        for i, (_, ident, title, phase) in enumerate(rows[:4]):
            self.row(1150, 254 + i * 81, 1598, title, ident, phase)
        self.button(
            (1150, 655, 1598, 707), "Geliştirme görevleri", lambda: self.pick_task(rows)
        )
        self.button((60, 655, 393, 707), "Görev başlat", self.a.cloud_window)

    def security(self):
        d = self.a.data
        self.panel(25, 176, 811, 735, "GÜVENLİK DENETİMLERİ")
        for i, (s, desc, state) in enumerate(
            [
                (
                    "Sıfır maliyet politikası",
                    "Üretim için doğrulanmış rota ve onay gerekir.",
                    "Sıkı" if d["strict"] else "Eski profil",
                ),
                (
                    "Anahtar kasası",
                    "Sağlayıcı anahtarları Core kasasında yönetilir.",
                    "Yerel",
                ),
                (
                    "İşlem onayları",
                    "İstek kimliği, içerik özeti ve durum doğrulanır.",
                    str(d["counts"].get("pending", 0)),
                ),
                (
                    "Sandbox",
                    "Kısıtlı geliştirme çalıştırıcısı her testte denetlenir.",
                    d["sandbox"],
                ),
                ("Gizlilik", "Donanım telemetrisi yerel okunur.", "Yerel"),
            ]
        ):
            self.row(49, 249 + i * 92, 786, s, desc, state)
        self.panel(829, 176, 1623, 735, "OLAY KAYDI", "Core / oturum")
        events = [
            (r["state"], r["operation"] + " • " + i[:12])
            for i, r in d["requests"].items()
        ]
        for i, (stamp, msg) in enumerate((events[:5] + self.events[-4:])[-9:]):
            self.text(856, 250 + i * 49, stamp, 15, MUT)
            self.text(980, 250 + i * 49, msg[:61], 16, ICE)
            self.line([(853, 270 + i * 49), (1597, 270 + i * 49)])

    def settings(self):
        self.panel(25, 176, 811, 735, "GÖRÜNTÜ & PERFORMANS")
        self.text(49, 255, "Ekran modu", 20, WHITE, True)
        self.text(49, 286, "Tam ekran veya pencere", 15, MUT)
        self.button(
            (455, 240, 615, 291),
            "Tam ekran",
            lambda: self.a.set_full(True),
            self.a.full,
        )
        self.button(
            (630, 240, 786, 291),
            "Pencere",
            lambda: self.a.set_full(False),
            not self.a.full,
        )
        self.line([(49, 316), (786, 316)])
        self.text(49, 360, "Animasyon hedefi", 20, WHITE, True)
        self.text(49, 391, "Kare hızı hedefi; ölçüm garantisi değil", 15, MUT)
        self.button(
            (455, 347, 615, 398), "30 FPS", lambda: self.a.set_fps(30), self.a.fps == 30
        )
        self.button(
            (630, 347, 786, 398), "60 FPS", lambda: self.a.set_fps(60), self.a.fps == 60
        )
        self.line([(49, 425), (786, 425)])
        self.text(49, 470, "Enerji çekirdeği", 20, WHITE, True)
        self.text(49, 501, "Sabit orbital düzlemi • 80 sn çekirdek", 15, MUT)
        self.button(
            (455, 457, 786, 508),
            "Devam et" if self.a.paused else "Duraklat",
            self.a.toggle_pause,
        )
        self.line([(49, 535), (786, 535)])
        self.text(49, 580, "Görsel tema", 20, WHITE, True)
        self.text(49, 615, "REACTOR / Neon cyan", 17, ICE)
        self.button(
            (49, 651, 786, 707),
            "Hava durumu • " + self.a.weather.city,
            self.a.change_weather_city,
        )
        self.panel(829, 176, 1623, 432, "SİSTEM BİLGİSİ")
        import platform

        for i, (k, val) in enumerate(
            [
                ("Python", platform.python_version()),
                ("İşletim sistemi", platform.system()),
                ("Arayüz motoru", "Tkinter + Pillow"),
                ("Donanım ölçümü", "psutil / NVIDIA-SMI"),
            ]
        ):
            self.text(853, 252 + i * 42, k, 16, MUT)
            self.text(1598, 252 + i * 42, val, 16, WHITE, anchor="e")
        self.panel(829, 451, 1623, 735, "KLAVYE KISAYOLLARI")
        for i, (k, v) in enumerate(
            [
                ("F11", "Tam ekran geçişi"),
                ("F6", "30 / 60 FPS"),
                ("F8", "Render ölçümü"),
                ("Space", "Animasyonu duraklat"),
                ("Ctrl + 1–7", "Sekme değiştir"),
                ("ESC", "Tam ekrandan çık / kapat"),
            ]
        ):
            self.text(853, 521 + i * 34, k, 15, ICE, True)
            self.text(1110, 521 + i * 34, v, 15, MUT)

    def click(self, x, y):
        for (l, t, r, b), fn in self.actions:
            if l <= x <= r and t <= y <= b:
                fn()
                return True
        return False
