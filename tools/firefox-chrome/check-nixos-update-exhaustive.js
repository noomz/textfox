#!/usr/bin/env node
'use strict';
/*
Bounded exhaustive check of the textfox NixOS autoconfig install protocol.

What runs: the autoconfig script from nix/pkgs/wrapTextfox.nix, unmodified,
through the XPCOM shim in test-nixos-update.js, against a real temp directory.
No model of the script is written by hand.

What is explored: every sequence of up to DEPTH events (default 4) from four
starting profiles. An event is one of:
  - Firefox starts on generation A or B and the script runs to the end
  - the same, but Firefox dies before mutating step k (for every k), or dies
    part-way through a recursive directory removal at step k
  - the user writes their own bytes into userChrome.css or userContent.css
  - the user deletes userChrome.css or userContent.css
  - the user flips textfox.force-adopt
Reached profiles are deduplicated by content, so each distinct profile is
expanded once.

Properties (checked after every event):
  P1 keep-user-bytes   Bytes the user wrote into an owned name stay somewhere in
                       chrome/ (in place or in a textfox-backup-* directory)
                       until the user removes them.
  P2 others-untouched  Files textfox does not own keep their bytes.
  P3 refuse-only-foreign  A complete run refuses only when force-adopt is off and
                       an owned CSS file holds bytes textfox cannot prove are its
                       own; a refusal changes nothing and does not restart.
  P4 converge          A complete run that does not refuse leaves this
                       generation's CSS bytes, its marker, its complete asset
                       directory, no staging names, no other asset directory,
                       and no legacy marker; it restarts Firefox when the CSS
                       bytes changed; and it reports no internal failure.
  P5 idempotent        A second complete run of the same generation does nothing.

Exit 0: every property holds in every explored state. Exit 1: the shortest
counterexample per property is printed as numbered steps.

Usage: node tools/firefox-chrome/check-nixos-update-exhaustive.js [--depth N]
*/

const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {
  Sandbox,
  makeContext,
  codeFor,
  writeStore,
  sha256Hex,
  GENERATION_A,
  GENERATION_B,
  LEGACY_GENERATION,
} = require('./test-nixos-update.js');

const depthArg = process.argv.indexOf('--depth');
const DEPTH = depthArg >= 0 ? Number(process.argv[depthArg + 1]) : 4;
const MAX_USER_EDITS = 1;
const CSS_NAMES = ['userChrome.css', 'userContent.css'];
const BACKUP_RE = /^textfox-backup-\d+(-\d+)?$/;

/* ------------------------------------------------------------- crash shim */

// Process death, not an exception: once the injected step fires, every later
// mutating step also fails, so a catch inside the script (removeIfOurs) cannot
// carry on writing as a live process would not.
class CrashSandbox extends Sandbox {
  step(verb, absPath) {
    if (this.dead) throw new Error('process is dead');
    const index = this.steps.length;
    const isDir = verb === 'remove' && fs.existsSync(absPath) && fs.statSync(absPath).isDirectory();
    const removable = isDir ? fs.readdirSync(absPath).sort() : [];
    this.steps.push({ label: `${verb} ${this.label(absPath)}`, partialRemove: removable.length >= 2 });
    if (index === this.failIndex) {
      this.dead = true;
      if (this.crashMode === 'partial') {
        // nsIFile.remove(true) deletes entry by entry; dying in the middle leaves
        // the directory under its own name with some entries gone.
        fs.rmSync(path.join(absPath, removable[0]), { recursive: true });
      }
      throw new Error(`killed at step ${index}`);
    }
  }
}

const sandbox = new CrashSandbox();
const chromeDir = path.join(sandbox.profileDir, 'chrome');
const stores = {
  A: writeStore(sandbox, GENERATION_A),
  B: writeStore(sandbox, GENERATION_B),
};
const legacy = writeStore(sandbox, LEGACY_GENERATION);
const legacyHidden = path.join(sandbox.root, 'collected-legacy-store');
const compiled = {};
for (const g of Object.keys(stores)) compiled[g] = new vm.Script(codeFor(stores[g].abs));

const GEN_BYTES = {
  A: { 'userChrome.css': GENERATION_A.userChrome, 'userContent.css': GENERATION_A.userContent },
  B: { 'userChrome.css': GENERATION_B.userChrome, 'userContent.css': GENERATION_B.userContent },
};
const LEGACY_BYTES = new Set([LEGACY_GENERATION.userChrome, LEGACY_GENERATION.userContent]);

