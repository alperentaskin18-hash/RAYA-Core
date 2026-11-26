"""Optional local screen capture and bounded image/animation inspection."""

import hashlib
import os
import re
import tempfile

from .storage import child
from .core import propose, require


def image_path(profile, name):
    require(
        isinstance(name, str)
        and re.fullmatch(r"[a-zA-Z0-9_-]{1,64}\.(?:png|gif|jpg|webp)", name),
        "invalid image name",
    )
    path = child(child(profile.directory, "exports", directory=True), name)
    require(not path.parent.is_symlink() and not path.is_symlink(), "unsafe image path")
    return path


def plan(profile, args):
    path = image_path(profile, args["name"])
    require(
        path.suffix == ".png" and not os.path.lexists(path),
        "new PNG destination required",
    )
    return propose(
        profile,
        "capture",
        path.name,
        {"display": "current desktop", "format": "PNG"},
        "absent",
        ["screen.read", "profile.export"],
    )


def capture(profile, name):
    from PIL import ImageGrab

    path = image_path(profile, name)
    path.parent.mkdir(mode=0o700, exist_ok=True)
    image = ImageGrab.grab()
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as out:
        try:
            image.save(out, "PNG")
            out.close()
            os.link(out.name, path)
        finally:
            os.unlink(out.name)
    return {
        "name": name,
        "size": list(image.size),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def inspect(profile, name, frame):
    from PIL import Image

    path = image_path(profile, name)
    require(
        path.is_file() and path.stat().st_size <= 16 * 1024 * 1024,
        "image unavailable or too large",
    )
    with Image.open(path, formats=["PNG", "GIF", "JPEG", "WEBP"]) as image:
        require(image.width * image.height <= 16_000_000, "image dimensions too large")
        require(
            type(frame) is int and 0 <= frame <= 120, "frame outside inspection limit"
        )
        try:
            image.seek(frame)
        except EOFError as exc:
            raise ValueError("frame unavailable") from exc
        mean = (
            image.convert("RGB")
            .resize((1, 1), resample=Image.Resampling.BOX)
            .getpixel((0, 0))
        )
        return {
            "format": image.format,
            "width": image.width,
            "height": image.height,
            "frame": frame,
            "duration_ms": image.info.get("duration"),
            "mean_rgb": mean,
        }
