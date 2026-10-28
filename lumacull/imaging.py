from __future__ import annotations

import io
import math
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageCms, ImageDraw, ImageFont, ImageOps

from .config import (
    JPEG_QUALITY_PREVIEW,
    JPEG_QUALITY_THUMB,
    PREVIEW_EDGE,
    THUMB_SIZE,
    local_data_dir,
)

Image.MAX_IMAGE_PIXELS = 320_000_000

try:  # Apple photos are common in a photographer's library.
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:  # pragma: no cover - optional dependency
    pass

try:
    Image.init()
except Exception:
    pass

EXIF_TAGS = {
    271: "make",
    272: "model",
    315: "artist",
    33432: "copyright",
    42036: "lens",
    42035: "lens_make",
    33437: "fnumber",
    33434: "exposure",
    34855: "iso",
    37386: "focal",
    36867: "taken_at",
    36868: "digitized_at",
    306: "modified_at",
}

THUMB_CACHE = local_data_dir() / "cache" / "thumbs"


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8", "ignore")
    text = str(value).replace("\x00", "").strip()
    return text or None


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if isinstance(value, (tuple, list)) and len(value) == 2:
            denominator = float(value[1])
            return float(value[0]) / denominator if denominator else None
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def format_bytes(size: int | float) -> str:
    size = float(size)
    if size < 1024:
        return f"{size:.0f} B"
    if size < 1024**2:
        return f"{size / 1024:.0f} KB"
    if size < 1024**3:
        return f"{size / 1024**2:.1f} MB"
    return f"{size / 1024**3:.2f} GB"


def _format_shutter(value: Any) -> str | None:
    seconds = _number(value)
    if not seconds or seconds <= 0:
        return None
    if seconds >= 2:
        return f"{seconds:g} s"
    if seconds >= 1:
        return "1 s"
    reciprocal = round(1 / seconds)
    if reciprocal <= 1:
        return f"{seconds:.2f} s"
    return f"1/{reciprocal} s"


def _format_aperture(value: Any) -> str | None:
    number = _number(value)
    if not number:
        return None
    return f"f/{number:g}"


def _format_focal(value: Any) -> str | None:
    number = _number(value)
    if not number:
        return None
    return f"{number:g} mm"


def _format_iso(value: Any) -> str | None:
    if isinstance(value, (tuple, list)):
        value = value[0] if value else None
    number = _number(value)
    if not number:
        return None
    return f"ISO {int(number)}"


def _format_date(value: Any) -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    for pattern in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M"):
        try:
            parsed = datetime.strptime(text, pattern)
            return parsed.strftime("%Y.%m.%d %H:%M")
        except ValueError:
            continue
    return text


def _camera_name(make: str | None, model: str | None) -> str | None:
    make = _clean_text(make)
    model = _clean_text(model)
    if make and model:
        if model.upper().startswith(make.upper()):
            return model
        return f"{make} {model}"
    return model or make


def _safe_exif(exif: Image.Exif) -> dict[str, Any]:
    exif_ifd: dict[int, Any] = {}
    try:
        exif_ifd = dict(exif.get_ifd(0x8769))
    except Exception:
        exif_ifd = {}

    def read(tag: int) -> Any:
        for source in (exif_ifd, exif):
            try:
                if tag in source and source[tag] not in (None, ""):
                    return source[tag]
            except Exception:
                continue
        return None

    make = read(271)
    model = read(272)
    camera = _camera_name(_clean_text(make), _clean_text(model))
    lens = _clean_text(read(42036)) or _clean_text(read(42035))
    fields = {
        "camera": camera,
        "lens": lens,
        "aperture": _format_aperture(read(33437)),
        "shutter": _format_shutter(read(33434)),
        "iso": _format_iso(read(34855)),
        "focal": _format_focal(read(37386)),
        "takenAt": _format_date(read(36867) or read(306)),
        "artist": _clean_text(read(315)),
        "copyright": _clean_text(read(33432)),
    }
    return {key: value for key, value in fields.items() if value}


def _to_srgb(im: Image.Image) -> Image.Image:
    """Flatten to RGB, honoring an embedded ICC profile when possible."""
    if getattr(im, "is_animated", False):
        try:
            im.seek(0)
        except Exception:
            pass
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        backdrop = Image.new("RGBA", rgba.size, (16, 18, 22, 255))
        return Image.alpha_composite(backdrop, rgba).convert("RGB")

    icc = im.info.get("icc_profile")
    if icc:
        try:
            source_profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            destination = ImageCms.createProfile("sRGB")
            return ImageCms.profileToProfile(
                im,
                source_profile,
                destination,
                outputMode="RGB",
                renderingIntent=0,
            )
        except Exception:
            pass
    if im.mode != "RGB":
        return im.convert("RGB")
    return im


