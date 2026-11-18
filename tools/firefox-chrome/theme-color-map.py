#!/usr/bin/env python3
"""T35 mapping table (V22): every color key of the pinned themes (ff_themes.py), each
textfox surface's theme pair, its contrast as Firefox would paint it without art, and
whether V22 keeps the pair or falls back to --tf-bg with contrast-color() text.

Reads the cached, sha256-pinned XPIs (ff_themes.xpi downloads a missing one).
Usage: theme-color-map.py [--out FILE]   (markdown on stdout by default)

Model, not paint: painted checks (T35 step 5) decide. Assumptions, from Firefox's
ThemeVariableMap / lwtheme CSS, marked in the table:
- A translucent color composites over what lies under it: toolbar over frame, field over
  toolbar, selected tab over frame. Popups have no known backing, so a translucent one
  counts at its worst over black and over white. Firefox drops the sidebar color's alpha
  (ThemeVariableMap `--sidebar-background-color`).
- `bookmark_text` is Firefox's alias of `toolbar_text` (LightweightThemeManager).
- A text key the theme leaves unset takes Firefox 160's fallback, marked `(inherited)`:
  toolbar_text -> tab_background_text (`--toolbar-text-color: var(--lwt-text-color)`),
  toolbar_field_text -> black (LightweightThemeConsumer fallbackColor), tab_text ->
  toolbar_text. popup_text and sidebar_text have none (no pinned theme leaves them unset
  with a background set).
- A background key left unset leaves the pair unset: V22 fallback.
"""
import argparse
import json
from pathlib import Path
import re
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ff_themes  # noqa: E402

FLOOR = 4.5
NAMES = {
    '{5adf2485-4acd-42a8-b04c-1b0a6b03ddd0}': 'Purple starfield',
    '{cefe0497-fb8e-4407-a806-19acd3ed20fe}': 'Rosy Sky',
    '{2140e74f-2373-4cd1-85dd-984632a6abc7}': 'Topo! Black',
}
# (surface, background key, backing key or None, text key, text fallback chain)
PAIRS = [
    ('window, tab strip', 'frame', None, 'tab_background_text', ()),
    ('navbar', 'toolbar', 'frame', 'toolbar_text', ('tab_background_text',)),
    ('URL bar, search fields', 'toolbar_field', 'toolbar', 'toolbar_field_text', ('=black',)),
    ('selected tab', 'tab_selected', 'frame', 'tab_text', ('toolbar_text', 'tab_background_text')),
    ('popups', 'popup', None, 'popup_text', ()),
    ('sidebar', 'sidebar', None, 'sidebar_text', ()),
]
# Keys textfox maps (V22). Every other key: listed with why textfox leaves it to Firefox.
MAPPED = {k for p in PAIRS for k in (p[1], p[3])} | {'tab_line'}
NOT_MAPPED = {
    'frame_inactive': 'Firefox: window frame when unfocused; textfox paints --tf-bg on frame, keeps Firefox for inactive tint',
    'icons': 'toolbar icon fill; --tf-accent default already reads --toolbarbutton-icon-fill',
    'icons_attention': 'icon attention state; Firefox',
    'tab_loading': 'tab throbber; Firefox',
    'tab_background_separator': 'separators between tabs; textfox draws boxes, no separators',
    'toolbar_top_separator': 'textfox box borders replace separators',
    'toolbar_bottom_separator': 'textfox box borders replace separators',
    'toolbar_vertical_separator': 'textfox box borders replace separators',
    'toolbar_field_border': 'textfox draws --tf-border on the field box',
    'toolbar_field_border_focus': 'focus ring is --tf-accent (B12, V12)',
    'toolbar_field_focus': 'focused field background; candidate pair, see note',
    'toolbar_field_text_focus': 'focused field text; candidate pair, see note',
    'toolbar_field_highlight': 'selected text in the field; Firefox',
    'toolbar_field_highlight_text': 'selected text in the field; Firefox',
    'toolbar_field_separator': 'URL bar separators; Firefox',
    'popup_border': 'textfox draws --tf-border on popups',
    'popup_highlight': 'hovered popup row; Firefox (V12 menu rows)',
    'popup_highlight_text': 'hovered popup row; Firefox',
    'sidebar_border': 'textfox draws --tf-border on the sidebar box',
    'sidebar_highlight': 'selected sidebar row; Firefox',
    'sidebar_highlight_text': 'selected sidebar row; Firefox',
    'button_background_hover': 'toolbar button hover; Firefox',
    'button_background_active': 'toolbar button active; Firefox',
    'ntp_background': 'new tab page; content, not chrome (newtab.css keeps Firefox ntp_*)',
    'ntp_text': 'new tab page; content, not chrome',
}


