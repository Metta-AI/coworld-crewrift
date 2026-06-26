"""Port of the Crewrift resource-rect parser (``src/crewrift/resources.nim``).

Reads a CSS-like list of named rectangles: a ``/* name */`` block comment followed
by ``width``/``height``/``left``/``top`` (px) and a ``background``/``border`` color.
A rect is kept only if it has a name, all four bounds, and a color, with positive
width and height (design §6).
"""

from __future__ import annotations

from dataclasses import dataclass


class ResourceError(ValueError):
    """Raised when a resource file cannot be parsed."""


@dataclass
class ResourceRect:
    """A named resource rectangle with integer bounds and an RGBA color."""

    name: str
    x: int
    y: int
    w: int
    h: int
    color: tuple[int, int, int, int]


@dataclass
class _Draft:
    name: str = ""
    x: int | None = None
    y: int | None = None
    w: int | None = None
    h: int | None = None
    color: tuple[int, int, int, int] | None = None


def _trim_value(value: str) -> str:
    """Strip whitespace and one optional trailing semicolon."""

    value = value.strip()
    if value.endswith(";"):
        value = value[:-1].strip()
    return value


def _parse_px(value: str, field_name: str) -> int:
    clean = _trim_value(value)
    if not clean.endswith("px"):
        raise ResourceError(f"Invalid {field_name} resource value: {value}.")
    try:
        return int(clean[:-2].strip())
    except ValueError as exc:
        raise ResourceError(f"Invalid {field_name} resource value: {value}.") from exc


def _parse_hex_color(value: str) -> tuple[int, int, int, int]:
    clean = _trim_value(value)
    if len(clean) != 7 or clean[0] != "#":
        raise ResourceError(f"Invalid resource color: {value}.")
    try:
        r = int(clean[1:3], 16)
        g = int(clean[3:5], 16)
        b = int(clean[5:7], 16)
    except ValueError as exc:
        raise ResourceError(f"Invalid resource color: {value}.") from exc
    return (r, g, b, 255)


def _parse_channel(value: str) -> int:
    try:
        parsed = int(value.strip())
    except ValueError as exc:
        raise ResourceError(f"Invalid resource color channel: {value}.") from exc
    if parsed < 0 or parsed > 255:
        raise ResourceError(f"Invalid resource color channel: {value}.")
    return parsed


def _parse_alpha(value: str) -> int:
    try:
        parsed = float(value.strip())
    except ValueError as exc:
        raise ResourceError(f"Invalid alpha resource color channel: {value}.") from exc
    scaled = int(parsed * 255.0 + 0.5) if parsed <= 1.0 else int(parsed + 0.5)
    return max(0, min(255, scaled))


def _parse_rgba_color(value: str) -> tuple[int, int, int, int]:
    clean = _trim_value(value)
    lower = clean.lower()
    is_rgba = lower.startswith("rgba(") and clean.endswith(")")
    is_rgb = lower.startswith("rgb(") and clean.endswith(")")
    if not is_rgba and not is_rgb:
        raise ResourceError(f"Invalid resource color: {value}.")
    prefix_len = 5 if is_rgba else 4
    parts = clean[prefix_len:-1].split(",")
    if (is_rgba and len(parts) != 4) or (is_rgb and len(parts) != 3):
        raise ResourceError(f"Invalid resource color: {value}.")
    return (
        _parse_channel(parts[0]),
        _parse_channel(parts[1]),
        _parse_channel(parts[2]),
        _parse_alpha(parts[3]) if is_rgba else 255,
    )


def _parse_color(value: str) -> tuple[int, int, int, int]:
    clean = _trim_value(value)
    if clean.startswith("#"):
        return _parse_hex_color(clean)
    if clean.lower().startswith("rgb"):
        return _parse_rgba_color(clean)
    hash_at = clean.find("#")
    if hash_at >= 0 and hash_at + 7 <= len(clean):
        return _parse_hex_color(clean[hash_at : hash_at + 7])
    raise ResourceError(f"Invalid resource color: {value}.")


def _parse_block_name(line: str) -> str:
    text = line.strip()
    if len(text) < 4 or not text.startswith("/*") or not text.endswith("*/"):
        return ""
    return text[2:-2].strip()


def _split_property(line: str) -> tuple[str, str]:
    text = line.strip()
    colon = text.find(":")
    if colon < 0:
        return "", ""
    return text[:colon].strip().lower(), text[colon + 1 :].strip()


def _finalize(draft: _Draft, rects: list[ResourceRect]) -> None:
    if (
        not draft.name
        or draft.x is None
        or draft.y is None
        or draft.w is None
        or draft.h is None
        or draft.color is None
        or draft.w <= 0
        or draft.h <= 0
    ):
        return
    rects.append(
        ResourceRect(name=draft.name, x=draft.x, y=draft.y, w=draft.w, h=draft.h, color=draft.color)
    )


def load_resource_rects(text: str) -> list[ResourceRect]:
    """Parse the complete named rectangle blocks from resource-file text."""

    rects: list[ResourceRect] = []
    draft = _Draft()
    for line_number, line in enumerate(text.splitlines(), start=1):
        try:
            name = _parse_block_name(line)
            if name:
                _finalize(draft, rects)
                draft = _Draft(name=name)
                continue
            key, value = _split_property(line)
            if not key:
                continue
            if key == "width":
                draft.w = _parse_px(value, "width")
            elif key == "height":
                draft.h = _parse_px(value, "height")
            elif key == "left":
                draft.x = _parse_px(value, "left")
            elif key == "top":
                draft.y = _parse_px(value, "top")
            elif key in ("background", "border"):
                draft.color = _parse_color(value)
        except ResourceError as exc:
            raise ResourceError(f"line {line_number}: {exc}") from exc
    _finalize(draft, rects)
    return rects
