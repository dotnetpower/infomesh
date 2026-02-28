#!/usr/bin/env python3
"""Generate InfoMesh OG / GitHub banner images.

Three variants blending InfoMesh decentralised P2P philosophy
with retro Winamp-era aesthetics.

Output: assets/og-banner-{1,2,3}.png  (1280 x 640)
"""

from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ── constants ──────────────────────────────────────────────
W, H = 1280, 640
OUT_DIR = Path(__file__).parent / "assets"
OUT_DIR.mkdir(exist_ok=True)

MONO_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
SANS_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

random.seed(42)  # reproducible


# ── utility helpers ────────────────────────────────────────

def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def _draw_mesh_nodes(
    draw: ImageDraw.ImageDraw,
    nodes: list[tuple[int, int]],
    node_color: str,
    line_color: str,
    node_r: int = 5,
    max_dist: int = 200,
) -> None:
    """Draw a P2P mesh network graph — nodes + edges within max_dist."""
    for i, (x1, y1) in enumerate(nodes):
        for x2, y2 in nodes[i + 1:]:
            dist = math.hypot(x2 - x1, y2 - y1)
            if dist < max_dist:
                alpha = max(30, int(180 * (1 - dist / max_dist)))
                col = line_color if isinstance(line_color, tuple) else _hex_to_rgba(line_color, alpha)
                draw.line([(x1, y1), (x2, y2)], fill=col, width=1)
    for x, y in nodes:
        draw.ellipse(
            [x - node_r, y - node_r, x + node_r, y + node_r],
            fill=node_color,
        )


def _hex_to_rgba(hex_str: str, alpha: int = 255) -> tuple[int, int, int, int]:
    h = hex_str.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)


def _gradient_v(
    img: Image.Image,
    top: tuple[int, int, int],
    bottom: tuple[int, int, int],
) -> None:
    """Fill image with a vertical linear gradient."""
    draw = ImageDraw.Draw(img)
    for y in range(img.height):
        t = y / max(1, img.height - 1)
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        draw.line([(0, y), (img.width, y)], fill=(r, g, b))


def _winamp_title_bar(
    draw: ImageDraw.ImageDraw,
    x: int, y: int, w: int, h: int,
    text: str,
    bg_left: str = "#4A6984",
    bg_right: str = "#1B3A50",
    text_color: str = "#FFFFFF",
) -> None:
    """Draw a Winamp-style gradient title bar."""
    for i in range(w):
        t = i / max(1, w - 1)
        lr, lg, lb = _hex_to_rgba(bg_left)[:3], _hex_to_rgba(bg_left)[:3], _hex_to_rgba(bg_left)[:3]
        rr, rg, rb = _hex_to_rgba(bg_right)[:3], _hex_to_rgba(bg_right)[:3], _hex_to_rgba(bg_right)[:3]
        left_c = _hex_to_rgba(bg_left)[:3]
        right_c = _hex_to_rgba(bg_right)[:3]
        cr = int(left_c[0] + (right_c[0] - left_c[0]) * t)
        cg = int(left_c[1] + (right_c[1] - left_c[1]) * t)
        cb = int(left_c[2] + (right_c[2] - left_c[2]) * t)
        draw.line([(x + i, y), (x + i, y + h)], fill=(cr, cg, cb))
    font = _font(SANS_BOLD, h - 6)
    draw.text((x + 8, y + 2), text, fill=text_color, font=font)


def _draw_eq_bars(
    draw: ImageDraw.ImageDraw,
    x: int, y: int, w: int, h: int,
    n_bars: int = 28,
    bar_color_low: tuple[int, int, int] = (0, 200, 80),
    bar_color_high: tuple[int, int, int] = (255, 80, 40),
) -> None:
    """Draw Winamp-style equalizer bars."""
    gap = 2
    bar_w = max(2, (w - (n_bars - 1) * gap) // n_bars)
    for i in range(n_bars):
        bx = x + i * (bar_w + gap)
        bar_h = random.randint(int(h * 0.15), h)
        segments = bar_h // 3
        for s in range(segments):
            sy = y + h - s * 3 - 3
            t = s / max(1, segments - 1) if segments > 1 else 0
            cr = int(bar_color_low[0] + (bar_color_high[0] - bar_color_low[0]) * t)
            cg = int(bar_color_low[1] + (bar_color_high[1] - bar_color_low[1]) * t)
            cb = int(bar_color_low[2] + (bar_color_high[2] - bar_color_low[2]) * t)
            draw.rectangle([bx, sy, bx + bar_w - 1, sy + 2], fill=(cr, cg, cb))


def _draw_scanlines(img: Image.Image, alpha: int = 18) -> None:
    """Overlay subtle CRT scanlines."""
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    for y in range(0, img.height, 2):
        d.line([(0, y), (img.width, y)], fill=(0, 0, 0, alpha))
    img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"))


def _draw_rounded_rect(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int, int, int],
    radius: int,
    fill: str | tuple,
    outline: str | tuple | None = None,
    width: int = 1,
) -> None:
    """Draw a rounded rectangle."""
    x1, y1, x2, y2 = xy
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


