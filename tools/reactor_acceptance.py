"""Capture the actual Tk window and time delivered frames on a synthetic profile."""

import argparse
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import psutil
from PIL import Image, ImageChops, ImageGrab, ImageStat
from raya import engine, goals
from raya.core import Profile, plan_install, plan_run, decide
from raya.reactor import services
from raya.reactor.live_ui import LiveApp, v3


def sample(app, seconds):
    loop = app.root
    stamps = []
    render = app.draw_core

    def measured(t):
        render(t)
        stamps.append(time.perf_counter())

    app.draw_core = measured
    process = psutil.Process()
    process.cpu_percent(None)
    loop.after(round(seconds * 1000), loop.quit)
    loop.mainloop()
    app.draw_core = render
    intervals = [1000 * (b - a) for a, b in zip(stamps, stamps[1:])]
    return {
        "frames": len(stamps),
        "fps": round(1000 / statistics.mean(intervals), 2),
        "frame_interval_ms_p50": round(statistics.median(intervals), 2),
        "frame_interval_ms_p95": round(statistics.quantiles(intervals, n=20)[18], 2),
        "process_cpu_percent": process.cpu_percent(None),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        p = Profile(tmp)
        with p.transaction():
            engine.configure(
                p,
                {
                    "offline": True,
                    "routes": {
                        "Gemini": {"provider": "gemini", "model": "profil-modeli"},
                        "OmniRoute": {
                            "provider": "omniroute",
                            "model": "auto/best-coding",
                        },
                    },
                },
            )
            goals.change(p, "goal-create", {"title": "HUD kabul kontrolü"})
            req = plan_install(
                p,
                dict(
                    action="buyuk",
                    provider="deneme",
                    version="1",
                    license="Apache-2.0",
                    operation="upper",
                    input="string",
                    output="string",
                    runtime="raya-text-1",
                    permissions=[],
                ),
            )
            decide(p, req["id"], req["digest"], True)
            plan_run(p, "deneme/buyuk", "Merhaba")
        services.prefs(p, fullscreen=False)
        ref = Image.open(v3.HERE / "reference.png").convert("RGB")
        start = time.perf_counter()
        app = LiveApp(p)
        try:
            root = app.root
            root.geometry(f"{ref.width}x{ref.height}+0+0")
            root.update()
            app.layout()
            root.update_idletasks()
            metrics = {
                "startup_to_draw_ms": round((time.perf_counter() - start) * 1000, 2),
                "size": ref.size,
                "host": sys.platform,
                "python": sys.version.split()[0],
            }
            for fps in (30, 60):
                app.set_fps(fps)
                metrics[str(fps)] = sample(app, 4)
            latency = []
            for i, (name, *_) in enumerate(v3.NAV):
                start = time.perf_counter()
                app.navigate(name)
                root.update_idletasks()
                latency.append((time.perf_counter() - start) * 1000)
                x, y = app.canvas.winfo_rootx(), app.canvas.winfo_rooty()
                shot = ImageGrab.grab(
                    bbox=(
                        x,
                        y,
                        x + app.canvas.winfo_width(),
                        y + app.canvas.winfo_height(),
                    )
                ).convert("RGB")
                if shot.size != ref.size:
                    raise RuntimeError("Reference-sized display required")
                shot.save(
                    args.directory
                    / f"{i + 1:02d}-{('home', 'skills', 'tasks', 'ai', 'workflows', 'security', 'settings')[i]}.png"
                )
                if i == 0:
                    # The logo, left panel headings and reactor title are static in the supplied reference.
                    regions = {
                        "header_logo": (20, 5, 347, 55),
                        "system_heading": (48, 96, 461, 130),
                        "reactor_heading": (666, 83, 989, 130),
                    }
                    metrics["static_regions"] = {}
                    for k, box in regions.items():
                        coords = tuple(
                            round(n * app.s + (app.ox if j % 2 == 0 else app.oy))
                            for j, n in enumerate(box)
                        )
                        delta = ImageChops.difference(
                            shot.crop(coords), ref.crop(coords)
                        )
                        metrics["static_regions"][k] = {
                            "box": coords,
                            "mean_absolute_rgb_error": round(
                                statistics.mean(ImageStat.Stat(delta).mean), 3
                            ),
                        }
            metrics["tab_to_draw_ms"] = dict(
                zip([x[0] for x in v3.NAV], (round(v, 2) for v in latency))
            )
            info = psutil.Process().memory_info()
            if os.name == "nt":
                peak = info.peak_wset
            else:
                import resource

                peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (
                    1 if sys.platform == "darwin" else 1024
                )
            metrics["peak_rss_mib"] = round(peak / (1024**2), 2)
            (args.directory / "measurements.json").write_text(
                json.dumps(metrics, ensure_ascii=False, indent=2) + "\n",
                encoding="utf8",
            )
            print(json.dumps(metrics, ensure_ascii=False))
        finally:
            app.close()
            p.db.close()


if __name__ == "__main__":
    main()