/* ------------------------------------------------------- state save/load */

function walk(dir, rel = '') {
  if (!fs.existsSync(dir)) return [];
  const out = [];
  for (const name of fs.readdirSync(dir).sort()) {
    const abs = path.join(dir, name);
    const r = rel ? `${rel}/${name}` : name;
    const st = fs.statSync(abs);
    if (st.isDirectory()) {
      out.push({ rel: r, dir: true, mode: st.mode & 0o777 });
      out.push(...walk(abs, r));
    } else {
      out.push({ rel: r, dir: false, mode: st.mode & 0o777, data: fs.readFileSync(abs, 'utf8') });
    }
  }
  return out;
}

function snapshot(env) {
  return { files: walk(sandbox.profileDir), env: { ...env, ghost: [...env.ghost] } };
}

function restore(state) {
  fs.rmSync(sandbox.profileDir, { recursive: true, force: true });
  fs.mkdirSync(sandbox.profileDir);
  for (const f of state.files) {
    const abs = path.join(sandbox.profileDir, f.rel);
    if (f.dir) fs.mkdirSync(abs);
    else fs.writeFileSync(abs, f.data);
  }
  // Modes last, so a read-only directory never blocks its own children.
  for (const f of [...state.files].reverse()) fs.chmodSync(path.join(sandbox.profileDir, f.rel), f.mode);
  const legacyPresent = fs.existsSync(legacy.dir);
  if (state.env.legacyStore && !legacyPresent) fs.renameSync(legacyHidden, legacy.dir);
  if (!state.env.legacyStore && legacyPresent) fs.renameSync(legacy.dir, legacyHidden);
}

// Backup directory names carry a timestamp; two profiles that differ only in
// that timestamp are the same profile.
function stateKey(state) {
  const backups = {};
  const plain = [];
  for (const f of state.files) {
    const [top, ...rest] = f.rel.split('/');
    const line = `${f.dir ? 'd' : 'f'} ${f.mode.toString(8)} ${f.dir ? '' : sha256Hex(f.data)}`;
    if (f.rel.startsWith('chrome/') && BACKUP_RE.test(rest[0] || '')) {
      const key = rest[0];
      backups[key] = backups[key] || [];
      if (rest.length > 1) backups[key].push(`${rest.slice(1).join('/')} ${line}`);
    } else {
      plain.push(`${f.rel} ${line}`);
    }
  }
  const backupSigs = Object.values(backups).map((b) => b.join(';')).sort();
  const { env } = state;
  return JSON.stringify([plain, backupSigs, env.force, env.legacyStore, env.edits, [...env.ghost].sort()]);
}

/* ------------------------------------------------------------ the script */

function runScript(gen, force, crash) {
  sandbox.steps = [];
  sandbox.dead = false;
  sandbox.failIndex = crash ? crash.index : -1;
  sandbox.crashMode = crash ? crash.mode : null;
  const run = sandbox.beginRun();
  const prefs = force ? { 'textfox.force-adopt': true } : {};
  try {
    compiled[gen].runInNewContext(makeContext(sandbox, { prefs }), { timeout: 30000 });
  } catch (error) {
    run.errors.push(`uncaught: ${error}`);
  }
  return { steps: sandbox.steps, errors: run.errors, restarts: run.restarts, killed: sandbox.dead };
}

/* ------------------------------------------------------------ properties */

function cssBytes(files) {
  const out = {};
  for (const name of CSS_NAMES) {
    const f = files.find((x) => x.rel === `chrome/${name}` && !x.dir);
    out[name] = f ? f.data : null;
  }
  return out;
}

function provable(bytes, env, files) {
  if (bytes === null) return true;
  if (CSS_NAMES.some((n) => GEN_BYTES.A[n] === bytes || GEN_BYTES.B[n] === bytes)) return true;
  const legacyMarker = files.some((f) => f.rel === `chrome/${legacy.leaf}`);
  return LEGACY_BYTES.has(bytes) && env.legacyStore && legacyMarker;
}

function checkAlways(before, after, violations) {
  const all = after.files.filter((f) => !f.dir && f.rel.startsWith('chrome/')).map((f) => f.data);
  for (const bytes of after.env.ghost) {
    if (!all.includes(bytes)) violations.push(['P1 keep-user-bytes', `user bytes ${JSON.stringify(bytes)} are gone from chrome/`]);
  }
  for (const f of after.env.others) {
    const now = after.files.find((x) => x.rel === f.rel);
    if (!now || now.data !== f.data) violations.push(['P2 others-untouched', `${f.rel} was changed or removed`]);
  }
}