# ══════════════════════════════════════════════════════════
# VARIANT 1 — "Classic Winamp Skin" style
# Dark metallic frame, green LCD, equalizer, mesh overlay
# ══════════════════════════════════════════════════════════

def generate_v1() -> Image.Image:
    img = Image.new("RGB", (W, H), (28, 28, 32))
    draw = ImageDraw.Draw(img)

    # Background: dark brushed metal gradient
    _gradient_v(img, (35, 38, 45), (18, 20, 25))
    draw = ImageDraw.Draw(img)

    # Outer frame (Winamp-style embossed border)
    draw.rectangle([0, 0, W - 1, H - 1], outline=(80, 90, 100), width=3)
    draw.rectangle([3, 3, W - 4, H - 4], outline=(50, 55, 65), width=2)

    # Title bar gradient (Winamp blue)
    _winamp_title_bar(draw, 8, 8, W - 16, 28,
                      "InfoMesh v0.1 — Decentralized P2P Search for LLMs",
                      bg_left="#2E6496", bg_right="#0D2840")

    # ── LCD display area (green-on-black like Winamp) ──
    lcd_x, lcd_y, lcd_w, lcd_h = 12, 44, W - 24, 180
    draw.rectangle([lcd_x, lcd_y, lcd_x + lcd_w, lcd_y + lcd_h],
                   fill=(5, 12, 5), outline=(40, 80, 40), width=2)

    # Scrolling text in LCD
    lcd_font = _font(MONO_BOLD, 48)
    lcd_font_sm = _font(MONO, 18)
    draw.text((lcd_x + 20, lcd_y + 12), "InfoMesh", fill=(0, 230, 80), font=lcd_font)

    # Subtitle in LCD
    draw.text((lcd_x + 20, lcd_y + 70),
              "Fully Decentralized • LLM-First • P2P",
              fill=(0, 180, 60), font=lcd_font_sm)
    draw.text((lcd_x + 20, lcd_y + 96),
              "No Central Server • Community-Driven • Free via MCP",
              fill=(0, 150, 50), font=lcd_font_sm)
    draw.text((lcd_x + 20, lcd_y + 122),
              "Crawl → Index → Search → Share",
              fill=(0, 140, 45), font=lcd_font_sm)

    # Time display (Winamp-style right side)
    time_font = _font(MONO_BOLD, 40)
    draw.text((lcd_x + lcd_w - 200, lcd_y + 14), "∞:∞∞", fill=(0, 230, 80), font=time_font)

    # kbps / kHz indicators
    tiny = _font(MONO, 12)
    draw.text((lcd_x + lcd_w - 200, lcd_y + 64), "P2P  DHT  MCP", fill=(0, 160, 50), font=tiny)

    # ── Equalizer section ──
    eq_y = lcd_y + lcd_h + 16
    draw.rectangle([12, eq_y, W - 12, eq_y + 120],
                   fill=(10, 10, 14), outline=(50, 55, 65), width=2)
    _draw_eq_bars(draw, 20, eq_y + 8, W - 40, 104,
                  n_bars=42,
                  bar_color_low=(0, 200, 80),
                  bar_color_high=(200, 255, 0))

    # ── Mesh network overlay (bottom section) ──
    mesh_y = eq_y + 136
    mesh_h = H - mesh_y - 12
    draw.rectangle([12, mesh_y, W - 12, mesh_y + mesh_h],
                   fill=(8, 10, 16), outline=(50, 55, 65), width=2)

    nodes = [(random.randint(30, W - 30), random.randint(mesh_y + 15, mesh_y + mesh_h - 15))
             for _ in range(35)]
    _draw_mesh_nodes(draw, nodes, node_color=(0, 200, 120), line_color="#0A5030",
                     node_r=4, max_dist=180)

    # Label
    mesh_label = _font(MONO, 13)
    draw.text((20, mesh_y + 6), "NETWORK MESH — peers connected", fill=(0, 140, 60), font=mesh_label)

    # Winamp buttons (bottom-right) — decorative
    btn_y = H - 36
    for i, label in enumerate(["⏮", "▶", "⏸", "⏹", "⏭"]):
        bx = W - 200 + i * 36
        draw.rounded_rectangle([bx, btn_y, bx + 30, btn_y + 24],
                               radius=3, fill=(50, 55, 65), outline=(80, 90, 100))
        btn_font = _font(SANS, 14)
        draw.text((bx + 6, btn_y + 3), label, fill=(180, 200, 220), font=btn_font)

    _draw_scanlines(img, alpha=12)
    return img


