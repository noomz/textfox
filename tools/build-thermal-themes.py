#!/usr/bin/env python3
"""Rebuild the three unsigned, script-free Firefox themes shipped with Thermal Atlas (still contours)."""
import json
import math
from pathlib import Path
import struct
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED
import zlib

OUT = Path(__file__).resolve().parents[1] / 'prototypes/nova-thermal/themes'
PALETTES = {
    'Aurora': ('#7ddbed', '#b398fa', '#ff9568'),
    'Lichen': ('#77cac2', '#b4d68b', '#f9ac59'),
    'Cinder': ('#a2b3f5', '#e789a4', '#ffd38b'),
}


def rgb(color):
    return bytes.fromhex(color[1:])


def mix(base, tint, strength):
    return bytes(round(a + (b - a) * strength) for a, b in zip(base, tint))


def contours(stops):
    # Still isotherm rings like the preview's contour plate: 1px lines every 16px, tinted by the
    # cold → mid → hot spectrum. Firefox anchors header art to the top right, so the rings centre
    # there and stay in view behind the tab strip and the see-through toolbar.
    width, height = 2048, 192
    cx, cy, reach = width - 330, 64, 1050
    spacing, line, opacity = 16, 9.5, .65
    base, ramp = rgb('#131620'), list(stops)
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # PNG scanline filter: none
        for x in range(width):
            r = math.hypot(x - cx, (y - cy) * 1.15)
            # Antialiased ring: full at the line centre, gone 1.25px either side.
            ring = max(0., 1 - max(0., abs(r % spacing - line) - .5) / .75)
            fade = min(1., max(0., (reach - r) / 260))
            t = min(1., max(0., (x - cx + .16 * (y - cy)) / (2 * reach) + .58))
            lo, hi, k = (ramp[0], ramp[1], t * 2) if t < .5 else (ramp[1], ramp[2], t * 2 - 1)
            rows.extend(mix(base, mix(lo, hi, k), ring * fade * opacity))

    return png(width, height, rows)


def png(width, height, rows):
    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data))

    header = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + chunk(b'IDAT', zlib.compress(rows, 9)) + chunk(b'IEND', b'')


# The preview's still contour plate (index.html, article::after): the spectrum at 65% on #15161b, seen
# through 2px rings every 16 CSS px centred at 88% 42%, cut off along the rotated box's left edge.
PLATE, PLATE_OPACITY, RING_CENTRE, CUT_ANGLE, CUT_AT = '#15161b', .65, (.88, .42), 66, .47
# The spectrum spans the rotated box, not the page: cold at the cut, mid at the ring centre, hot off-screen.
SPECTRUM_AT = (.42, .88, 1.3)


def plate_stops(palette):
    return ['#' + mix(rgb(PLATE), rgb(c), PLATE_OPACITY).hex() for c in palette]


def newtab_css(palette):
    cx, cy = (f'{round(v * 100)}%' for v in RING_CENTRE)
    cold, mid, hot = plate_stops(palette)
    return (f'linear-gradient({CUT_ANGLE}deg, {PLATE} {CUT_AT:.0%}, transparent {CUT_AT:.0%}), '
            f'repeating-radial-gradient(circle at {cx} {cy}, {PLATE} 0 8px, transparent 9px 10px, {PLATE} 11px 16px), '
            f'linear-gradient(105deg, {cold} {SPECTRUM_AT[0]:.0%}, {mid} {SPECTRUM_AT[1]:.0%}, {hot} {SPECTRUM_AT[2]:.0%})')


def gradient_position(x, y, width, height, degrees):
    # Where (x, y) falls along a CSS linear-gradient line, 0 at the start and 1 at the end.
    a = math.radians(degrees)
    dx, dy = math.sin(a), -math.cos(a)
    length = abs(width * dx) + abs(height * dy)
    return ((x - width / 2) * dx + (y - height / 2) * dy) / length + .5


def newtab_png(palette, width=2880, height=1800, scale=2):
    # The same plate rasterised for a 1440x900 window at 2x; Firefox scales uploads to cover.
    plate, ramp = rgb(PLATE), list(map(rgb, plate_stops(palette)))
    cx, cy = RING_CENTRE[0] * width, RING_CENTRE[1] * height
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            m = math.hypot(x - cx, y - cy) / scale % 16
            ring = min(1., max(0., m - 8, 11 - m)) if 8 < m < 11 else 0.
            ring = 1. if 9 <= m <= 10 else ring
            if gradient_position(x, y, width, height, CUT_ANGLE) < CUT_AT:
                ring = 0.
            t = gradient_position(x, y, width, height, 105)
            a, b, c = SPECTRUM_AT
            lo, hi, k = ((ramp[0], ramp[1], (t - a) / (b - a)) if t < b else (ramp[1], ramp[2], (t - b) / (c - b)))
            k = min(1., max(0., k))
            rows.extend(mix(plate, mix(lo, hi, k), ring))
    return png(width, height, rows)


def build(name, palette):
    cold, mid, hot = palette
    surface = '#' + mix(rgb('#101115'), rgb(cold), .13).hex()
    selected = '#' + mix(rgb('#101115'), rgb(mid), .22).hex()
    ink = '#efece5'
    # Let the contour art show behind the address-bar row, as the preview's 'Apply to browser UI too' does.
    see_through = 'rgba({}, {}, {}, .62)'.format(*bytes.fromhex(surface[1:]))
    slug = name.lower()
    manifest = {
        'manifest_version': 2, 'name': f'Thermal Atlas · {name}', 'version': '1.0.1',
        'description': f'Still {name} thermal contours for Firefox, designed for Textfox Nova.',
        'browser_specific_settings': {'gecko': {'id': f'thermal-atlas-{slug}@textfox.local'}},
        'theme': {'images': {'theme_frame': 'contours.png'}, 'colors': {
            'frame': '#131620', 'tab_background_text': ink, 'tab_selected': selected,
            'tab_text': ink, 'tab_line': mid, 'toolbar': see_through, 'toolbar_text': ink,
            'toolbar_field': '#15161b', 'toolbar_field_text': ink, 'toolbar_field_border_focus': mid,
            'icons': cold, 'icons_attention': hot, 'sidebar': surface, 'sidebar_text': ink,
            'sidebar_highlight': selected, 'sidebar_highlight_text': ink,
            'popup': surface, 'popup_text': ink, 'ntp_background': surface, 'ntp_text': ink,
        }},
    }
    with ZipFile(OUT / f'{slug}.xpi', 'w') as archive:
        for path, data in [('manifest.json', json.dumps(manifest, indent=2) + '\n'),
                           ('contours.png', contours(list(map(rgb, palette))))]:
            archive.writestr(ZipInfo(path), data, compress_type=ZIP_DEFLATED)
    # A Firefox theme cannot paint about:newtab, so each theme also ships the preview's contour plate
    # for it: a CSS snippet for textfox's --tf-newtab-wallpaper, and a PNG to upload in Customize.
    (OUT / f'{slug}-newtab.css').write_text(
        f':root {{\n  --tf-newtab-wallpaper: {newtab_css(palette)};\n}}\n')
    (OUT / f'{slug}-wallpaper.png').write_bytes(newtab_png(palette))


if __name__ == '__main__':
    OUT.mkdir(parents=True, exist_ok=True)
    for name, stops in PALETTES.items():
        build(name, stops)
