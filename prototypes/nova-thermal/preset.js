// Paint backgrounds, not transformed Firefox controls: tab hitboxes and menus must stay put.
function generatePreset() {
  const selected = name => document.querySelector(`input[name="${name}"]:checked`).id;
  const palette = selected('palette');
  const plate = selected('plate');
  const playback = selected('playback');
  const ui = document.querySelector('#ui').checked;
  const figure = document.querySelector('figure');
  const field = figure.querySelector('article .field');
  const color = name => getComputedStyle(figure).getPropertyValue(`--${name}`).trim();
  const artwork = plate === 'contours'
    ? `repeating-radial-gradient(ellipse at 85% 65%, transparent 0 2px, #15161b 3px 13px), ${getComputedStyle(figure.querySelector('.scale')).backgroundImage}`
    : getComputedStyle(field).backgroundImage;
  const grain = plate === 'grain' ? `${getComputedStyle(field, '::after').backgroundImage}, ` : '';
  const dark = 'color-mix(in srgb, ' + color('cold') + ' 13%, #101115)';
  const uiSurfaces = ':root :is(#nav-bar, #TabsToolbar, box#vertical-tabs)';

  // Motion is a cross-fade: the surface keeps the first frame and ::after fades the last
  // frame in and out on the compositor, 4 updates a second. Animating background-position
  // every frame held the GPU at 40%; each update still redraws the surface, so keep it stepped.
  function paint(selector, shade, anchors) {
    const veil = `rgb(16 17 21 / ${shade}%)`;
    const layers = `linear-gradient(${veil}, ${veil}), ${grain}${artwork}`;
    const blend = grain ? '\n    background-blend-mode: normal, soft-light, normal;' : '';
    const timing = {grain: '5s steps(20) -1s', lava: '5s steps(20) -2s', halo: '5s steps(20) -1.5s'}[plate];
    // The grain texture is a tiled SVG; the color artwork fills the entire surface.
    const sizes = size => `100% 100%, ${grain ? '128px 128px, ' : ''}${size}`;
    const frame = (position, size) => `background-position: ${position} bottom;
    background-size: ${sizes(size)};`;
    if (!timing) {
      return `${selector} {
    background-color: #15161b !important;
    background-image: ${layers} !important;${blend}
    ${frame('center', '100% 100%')}
  }`;
    }
    const growth = plate === 'halo' ? '118% 127%' : '145% 120%';
    const name = `thermal-atlas-${plate}`;
    // Fixed + anchored, so no surface is repositioned: tab titles straddle the border
    // and would be clipped by box#vertical-tabs' overflow if it became a containing block.
    const edges = anchors ? `top: calc(anchor(top) + var(--border-width));
    right: calc(anchor(right) + var(--border-width));
    bottom: calc(anchor(bottom) + var(--border-width));
    left: calc(anchor(left) + var(--border-width));` : 'inset: 0;';
    const names = (anchors || []).map(([surface, anchor]) => `
  :root ${surface} { anchor-name: ${anchor}; }
  :root ${surface}::after { position-anchor: ${anchor}; }`).join('');
    return `${selector} {
    background-color: #15161b !important;
    background-image: ${layers} !important;${blend}
    ${frame(plate === 'halo' ? 'center' : 'left', plate === 'grain' ? growth : '100% 100%')}
    isolation: isolate;
  }${names}
  ${selector}::after {
    content: '';
    position: fixed;
    ${edges}
    z-index: -1;
    pointer-events: none;
    border-radius: inherit;
    background-color: #15161b;
    background-image: ${layers};${blend}
    ${frame(plate === 'halo' ? 'center' : 'right', growth)}
    animation: ${name} ${timing} infinite alternate${playback === 'pause' ? ' paused' : ''};
  }
  @keyframes ${name} {
    from { opacity: 0; }
    to { opacity: 1; }
  }${playback === 'auto' ? `
  @media (prefers-reduced-motion: reduce) {
    ${selector}::after { animation-play-state: paused !important; }
  }` : ''}`;
  }

  return `/* Thermal Atlas for Textfox | ${palette} / ${plate} / ${playback} / UI artwork ${ui ? 'on' : 'off'}
   Save as chrome/thermal-atlas.css. Put @import url("thermal-atlas.css");
   before the rules in chrome/config.css, then restart Firefox. */
@-moz-document url("chrome://browser/content/browser.xhtml") {
 @media (forced-colors: none) {
  :root {
    --tf-theme-colors: 0;
    --tf-bg: ${dark};
    --tf-fg: #efece5;
    --tf-accent: ${color('mid')};
    --tf-border: color-mix(in srgb, ${color('cold')} 40%, #191a20);
    --tf-tab-selected-bg: color-mix(in srgb, ${color('mid')} 18%, #101115);
    --tf-urlbar-bg: color-mix(in srgb, ${color('mid')} 10%, #101115);
  }${ui ? '\n  ' + paint(uiSurfaces, 72, [['#nav-bar', '--thermal-atlas-navbar'],
    ['#TabsToolbar', '--thermal-atlas-tabs'], ['box#vertical-tabs', '--thermal-atlas-vertical-tabs']]) : ''}
 }
}
@-moz-document url("about:home"), url("about:newtab") {
 @media (forced-colors: none) {
  body { color-scheme: dark !important; --newtab-background-color: #15161b !important; --newtab-contextual-text-primary-color: #efece5 !important; }
  ${paint('body', 55)}
 }
}
`;
}

document.querySelector('#download').addEventListener('click', () => {
  const link = document.createElement('a');
  const url = URL.createObjectURL(new Blob([generatePreset()], {type: 'text/css'}));
  link.href = url;
  link.download = 'thermal-atlas.css';
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
});
