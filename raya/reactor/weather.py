"""Optional city-level weather, no GPS or API credentials. Personal-use endpoint."""

from datetime import datetime
import http.client
import json
import math
import threading
import time
from urllib.parse import urlencode

DAYS = ("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar")
MONTHS = (
    "Ocak",
    "Şubat",
    "Mart",
    "Nisan",
    "Mayıs",
    "Haziran",
    "Temmuz",
    "Ağustos",
    "Eylül",
    "Ekim",
    "Kasım",
    "Aralık",
)


def clock_labels(now=None):
    now = now or datetime.now()
    return (
        now.strftime("%H:%M:%S"),
        f"{now.day} {MONTHS[now.month - 1]} {now.year}",
        DAYS[now.weekday()],
    )


def condition(code):
    if code == 0:
        return "Açık"
    if code == 1:
        return "Az bulutlu"
    if code == 2:
        return "Parçalı bulutlu"
    if code == 3:
        return "Kapalı"
    if code in (45, 48):
        return "Sisli"
    if code in (51, 53, 55, 56, 57):
        return "Çisenti"
    if code in (61, 63, 65, 66, 67, 80, 81, 82):
        return "Yağmurlu"
    if code in (71, 73, 75, 77, 85, 86):
        return "Karlı"
    if code in (95, 96, 99):
        return "Gök gürültülü"
    return "Durum bilinmiyor"


def read_json(host, path, params):
    conn = http.client.HTTPSConnection(host, timeout=5)
    try:
        conn.request(
            "GET",
            path + "?" + urlencode(params),
            headers={"User-Agent": "RAYA-HUD/6.1", "Accept": "application/json"},
        )
        with conn.getresponse() as response:
            if response.status != 200:
                raise ValueError("Hava servisi kullanılamıyor")
            raw = response.read(512001)
    finally:
        conn.close()
    if len(raw) > 512000:
        raise ValueError("Response too large")
    return json.loads(raw)


def resolve(city):
    # Approximate public city-centre coordinates; never read device location.
    if city.casefold() == "isparta":
        return "Isparta", 37.76, 30.55
    results = read_json(
        "geocoding-api.open-meteo.com",
        "/v1/search",
        {"name": city, "count": 5, "language": "tr", "countryCode": "TR"},
    ).get("results", [])
    if not results:
        raise ValueError("Şehir bulunamadı")
    r = results[0]
    return str(r["name"])[:40], float(r["latitude"]), float(r["longitude"])


def fetch(coords):
    name, lat, lon = coords
    result = read_json(
        "api.open-meteo.com",
        "/v1/forecast",
        {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,weather_code",
            "temperature_unit": "celsius",
            "timezone": "auto",
            "forecast_days": 1,
        },
    )
    cur = result["current"]
    temp = float(cur["temperature_2m"])
    code = int(cur["weather_code"])
    if not math.isfinite(temp) or not -100 <= temp <= 100:
        raise ValueError("Invalid temperature")
    return {
        "city": name,
        "temperature": temp,
        "condition": condition(code),
        "at": time.monotonic(),
    }


class Weather:
    def __init__(self, city="Isparta", enabled=False):
        self.changed = threading.Condition()
        self.stopped = False
        self.thread = None
        self.generation = 0
        self.configure(city, enabled)

    def configure(self, city, enabled):
        with self.changed:
            self.generation += 1
            self.city, self.enabled = city, enabled
            self.data = {}
            self.status = "Güncelleniyor" if enabled else "Hava durumu kapalı"
            if enabled and self.thread is None:
                self.thread = threading.Thread(target=self.loop, name="reactor-weather")
                self.thread.start()
            self.changed.notify_all()

    def loop(self):
        while True:
            with self.changed:
                self.changed.wait_for(lambda: self.enabled or self.stopped)
                if self.stopped:
                    return
                generation, city = self.generation, self.city
            try:
                coords = resolve(city)
                with self.changed:
                    if self.stopped or self.generation != generation:
                        continue
                data = fetch(coords)
                status, delay = "Güncel", 900
            except (
                ValueError,
                KeyError,
                IndexError,
                TypeError,
                OSError,
                http.client.HTTPException,
            ):
                data, status, delay = {}, "Veri alınamadı", 300
            with self.changed:
                if generation == self.generation:
                    self.data, self.status = data, status
                self.changed.wait_for(
                    lambda: self.stopped or self.generation != generation, timeout=delay
                )

    def snapshot(self):
        with self.changed:
            return dict(self.data), self.status

    def close(self):
        with self.changed:
            self.stopped = True
            self.changed.notify_all()
        if self.thread:
            self.thread.join()