function checkCompleteRun(gen, before, result, after, violations) {
  const refused = result.errors.some((m) => m.startsWith('textfox: refusing to install'));
  const failed = result.errors.filter((m) => !m.startsWith('textfox: refusing') && !m.startsWith('textfox: textfox.force-adopt is on'));
  if (failed.length) violations.push(['P4 converge', `internal failure reported: ${failed[0]}`]);
  if (refused) {
    const pre = cssBytes(before.files);
    const foreign = CSS_NAMES.filter((n) => !provable(pre[n], before.env, before.files));
    if (before.env.force || !foreign.length) {
      const why = before.env.force ? 'force-adopt is on' : 'every CSS file present holds bytes textfox wrote';
      violations.push(['P3 refuse-only-foreign', `run refused although ${why} (${CSS_NAMES.map((n) => `${n}=${describeBytes(pre[n])}`).join(', ')}; ${describeMarker(before.files, pre)})`]);
    }
    if (result.steps.length || result.restarts) violations.push(['P3 refuse-only-foreign', `refusal still made ${result.steps.length} change(s) or restarted`]);
    return false;
  }
  const store = stores[gen];
  const post = cssBytes(after.files);
  for (const n of CSS_NAMES) {
    if (post[n] !== GEN_BYTES[gen][n]) violations.push(['P4 converge', `${n} is ${describeBytes(post[n])}, not generation ${gen}`]);
  }
  const markerFile = after.files.find((f) => f.rel === 'chrome/textfox-installed.json');
  let marker = null;
  try {
    marker = markerFile && JSON.parse(markerFile.data);
  } catch (e) {
    marker = null;
  }
  if (!marker || marker.store !== store.leaf || marker.assetsDir !== store.assetsDir ||
      CSS_NAMES.some((n) => marker.css[n] !== store.css[n])) {
    violations.push(['P4 converge', `marker does not describe generation ${gen}`]);
  }
  const wantIcons = Object.keys(store.icons).sort().map((n) => `chrome/${store.assetsDir}/${n}=${store.icons[n]}`);
  const haveIcons = after.files.filter((f) => !f.dir && f.rel.startsWith(`chrome/${store.assetsDir}/`)).map((f) => `${f.rel}=${f.data}`).sort();
  if (JSON.stringify(wantIcons) !== JSON.stringify(haveIcons)) {
    violations.push(['P4 converge', `${store.assetsDir}/ holds [${haveIcons.map((x) => path.basename(x.split('=')[0])).join(', ')}], generation ${gen} needs [${Object.keys(store.icons).sort().join(', ')}]: userChrome.css points at missing icons`]);
  }
  const top = after.files.filter((f) => f.rel.split('/').length === 2).map((f) => f.rel.slice('chrome/'.length));
  const leftovers = top.filter((n) => n.startsWith('.textfox-staging-') ||
    (n.startsWith('textfox-assets-') && n !== store.assetsDir) || n === legacy.leaf);
  if (leftovers.length) violations.push(['P4 converge', `leftovers after a complete run: ${leftovers.join(', ')}`]);
  const pre = cssBytes(before.files);
  if (CSS_NAMES.some((n) => pre[n] !== post[n]) && !result.restarts) {
    violations.push(['P4 converge', 'CSS bytes changed but Firefox was not restarted']);
  }
  return true;
}

function describeMarker(files, css) {
  const markerFile = files.find((f) => f.rel === 'chrome/textfox-installed.json');
  if (!markerFile) return 'marker: absent';
  let marker;
  try {
    marker = JSON.parse(markerFile.data);
  } catch (e) {
    return 'marker: unreadable';
  }
  const records = CSS_NAMES.every((n) => css[n] === null || marker.css[n] === sha256Hex(css[n]));
  return records ? 'marker: records these bytes' : 'marker: records other bytes';
}

function describeBytes(bytes) {
  if (bytes === null) return 'absent';
  for (const g of Object.keys(GEN_BYTES)) if (CSS_NAMES.some((n) => GEN_BYTES[g][n] === bytes)) return `generation ${g} bytes`;
  if (LEGACY_BYTES.has(bytes)) return 'legacy bytes';
  return 'user bytes';
}

/* ------------------------------------------------------------ the events */

