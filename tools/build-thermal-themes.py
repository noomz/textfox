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


def contours(stops, base='#131620', width=2048, height=192, centre=(1718, 64), reach=1050, opacity=.65):
    # Still isotherm rings like the preview's contour plate: 1px lines every 16px, tinted by the
    # cold → mid → hot spectrum. The defaults draw the header strip: Firefox anchors header art to
    # the top right, so the rings centre there and stay in view behind the tab strip and the
    # see-through toolbar.
    cx, cy = centre
    spacing, line = 16, 9.5
    base, ramp = rgb(base), list(stops)
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

    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data))

    header = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + chunk(b'IDAT', zlib.compress(rows, 9)) + chunk(b'IEND', b'')


def build(name, palette):
    cold, mid, hot = palette
    surface = '#' + mix(rgb('#101115'), rgb(cold), .13).hex()
    # A Firefox theme cannot paint about:newtab, so each theme also ships a full-page wallpaper on
    # its ntp_background, for Customize → upload or textfox's --tf-newtab-wallpaper.
    (OUT / f'{name.lower()}-wallpaper.png').write_bytes(
        contours(list(map(rgb, palette)), surface, 2560, 1600, centre=(1980, 420), reach=1900, opacity=.4))
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


if __name__ == '__main__':
    OUT.mkdir(parents=True, exist_ok=True)
    for name, stops in PALETTES.items():
        build(name, stops)