LITERAL = {'=black': (0, 0, 0, 1.0)}


def rgba(value):
    """Manifest color (list or CSS rgb()/rgba() string) -> (r, g, b, a)."""
    if isinstance(value, (list, tuple)):
        return (*value[:3], value[3] if len(value) > 3 else 1.0)
    nums = [float(n) for n in re.findall(r'[\d.]+', value)]
    return (*nums[:3], nums[3] if len(nums) > 3 else 1.0)


def over(top, under):
    a = top[3]
    return (*(top[i] * a + under[i] * (1 - a) for i in range(3)), 1.0)


def luminance(c):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2])


def ratio(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def contrast_color(bg):
    """CSS contrast-color(): white or black, whichever contrasts more (black on a tie)."""
    white, black = (255, 255, 255, 1.0), (0, 0, 0, 1.0)
    return white if ratio(white, bg) > ratio(black, bg) else black


def css(c):
    rgb = ','.join(str(round(v)) for v in c[:3])
    return f'({rgb})' if c[3] == 1 else f'({rgb},{c[3]:g})'


def normalize(colors):
    """Firefox's own key handling: bookmark_text is toolbar_text, sidebar loses its alpha."""
    colors = dict(colors)
    if 'bookmark_text' in colors:
        colors.setdefault('toolbar_text', colors.pop('bookmark_text'))
    if 'sidebar' in colors:
        colors['sidebar'] = (*rgba(colors['sidebar'])[:3], 1.0)
    return colors


def manifests():
    """(label, colors) per pinned theme variant, Nova first."""
    out = []
    for theme in [*ff_themes.NOVA, *ff_themes.IMAGES]:
        m = json.loads(zipfile.ZipFile(ff_themes.xpi(theme)).read('manifest.json'))
        name = NAMES.get(theme, theme.split('@')[0])
        if 'dark_theme' in m:
            out += [(f'{name} light', m['theme']['colors']), (f'{name} dark', m['dark_theme']['colors'])]
        else:
            out.append((name, m['theme']['colors']))
    return [(label, normalize(colors)) for label, colors in out]


def paint(colors, key, backing):
    """Opaque color Firefox paints for key: composited over its backing chain, or None."""
    if key not in colors:
        return None
    c = rgba(colors[key])
    if c[3] < 1 and backing:
        under = paint(colors, backing, dict(p[1:3] for p in PAIRS).get(backing))
        c = over(c, under) if under else c
    return c


def row(colors, surface, bg_key, backing, text_key, chain):
    frame = paint(colors, 'frame', None)
    fallback_bg = frame or (0, 0, 0, 1.0)  # --tf-bg = --lwt-accent-color = frame
    fb = contrast_color(fallback_bg)
    fallback = f'--tf-bg {css(fallback_bg)} + {css(fb)} = {ratio(fb, fallback_bg):.1f}'
    if bg_key not in colors:
        return [surface, 'unset', '', '', 'FALLBACK (unset)', fallback]
    bg = paint(colors, bg_key, backing)
    note = ''
    if rgba(colors[bg_key])[3] < 1 and not backing:
        note = ' worst of over black/white'
    elif rgba(colors[bg_key])[3] < 1:
        note = f' over {backing}'
    text_src = next((k for k in (text_key, *chain) if k in colors or k in LITERAL), None)
    if not text_src:
        return [surface, css(rgba(colors[bg_key])) + note, 'unset', '', 'FALLBACK (no text)', fallback]
    text = LITERAL.get(text_src) or rgba(colors[text_src])
    if bg[3] < 1 or (rgba(colors[bg_key])[3] < 1 and not backing):
        raw = rgba(colors[bg_key])
        cr = min(ratio(over(text, over(raw, u)) if text[3] < 1 else text, over(raw, u))
                 for u in ((0, 0, 0, 1.0), (255, 255, 255, 1.0)))
    else:
        cr = ratio(over(text, bg) if text[3] < 1 else text, bg)
    shown_bg = css(rgba(colors[bg_key])) + (f' → {css(bg)}' if rgba(colors[bg_key])[3] < 1 and backing else '') + note
    shown_text = css(text) + ('' if text_src == text_key else f' (inherited {text_src.lstrip("=")})')
    verdict = 'keep' if cr >= FLOOR else f'FALLBACK (< {FLOOR})'
    return [surface, shown_bg, shown_text, f'{cr:.2f}', verdict, fallback if verdict != 'keep' else '']


def accent_row(colors):
    frame = paint(colors, 'frame', None)
    if 'tab_line' not in colors:
        return ['accent', 'unset', '', '', 'FALLBACK (unset)', '--tf-accent default (--toolbarbutton-icon-fill)']
    line = rgba(colors['tab_line'])
    cr = ratio(line, frame) if frame else 0
    return ['accent', css(line), 'vs frame (non-text, 3:1)', f'{cr:.2f}',
            'keep' if cr >= 3 else 'FALLBACK (< 3)', '' if cr >= 3 else '--tf-accent default (--toolbarbutton-icon-fill)']


def set_by(key, variants):
    """Who sets a key, with whole groups (every Nova light / dark variant) collapsed."""
    users = [label for label, c in variants if key in c]
    if len(users) == len(variants):
        return 'all'
    out = []
    for scheme in ('light', 'dark'):
        group = [label for label, _ in variants if label.startswith('nova-') and label.endswith(scheme)]
        if group and set(group) <= set(users):
            out.append(f'every Nova {scheme}')
            users = [u for u in users if u not in group]
    return ', '.join(out + users)


def table(variants):
    lines = ['## Pairs per theme (no art)', '',
             'Columns: background as the manifest sets it (→ composited), text, contrast, V22 verdict, fallback paint.', '']
    for label, colors in variants:
        lines += [f'### {label}', '', '| surface | background | text | contrast | verdict | fallback |',
                  '|---|---|---|---|---|---|']
        for p in PAIRS:
            lines.append('| ' + ' | '.join(row(colors, *p)) + ' |')
        lines.append('| ' + ' | '.join(accent_row(colors)) + ' |')
        lines.append('')
    keys = sorted({k for _, c in variants for k in c})
    lines += ['## Every color key', '', '| key | set by | textfox use |', '|---|---|---|']
    for k in keys:
        who = set_by(k, variants)
        use = 'mapped (V22)' if k in MAPPED else NOT_MAPPED.get(k, 'UNCLASSIFIED')
        lines.append(f'| `{k}` | {who} | {use} |')
    lines += ['', '## Summary', '', '| theme | keep | fallback |', '|---|---|---|']
    for label, colors in variants:
        rows = [row(colors, *p) for p in PAIRS] + [accent_row(colors)]
        keep = [r[0] for r in rows if r[4] == 'keep']
        fall = [f'{r[0]} ({r[4][10:-1]})' for r in rows if r[4] != 'keep']
        lines.append(f'| {label} | {", ".join(keep) or "-"} | {", ".join(fall) or "-"} |')
    return '\n'.join(lines) + '\n'


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--out', type=Path)
    args = ap.parse_args()
    text = table(manifests())
    if 'UNCLASSIFIED' in text:
        print('unclassified color key: add it to MAPPED or NOT_MAPPED', file=sys.stderr)
        return 1
    if args.out:
        args.out.write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