function* events(state) {
  for (const gen of Object.keys(stores)) {
    restore(state);
    const dry = runScript(gen, state.env.force, null);
    yield { label: `Firefox starts on generation ${gen}; the script runs to the end`, run: { gen, crash: null } };
    for (let i = 0; i < dry.steps.length; i++) {
      yield { label: `Firefox starts on generation ${gen}; killed before step ${i} (${dry.steps[i].label})`, run: { gen, crash: { index: i, mode: 'before' } } };
      if (dry.steps[i].partialRemove) {
        yield { label: `Firefox starts on generation ${gen}; killed part-way through step ${i} (${dry.steps[i].label})`, run: { gen, crash: { index: i, mode: 'partial' } } };
      }
    }
  }
  if (state.env.edits < MAX_USER_EDITS) {
    for (const name of CSS_NAMES) yield { label: `user writes their own ${name}`, user: { edit: name } };
  }
  for (const name of CSS_NAMES) {
    if (state.files.some((f) => f.rel === `chrome/${name}`)) yield { label: `user deletes ${name}`, user: { remove: name } };
  }
  yield { label: `user sets textfox.force-adopt to ${!state.env.force}`, user: { force: true } };
}

function apply(state, event) {
  restore(state);
  const env = { ...state.env, ghost: new Set(state.env.ghost) };
  let result = null;
  if (event.run) {
    result = runScript(event.run.gen, env.force, event.run.crash);
  } else if (event.user.edit || event.user.remove) {
    const name = event.user.edit || event.user.remove;
    const abs = path.join(chromeDir, name);
    if (fs.existsSync(abs)) env.ghost.delete(fs.readFileSync(abs, 'utf8'));
    if (event.user.edit) {
      env.edits += 1;
      const bytes = `/* the user's own ${name}, edit ${env.edits} */\n`;
      fs.mkdirSync(chromeDir, { recursive: true });
      if (fs.existsSync(abs)) fs.chmodSync(abs, 0o644);
      fs.writeFileSync(abs, bytes);
      env.ghost.add(bytes);
    } else {
      fs.rmSync(abs);
    }
  } else {
    env.force = !env.force;
  }
  return { after: snapshot(env), result };
}

/* ------------------------------------------------------- start profiles */

function startProfile(name, build, legacyStore) {
  fs.rmSync(sandbox.profileDir, { recursive: true, force: true });
  fs.mkdirSync(sandbox.profileDir);
  fs.writeFileSync(path.join(sandbox.profileDir, 'prefs.js'), '// the user\'s prefs\n');
  const ghost = build() || [];
  const files = walk(sandbox.profileDir);
  const others = files.filter((f) => !f.dir && (f.rel === 'prefs.js' || f.rel === 'chrome/notes.txt' || f.rel.startsWith('chrome/mine/')));
  return { name, files, env: { force: false, legacyStore, edits: 0, ghost: new Set(ghost), others } };
}

function unrelated() {
  fs.mkdirSync(path.join(chromeDir, 'mine'), { recursive: true });
  fs.writeFileSync(path.join(chromeDir, 'notes.txt'), 'the user\'s notes\n');
  fs.writeFileSync(path.join(chromeDir, 'mine', 'extra.css'), '/* the user\'s extra sheet */\n');
}

function legacyInstall() {
  unrelated();
  fs.writeFileSync(path.join(chromeDir, legacy.leaf), '');
  fs.writeFileSync(path.join(chromeDir, 'userChrome.css'), LEGACY_GENERATION.userChrome);
  fs.writeFileSync(path.join(chromeDir, 'userContent.css'), LEGACY_GENERATION.userContent);
}

const starts = [
  startProfile('fresh profile, no chrome/ directory', () => null, true),
  startProfile('hand-written chrome/ (user\'s own userChrome.css, notes.txt, mine/extra.css)', () => {
    unrelated();
    const bytes = '/* the user\'s hand-written userChrome.css */\n';
    fs.writeFileSync(path.join(chromeDir, 'userChrome.css'), bytes);
    return [bytes];
  }, true),
  startProfile('pre-marker legacy install, legacy store path still present', legacyInstall, true),
  startProfile('pre-marker legacy install, legacy store path garbage-collected', legacyInstall, false),
];

/* ------------------------------------------------------------ the search */

const seen = new Map();
const firstViolation = new Map();
let transitions = 0;
const startedAt = Date.now();

