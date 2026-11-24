#!/usr/bin/env python3
"""Measure what a Thermal Atlas preset's motion costs in live Firefox chrome.

Generates the preset with prototypes/nova-thermal/preset.js (or takes --preset),
installs it the way users do (config.css @imports thermal-atlas.css), restarts
Firefox, then reports over --seconds:
  - chrome MozAfterPaint count (main-thread repaints of browser.xhtml)
  - WebRender render reasons from the Gecko profiler (SCENE = new display list)
  - GPU engine busy % for Firefox's processes (Linux DRM fdinfo)
  - every running chrome animation: target, properties, isRunningOnCompositor
    and Firefox's warning when it refuses the compositor
--facts prints how the toolbar surfaces are built instead.

Moving artwork is acceptable when the steady state has ~0 MozAfterPaint and no
per-frame SCENE renders: see the 40% GPU profile behind this probe.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time

from ff_inspect import connect

ROOT = Path(__file__).resolve().parents[2]
SURFACES = ['#nav-bar', '#TabsToolbar', 'box#vertical-tabs']

FACTS_JS = r"""
const out = {};
for (const sel of arguments[0]) {
  const el = document.querySelector(sel);
  if (!el) { out[sel] = null; continue; }
  const cs = getComputedStyle(el);
  const pick = (s, props) => Object.fromEntries(props.map(p => [p, s.getPropertyValue(p)]));
  const r = el.getBoundingClientRect();
  out[sel] = {
    rect: [r.x, r.y, r.width, r.height].map(Math.round),
    style: pick(cs, ['position', 'z-index', 'isolation', 'overflow', 'contain', 'transform', 'background-color']),
    before: pick(getComputedStyle(el, '::before'), ['content', 'position', 'display', 'animation-name']),
    after: pick(getComputedStyle(el, '::after'), ['content', 'position', 'display', 'animation-name']),
  };
}
const urlbar = document.getElementById('urlbar');
out.urlbar = urlbar && {
  popover: urlbar.getAttribute('popover'),
  position: getComputedStyle(urlbar).position,
  zIndex: getComputedStyle(urlbar).zIndex,
  openPopover: (() => { try { return urlbar.matches(':popover-open'); } catch (e) { return String(e); } })(),
};
out.supports = Object.fromEntries(['anchor-name: --a', 'top: anchor(top)', 'position-anchor: --a', 'overflow-clip-margin: 1px'].map(q => [q, CSS.supports(q)]));
return out;
"""

MEASURE_JS = r"""
const [seconds, done] = [arguments[0], arguments[arguments.length - 1]];
let paints = 0;
const onPaint = () => paints++;
window.addEventListener('MozAfterPaint', onPaint);
const threads = ['GeckoMain', 'Compositor', 'Renderer', 'RenderBackend', 'SceneBuilder'];
Services.profiler.StartProfiler(1 << 22, 1, ['nostacksampling'], threads);
setTimeout(async () => {
  window.removeEventListener('MozAfterPaint', onPaint);
  const profile = await Services.profiler.getProfileDataAsync();
  Services.profiler.StopProfiler();
  const names = {}, texts = {};
  const keep = /^(Render reason|NumPictureCacheInvalidated|SetDisplayList|SceneBuilding|Composite)/;
  const walk = p => {
    for (const t of p.threads || []) {
      const strings = t.stringTable || t.stringArray || [];
      const schema = t.markers.schema;
      for (const row of t.markers.data) {
        const name = strings[row[schema.name]] ?? row[schema.name];
        if (!keep.test(name)) continue;
        names[name] = (names[name] || 0) + 1;
        const data = row[schema.data];
        if (data && data.type === 'Text' && data.name != null) {
          const key = name + ': ' + data.name;
          texts[key] = (texts[key] || 0) + 1;
        }
      }
    }
    for (const sub of p.processes || []) walk(sub);
  };
  walk(profile);
  const animations = document.getAnimations({subtree: true}).map(a => {
    const effect = a.effect;
    const target = effect.target;
    let props = [];
    try { props = effect.getProperties().map(p => ({property: p.property, runningOnCompositor: p.runningOnCompositor, warning: p.warning || null})); } catch (e) { props = String(e); }
    return {
      target: target ? (target.id ? '#' + target.id : target.localName) + (effect.pseudoElement || '') : null,
      name: a.animationName || null,
      playState: a.playState,
      isRunningOnCompositor: a.isRunningOnCompositor,
      properties: props,
    };
  });
  done({seconds, mozAfterPaint: paints, markers: names, markerTexts: texts, animations});
}, seconds * 1000);
"""

GENERATE_JS = r"""
const [palette, plate, playback, ui] = arguments;
for (const id of [palette, plate, playback]) document.getElementById(id).checked = true;
document.getElementById('ui').checked = ui;
document.body.offsetHeight;
return window.wrappedJSObject.generatePreset();
"""


def process_tree(root):
    children = {}
    for stat in Path('/proc').glob('[0-9]*/stat'):
        try:
            fields = stat.read_text().rsplit(')', 1)[1].split()
        except OSError:
            continue
        children.setdefault(int(fields[1]), []).append(int(stat.parent.name))
    tree, todo = [], [root]
    while todo:
        pid = todo.pop()
        tree.append(pid)
        todo.extend(children.get(pid, []))
    return tree


def gpu_engine_ns(root):
    """Linux DRM fdinfo: GPU engine busy nanoseconds for Firefox's process tree, per client."""
    clients = {}
    for pid in process_tree(root):
        for info in Path(f'/proc/{pid}/fdinfo').glob('*'):
            try:
                text = info.read_text()
            except OSError:
                continue
            fields = dict(line.split(':', 1) for line in text.splitlines() if ':' in line)
            if 'drm-client-id' not in fields:
                continue
            engines = {k[len('drm-engine-'):]: int(v.split()[0]) for k, v in fields.items()
                       if k.startswith('drm-engine-') and v.strip().endswith('ns')}
            clients[(fields.get('drm-pdev', '').strip(), fields['drm-client-id'].strip())] = engines
    total = {}
    for engines in clients.values():
        for engine, ns in engines.items():
            total[engine] = total.get(engine, 0) + ns
    return total


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def launch(profile, port, vertical, width, height):
    prefs = {'marionette.port': port,
             'toolkit.legacyUserProfileCustomizations.stylesheets': True,
             'browser.shell.checkDefaultBrowser': False,
             'datareporting.policy.dataSubmissionEnabled': False,
             'ui.prefersReducedMotion': 0,
             'sidebar.revamp': vertical, 'sidebar.verticalTabs': vertical,
             'sidebar.visibility': 'always-show'}
    (profile / 'user.js').write_text(''.join(
        f'user_pref({json.dumps(k)}, {json.dumps(v)});\n' for k, v in prefs.items()))
    env = os.environ.copy()
    env['MOZ_REMOTE_ALLOW_SYSTEM_ACCESS'] = '1'
    process = subprocess.Popen(['firefox', '--marionette', '-remote-allow-system-access',
                                '--new-instance', '--profile', str(profile), 'about:blank'],
                               env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    m = connect(port)
    m.set_window_rect(width=width, height=height)
    return process, m


def stop(process, m):
    try:
        if m:
            m.delete_session()
    except Exception:  # noqa: BLE001 - Firefox may already be gone
        pass
    process.terminate()
    try:
        process.wait(15)
    except subprocess.TimeoutExpired:
        process.kill()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--palette', default='aurora', choices=['aurora', 'lichen', 'cinder'])
    parser.add_argument('--plate', default='halo', choices=['contours', 'grain', 'lava', 'halo'])
    parser.add_argument('--playback', default='play', choices=['auto', 'play', 'pause'])
    parser.add_argument('--preset', type=Path, help='use this CSS instead of generating one')
    parser.add_argument('--prototype', type=Path, default=ROOT / 'prototypes/nova-thermal',
                        help='Thermal Atlas page directory that generates the preset')
    parser.add_argument('--save-preset', type=Path, help='also write the generated CSS here')
    parser.add_argument('--horizontal', action='store_true', help='classic tab strip instead of vertical tabs')
    parser.add_argument('--seconds', type=float, default=4)
    parser.add_argument('--settle', type=float, default=3)
    parser.add_argument('--width', type=int, default=1280)
    parser.add_argument('--height', type=int, default=800)
    parser.add_argument('--facts', action='store_true', help='print surface structure and exit')
    args = parser.parse_args()
    vertical = not args.horizontal

    with tempfile.TemporaryDirectory(prefix='tf-thermal-motion-') as directory:
        profile = Path(directory)
        shutil.copytree(ROOT / 'chrome', profile / 'chrome')
        if args.preset and not args.facts:
            css = args.preset.read_text()
        else:
            process, m = launch(profile, free_port(), vertical, args.width, args.height)
            try:
                if args.facts:  # unthemed surfaces: what a preset has to work with
                    m.set_context('chrome')
                    time.sleep(args.settle)
                    print(json.dumps(m.execute_script(FACTS_JS, script_args=[SURFACES], sandbox='system'), indent=1))
                    return
                m.set_context('content')
                m.navigate((args.prototype.resolve() / 'index.html').as_uri())
                css = m.execute_script(GENERATE_JS, script_args=[args.palette, args.plate, args.playback, True])
            finally:
                stop(process, m)
        if args.save_preset:
            args.save_preset.write_text(css)

        (profile / 'chrome' / 'thermal-atlas.css').write_text(css)
        (profile / 'chrome' / 'config.css').write_text('@import url("thermal-atlas.css");\n')
        process, m = launch(profile, free_port(), vertical, args.width, args.height)
        try:
            m.set_context('chrome')
            time.sleep(args.settle)
            m.timeout.script = args.seconds + 60
            gpu_before, wall_before = gpu_engine_ns(process.pid), time.monotonic()
            result = m.execute_async_script(MEASURE_JS, script_args=[args.seconds], sandbox='system')
            gpu_after, wall = gpu_engine_ns(process.pid), time.monotonic() - wall_before
            result['gpuBusyPercent'] = {engine: round(100 * (ns - gpu_before.get(engine, 0)) / (wall * 1e9), 2)
                                        for engine, ns in gpu_after.items()} or 'no DRM fdinfo (not Linux?)'
            result['config'] = {'palette': args.palette, 'plate': args.plate, 'playback': args.playback,
                                'preset': str(args.preset) if args.preset else f'generated from {args.prototype}',
                                'tabs': 'vertical' if vertical else 'horizontal',
                                'version': m.execute_script('return Services.appinfo.version', sandbox='system')}
            print(json.dumps(result, indent=1))
        finally:
            stop(process, m)


if __name__ == '__main__':
    main()
