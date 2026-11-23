#!/usr/bin/env python3
"""Rebuild the three unsigned, script-free Firefox themes shipped with Thermal Atlas."""
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


def halo(stops):
    # Firefox anchors header art to the top right; keep the light in view on narrow windows.
    width, height = 2048, 192
    lights = [(width-620, 160, 310, 115), (width-375, 176, 240, 135), (width-220, 216, 155, 95)]
    xs = [[math.exp(-.5*((x-cx)/rx)**2) for x in range(width)] for cx, _, rx, _ in lights]
    rows = bytearray()
    for y in range(height):
        ys = [.42 * math.exp(-.5*((y-cy)/ry)**2) for _, cy, _, ry in lights]
        rows.append(0)  # PNG scanline filter: none
        for x in range(width):
            pixel = rgb('#131620')
            for stop, horizontal, vertical in zip(stops, xs, ys):
                pixel = mix(pixel, stop, horizontal[x] * vertical)
            rows.extend(pixel)

    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data))

    header = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + chunk(b'IDAT', zlib.compress(rows, 9)) + chunk(b'IEND', b'')


def build(name, palette):
    cold, mid, hot = palette
    surface = '#' + mix(rgb('#101115'), rgb(cold), .13).hex()
    selected = '#' + mix(rgb('#101115'), rgb(mid), .22).hex()
    ink = '#efece5'
    slug = name.lower()
    manifest = {
        'manifest_version': 2, 'name': f'Thermal Atlas · {name}', 'version': '1.0.0',
        'description': f'A still {name} Halo for Firefox, designed for Textfox Nova.',
        'browser_specific_settings': {'gecko': {'id': f'thermal-atlas-{slug}@textfox.local'}},
        'theme': {'images': {'theme_frame': 'halo.png'}, 'colors': {
            'frame': '#131620', 'tab_background_text': ink, 'tab_selected': selected,
            'tab_text': ink, 'tab_line': mid, 'toolbar': surface, 'toolbar_text': ink,
            'toolbar_field': '#15161b', 'toolbar_field_text': ink, 'toolbar_field_border_focus': mid,
            'icons': cold, 'icons_attention': hot, 'sidebar': surface, 'sidebar_text': ink,
            'sidebar_highlight': selected, 'sidebar_highlight_text': ink,
            'popup': surface, 'popup_text': ink, 'ntp_background': surface, 'ntp_text': ink,
        }},
    }
    with ZipFile(OUT / f'{slug}.xpi', 'w') as archive:
        for path, data in [('manifest.json', json.dumps(manifest, indent=2) + '\n'),
                           ('halo.png', halo(list(map(rgb, palette))))]:
            archive.writestr(ZipInfo(path), data, compress_type=ZIP_DEFLATED)


if __name__ == '__main__':
    OUT.mkdir(parents=True, exist_ok=True)
    for name, stops in PALETTES.items():
        build(name, stops)