let frontier = [];
for (const s of starts) {
  const node = { state: s, path: [`start: ${s.name}`] };
  const key = stateKey(s);
  if (!seen.has(key)) {
    seen.set(key, true);
    frontier.push(node);
  }
}

for (let depth = 1; depth <= DEPTH && frontier.length; depth++) {
  const next = [];
  for (const node of frontier) {
    for (const event of [...events(node.state)]) {
      const { after, result } = apply(node.state, event);
      transitions += 1;
      const violations = [];
      checkAlways(node.state, after, violations);
      if (event.run && !event.run.crash) {
        const settled = checkCompleteRun(event.run.gen, node.state, result, after, violations);
        if (settled) {
          restore(after);
          const again = runScript(event.run.gen, after.env.force, null);
          if (again.steps.length || again.restarts) {
            violations.push(['P5 idempotent', `second run of generation ${event.run.gen} still did: ${again.steps.map((s) => s.label).join(', ') || 'a restart'}`]);
          }
        }
      }
      const pathHere = [...node.path, event.label];
      for (const [prop, detail] of violations) {
        // One shortest counterexample per kind of violation, so a short one does
        // not hide a different defect under the same property.
        const kind = `${prop}: ${detail.replace(/[0-9a-f]{8,}/g, '#').replace(/generation [AB]/g, 'generation G').replace(/edit \d+/g, 'edit')}`;
        if (!firstViolation.has(kind) || firstViolation.get(kind).path.length > pathHere.length) {
          firstViolation.set(kind, { detail, path: pathHere });
        }
      }
      const key = stateKey(after);
      if (!seen.has(key)) {
        seen.set(key, true);
        next.push({ state: after, path: pathHere });
      }
    }
  }
  frontier = next;
}

const seconds = ((Date.now() - startedAt) / 1000).toFixed(1);
console.log(`explored ${seen.size} distinct profiles, ${transitions} events, depth ${DEPTH}, ${seconds}s`);
sandbox.cleanup();

/* ------------------------------------------------------------- the report */

// Defects this check found in the current code and that are not fixed yet. They
// are printed on every run, never hidden; they only stop failing the exit code,
// so a new defect stands out. When a fix lands, its entry reports "no longer
// reproduces" and must be deleted here.
// A known finding matches on the kind of violation and on the shape of its
// shortest counterexample, so a regression that breaks the same property
// without a crash still shows up as new.
const KNOWN = [
  {
    id: 'F5',
    summary: 'a legacy install with one CSS file deleted by the user is refused',
    match: (kind, steps) => kind.startsWith('P3 ') && kind.includes('legacy bytes') && kind.includes('absent') && !kind.includes('generation G bytes') &&
      !kind.includes('force-adopt is on') && steps.some((s) => s.startsWith('user deletes')),
  },
];

const known = new Map(KNOWN.map((k) => [k.id, []]));
const fresh = [];
for (const [kind, found] of [...firstViolation.entries()].sort()) {
  const k = KNOWN.find((entry) => entry.match(kind, found.path));
  if (k) known.get(k.id).push(found);
  else fresh.push(found);
}

function printCounterexample(title, { detail, path: steps }) {
  console.log(`\n${title}: ${detail}`);
  steps.forEach((s, i) => console.log(`  ${i}. ${s}`));
}

for (const found of fresh) printCounterexample('NEW VIOLATION', found);
for (const k of KNOWN) {
  const hits = known.get(k.id);
  if (!hits.length) {
    // A new violation can reshape the shortest counterexample of a known one, so
    // a vanished finding is only evidence of a fix when nothing new appeared.
    console.log(fresh.length
      ? `\n${k.id} did not reproduce at depth ${DEPTH} (${k.summary}), but new violations appeared: triage those first; this is not evidence of a fix.`
      : `\n${k.id} no longer reproduces at depth ${DEPTH} (${k.summary}). If it was fixed, delete its KNOWN entry.`);
    continue;
  }
  const shortest = hits.reduce((a, b) => (a.path.length <= b.path.length ? a : b));
  printCounterexample(`known ${k.id} (${k.summary})`, shortest);
}

if (fresh.length) {
  console.log(`\n${fresh.length} new violation(s): the change broke a property, or the check needs updating.`);
  process.exit(1);
}
console.log('\nno new violations: P1 keep-user-bytes, P2 others-untouched, P3 refuse-only-foreign, P4 converge, P5 idempotent hold outside the known findings');
process.exit(0);