# ══════════════════════════════════════════════════════════
# VARIANT 2 — "Synthwave Winamp" — neon purple/cyan,
# retro-future, mesh nodes glowing
# ══════════════════════════════════════════════════════════

def generate_v2() -> Image.Image:
    img = Image.new("RGB", (W, H), (15, 5, 30))
    _gradient_v(img, (20, 5, 45), (5, 0, 15))
    draw = ImageDraw.Draw(img)

    # Grid floor (synthwave perspective grid)
    vanish_x, vanish_y = W // 2, H // 2 - 40
    # Horizontal lines
    for i in range(15):
        y = vanish_y + int((i / 14) ** 1.8 * (H - vanish_y))
        alpha = min(255, 40 + i * 12)
        draw.line([(0, y), (W, y)], fill=(120, 0, 200, alpha), width=1)
    # Vertical lines (from vanishing point)
    for i in range(25):
        x = int(W * (i / 24))
        draw.line([(vanish_x, vanish_y), (x, H)], fill=(80, 0, 160), width=1)

    # ── Neon glow mesh nodes ──
    glow_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow_layer)
    nodes = [(random.randint(60, W - 60), random.randint(60, H - 160))
             for _ in range(50)]

    # Draw edges with neon glow
    for i, (x1, y1) in enumerate(nodes):
        for x2, y2 in nodes[i + 1:]:
            dist = math.hypot(x2 - x1, y2 - y1)
            if dist < 200:
                alpha = max(20, int(120 * (1 - dist / 200)))
                gd.line([(x1, y1), (x2, y2)], fill=(0, 255, 255, alpha), width=1)

    # Draw nodes as glowing dots
    for x, y in nodes:
        for r in range(12, 0, -3):
            a = int(60 * (1 - r / 12))
            gd.ellipse([x - r, y - r, x + r, y + r], fill=(0, 255, 255, a))
        gd.ellipse([x - 3, y - 3, x + 3, y + 3], fill=(200, 255, 255, 220))

    # Blur the glow layer
    glow_blurred = glow_layer.filter(ImageFilter.GaussianBlur(3))
    img_rgba = img.convert("RGBA")
    img_rgba = Image.alpha_composite(img_rgba, glow_blurred)
    img_rgba = Image.alpha_composite(img_rgba, glow_layer)
    img = img_rgba.convert("RGB")
    draw = ImageDraw.Draw(img)

    # ── Main title ──
    title_font = _font(SANS_BOLD, 72)
    title = "InfoMesh"

    # Neon glow effect for title
    glow_img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    glow_d = ImageDraw.Draw(glow_img)
    # Outer glow
    for offset in [(0, -2), (0, 2), (-2, 0), (2, 0), (-1, -1), (1, 1), (-1, 1), (1, -1)]:
        glow_d.text((W // 2 - 200 + offset[0], H - 200 + offset[1]),
                    title, fill=(255, 0, 200, 80), font=title_font, anchor=None)
    glow_blurred2 = glow_img.filter(ImageFilter.GaussianBlur(6))
    img_rgba = img.convert("RGBA")
    img_rgba = Image.alpha_composite(img_rgba, glow_blurred2)
    img = img_rgba.convert("RGB")
    draw = ImageDraw.Draw(img)

    # Title text
    bbox = draw.textbbox((0, 0), title, font=title_font)
    tw = bbox[2] - bbox[0]
    tx = (W - tw) // 2
    draw.text((tx, H - 200), title, fill=(255, 100, 255), font=title_font)

    # Subtitle
    sub_font = _font(MONO, 20)
    subtitle = "Decentralized P2P Search Engine for LLMs"
    bbox2 = draw.textbbox((0, 0), subtitle, font=sub_font)
    sw = bbox2[2] - bbox2[0]
    draw.text(((W - sw) // 2, H - 120), subtitle, fill=(0, 220, 255), font=sub_font)

    # Tagline
    tag_font = _font(MONO, 16)
    tagline = "No Central Server  •  Free via MCP  •  Community-Owned"
    bbox3 = draw.textbbox((0, 0), tagline, font=tag_font)
    tgw = bbox3[2] - bbox3[0]
    draw.text(((W - tgw) // 2, H - 88), tagline, fill=(180, 100, 255), font=tag_font)

    # ── Winamp-style mini EQ at bottom ──
    eq_x, eq_y = 40, H - 50
    _draw_eq_bars(draw, eq_x, eq_y, 200, 40, n_bars=18,
                  bar_color_low=(0, 200, 255),
                  bar_color_high=(255, 0, 200))

    # Right side: EQ mirror
    _draw_eq_bars(draw, W - 240, eq_y, 200, 40, n_bars=18,
                  bar_color_low=(255, 0, 200),
                  bar_color_high=(0, 200, 255))

    # Winamp-style top bar
    draw.rectangle([0, 0, W, 3], fill=(255, 0, 200))
    draw.rectangle([0, H - 3, W, H], fill=(0, 200, 255))

    _draw_scanlines(img, alpha=10)
    return img


# ══════════════════════════════════════════════════════════
# VARIANT 3 — "Hacker Terminal" — amber/green text,
# Winamp chrome frame, terminal readout + mesh
# ══════════════════════════════════════════════════════════

def generate_v3() -> Image.Image:
    img = Image.new("RGB", (W, H), (10, 10, 10))
    draw = ImageDraw.Draw(img)

    # Very subtle gradient
    _gradient_v(img, (16, 18, 22), (8, 8, 10))
    draw = ImageDraw.Draw(img)

    # ── Chrome Winamp frame ──
    # Outer bevel
    draw.rectangle([0, 0, W - 1, H - 1], outline=(100, 110, 120), width=2)
    draw.rectangle([2, 2, W - 3, H - 3], outline=(60, 65, 72), width=1)
    draw.rectangle([3, 3, W - 4, H - 4], outline=(40, 44, 50), width=1)

    # Title bar with Winamp-style gradient
    _winamp_title_bar(draw, 6, 6, W - 12, 26,
                      "InfoMesh — Search the Decentralized Web",
                      bg_left="#5A3A8A", bg_right="#1A0A3A")

    # Close / Minimize / Shade buttons (decorative)
    for i, col in enumerate([(180, 60, 60), (200, 180, 60), (60, 180, 60)]):
        bx = W - 80 + i * 22
        draw.rounded_rectangle([bx, 10, bx + 16, 26], radius=2, fill=col, outline=(40, 40, 40))

    # ── Left panel: Terminal readout ──
    panel_x, panel_y = 10, 40
    panel_w, panel_h = W // 2 - 15, H - 100
    draw.rectangle([panel_x, panel_y, panel_x + panel_w, panel_y + panel_h],
                   fill=(5, 8, 5), outline=(40, 70, 40), width=2)

    mono = _font(MONO, 15)
    mono_bold = _font(MONO_BOLD, 15)
    mono_lg = _font(MONO_BOLD, 36)
    mono_xl = _font(MONO_BOLD, 22)

    # "InfoMesh" ASCII-esque header
    ty = panel_y + 10
    draw.text((panel_x + 15, ty), "InfoMesh", fill=(0, 255, 100), font=mono_lg)
    ty += 48

    # Status lines (amber/green terminal)
    lines = [
        ("STATUS", "ONLINE", (0, 220, 80)),
        ("PEERS", "1,247 connected", (0, 200, 80)),
        ("INDEX", "2.4M documents", (0, 200, 80)),
        ("DHT", "Kademlia 160-bit", (0, 180, 70)),
        ("PROTO", "libp2p + MCP", (0, 180, 70)),
        ("CRAWL", "12.3 pages/min", (220, 180, 0)),
        ("CACHE", "847 MB / zstd", (220, 180, 0)),
        ("TRUST", "0.92 (Trusted)", (0, 220, 80)),
        ("CRED", "+42.5 earned", (0, 220, 80)),
        ("BAND", "↑ 3.2 ↓ 8.7 Mbps", (180, 180, 180)),
        ("", "", (0, 0, 0)),
        ("MISSION", "", (120, 100, 200)),
    ]

    for label, value, color in lines:
        if label:
            draw.text((panel_x + 15, ty), f"  {label:8s}", fill=(120, 140, 120), font=mono_bold)
            draw.text((panel_x + 140, ty), value, fill=color, font=mono)
        ty += 20

    # Mission text (wrapped)
    mission_lines = [
        "Real-time web access for AI",
        "assistants — free, via MCP.",
        "No per-query billing.",
        "Contribute = Reward.",
    ]
    for ml in mission_lines:
        draw.text((panel_x + 15, ty), f"  > {ml}", fill=(120, 100, 200), font=mono)
        ty += 18

    # Blinking cursor
    draw.text((panel_x + 15, ty + 10), "  > _", fill=(0, 255, 100), font=mono_bold)

    # ── Right panel: mesh visualization ──
    rp_x = W // 2 + 5
    rp_y = 40
    rp_w = W // 2 - 15
    rp_h = H - 100
    draw.rectangle([rp_x, rp_y, rp_x + rp_w, rp_y + rp_h],
                   fill=(8, 5, 18), outline=(60, 40, 100), width=2)

    # Section label
    draw.text((rp_x + 10, rp_y + 6), "NETWORK TOPOLOGY", fill=(140, 100, 220), font=mono_bold)

    # Mesh nodes (purple/cyan themed)
    nodes = [(random.randint(rp_x + 30, rp_x + rp_w - 30),
              random.randint(rp_y + 35, rp_y + rp_h - 60))
             for _ in range(45)]

    # Glow layer for mesh
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for i, (x1, y1) in enumerate(nodes):
        for x2, y2 in nodes[i + 1:]:
            dist = math.hypot(x2 - x1, y2 - y1)
            if dist < 160:
                alpha = max(15, int(100 * (1 - dist / 160)))
                gd.line([(x1, y1), (x2, y2)], fill=(120, 60, 220, alpha), width=1)

    for x, y in nodes:
        for r in range(10, 0, -2):
            a = int(50 * (1 - r / 10))
            gd.ellipse([x - r, y - r, x + r, y + r], fill=(160, 100, 255, a))
        gd.ellipse([x - 3, y - 3, x + 3, y + 3], fill=(200, 180, 255, 200))

    # Mark a few "hub" nodes bigger
    for x, y in nodes[:5]:
        gd.ellipse([x - 6, y - 6, x + 6, y + 6], fill=(255, 150, 255, 180))

    glow_b = glow.filter(ImageFilter.GaussianBlur(2))
    img_rgba = img.convert("RGBA")
    img_rgba = Image.alpha_composite(img_rgba, glow_b)
    img_rgba = Image.alpha_composite(img_rgba, glow)
    img = img_rgba.convert("RGB")
    draw = ImageDraw.Draw(img)

    # ── Bottom EQ bar (spanning full width) ──
    eq_y = H - 50
    draw.rectangle([6, eq_y - 4, W - 6, H - 6],
                   fill=(10, 8, 18), outline=(50, 40, 70), width=1)
    _draw_eq_bars(draw, 14, eq_y, W - 28, 36, n_bars=56,
                  bar_color_low=(80, 40, 180),
                  bar_color_high=(0, 220, 200))

    # Winamp transport buttons (decorative, bottom-left)
    btn_labels = ["⏮", "▶", "⏸", "⏹", "⏭"]
    for i, lbl in enumerate(btn_labels):
        bx = 14 + i * 32
        by = H - 54
        # Don't overlap with eq
        # Place above EQ
        pass

    _draw_scanlines(img, alpha=14)
    return img


# ══════════════════════════════════════════════════════════

def main() -> None:
    generators = [
        ("og-banner-1.png", "Classic Winamp Skin", generate_v1),
        ("og-banner-2.png", "Synthwave Neon", generate_v2),
        ("og-banner-3.png", "Hacker Terminal", generate_v3),
    ]
    for filename, desc, gen_fn in generators:
        print(f"Generating {filename} ({desc})…", flush=True)
        img = gen_fn()
        path = OUT_DIR / filename
        img.save(path, "PNG", optimize=True)
        print(f"  ✓ {path}  ({path.stat().st_size // 1024} KB)")
    print("\nDone! All 3 OG banners saved to assets/")


if __name__ == "__main__":
    main()
