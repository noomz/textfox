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

  function paint(selector, shade) {
    const veil = `rgb(16 17 21 / ${shade}%)`;
    const layers = `linear-gradient(${veil}, ${veil}), ${grain}${artwork}`;
    const timing = {grain: '5s linear -1s', lava: '5s ease-in-out -2s', halo: '5s ease-in-out -1.5s'}[plate];
    // The grain texture is a tiled SVG; the color artwork fills the entire surface.
    const sizes = size => `100% 100%, ${grain ? '128px 128px, ' : ''}${size}`;
    const growth = plate === 'halo' ? '118% 127%' : '145% 120%';
    const name = `thermal-atlas-${plate}`;
    return `${selector} {
    background-color: #15161b !important;
    background-image: ${layers} !important;${grain ? '\n    background-blend-mode: normal, soft-light, normal;' : ''}
    background-position: center bottom;
    background-size: ${sizes('100% 100%')};${timing ? `
    animation: ${name} ${timing} infinite alternate${playback === 'pause' ? ' paused' : ''};` : ''}
  }${timing ? `
  @keyframes ${name} {
    from { background-position: ${plate === 'halo' ? 'center' : 'left'} bottom; background-size: ${sizes(plate === 'grain' ? growth : '100% 100%')}; }
    to { background-position: ${plate === 'halo' ? 'center' : 'right'} bottom; background-size: ${sizes(growth)}; }
  }${playback === 'auto' ? `
  @media (prefers-reduced-motion: reduce) {
    ${selector} { animation-play-state: paused !important; }
  }` : ''}` : ''}`;
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
  }${ui ? '\n  ' + paint(uiSurfaces, 72) : ''}
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