def _display_image(path: Path, max_edge: int | None = None) -> Image.Image:
    with Image.open(path) as opened:
        opened.seek(0)
        image = ImageOps.exif_transpose(opened)
        image.load()
        rgb = _to_srgb(image)
        if max_edge and max(rgb.size) > max_edge:
            rgb.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        return rgb


def _atomic_save_jpeg(image: Image.Image, destination: Path, quality: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=destination.stem + ".", suffix=".tmp", dir=str(destination.parent)
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            image.save(
                handle,
                format="JPEG",
                quality=quality,
                optimize=True,
                progressive=True,
                subsampling=1,
            )
        os.replace(tmp_name, destination)
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


def thumbnail_for(item: Any) -> Path:
    """Return a cached thumbnail path, generating it when needed."""
    stamp = int(float(item.mtime) * 1000)
    destination = THUMB_CACHE / f"{item.id}_{stamp}.jpg"
    if destination.is_file():
        return destination
    try:
        image = _display_image(Path(item.path), max_edge=max(THUMB_SIZE))
        image.thumbnail(THUMB_SIZE, Image.Resampling.LANCZOS)
        _atomic_save_jpeg(image, destination, JPEG_QUALITY_THUMB)
        return destination
    except Exception:
        destination.parent.mkdir(parents=True, exist_ok=True)
        _atomic_save_jpeg(_placeholder_image(), destination, 82)
        return destination


def preview_bytes(item: Any) -> bytes:
    try:
        image = _display_image(Path(item.path), max_edge=PREVIEW_EDGE)
        buffer = io.BytesIO()
        image.save(
            buffer,
            format="JPEG",
            quality=JPEG_QUALITY_PREVIEW,
            optimize=True,
            progressive=True,
            subsampling=1,
        )
        return buffer.getvalue()
    except Exception:
        buffer = io.BytesIO()
        _placeholder_image().save(buffer, format="JPEG", quality=84)
        return buffer.getvalue()


def _placeholder_image() -> Image.Image:
    width, height = 1400, 900
    image = Image.new("RGB", (width, height), (13, 15, 18))
    draw = ImageDraw.Draw(image)
    for radius in (260, 190, 120):
        draw.ellipse(
            (
                width // 2 - radius,
                height // 2 - radius,
                width // 2 + radius,
                height // 2 + radius,
            ),
            outline=(50, 54, 62),
            width=2,
        )
    draw.line(
        (width // 2 - 180, height // 2, width // 2 + 180, height // 2),
        fill=(50, 54, 62),
        width=2,
    )
    draw.line(
        (width // 2, height // 2 - 180, width // 2, height // 2 + 180),
        fill=(50, 54, 62),
        width=2,
    )
    font = None
    for candidate in ("segoeui.ttf", "arial.ttf"):
        try:
            font = ImageFont.truetype(candidate, 30)
            break
        except OSError:
            continue
    draw.text(
        (width // 2, height // 2 + 290),
        "PREVIEW UNAVAILABLE",
        fill=(123, 128, 138),
        font=font,
        anchor="mm",
    )
    return image


def metadata_for(item: Any) -> dict[str, Any]:
    path = Path(item.path)
    stat = item.stat()
    payload: dict[str, Any] = {
        "id": item.id,
        "name": item.name,
        "path": str(path),
        "folder": str(path.parent),
        "format": path.suffix.lstrip(".").upper() or "IMAGE",
        "size": stat.st_size,
        "sizeLabel": format_bytes(stat.st_size),
        "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y.%m.%d %H:%M"),
        "mtime": stat.st_mtime,
        "width": None,
        "height": None,
        "megapixels": None,
        "exif": None,
        "error": None,
    }
    try:
        with Image.open(path) as opened:
            opened.seek(0)
            oriented = ImageOps.exif_transpose(opened)
            width, height = oriented.size
            exif = _safe_exif(opened.getexif())
            payload.update(
                {
                    "width": width,
                    "height": height,
                    "megapixels": round(width * height / 1_000_000, 1),
                    "format": (opened.format or payload["format"]).upper(),
                    "exif": exif or None,
                }
            )
    except Exception as exc:
        payload["error"] = f"无法读取这张图片：{exc}"
    return payload


def image_size_for_sort(item: Any) -> tuple[int, int] | None:
    try:
        with Image.open(item.path) as opened:
            opened.seek(0)
            width, height = opened.size
        return width, height
    except Exception:
        return None


def aspect_label(width: int | None, height: int | None) -> str | None:
    if not width or not height:
        return None
    ratio = width / height
    known = {
        1.5: "3:2",
        4 / 3: "4:3",
        16 / 9: "16:9",
        5 / 4: "5:4",
        1.0: "1:1",
        2 / 3: "2:3",
        3 / 4: "3:4",
        9 / 16: "9:16",
    }
    for value, label in known.items():
        if math.isclose(ratio, value, rel_tol=0.015):
            return label
    return f"{ratio:.2f}:1"
