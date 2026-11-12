#!/usr/bin/env node
'use strict';
/*
Protocol tests for the textfox NixOS autoconfig installer.

What is tested: the generated autoconfig JavaScript from nix/pkgs/wrapTextfox.nix,
run unmodified against a real filesystem (a temp directory) through a shim of the
XPCOM surface it uses — nsIFile create/remove/copyTo/moveTo, file and converter
streams, nsICryptoHash, Services.dirsvc, Services.prefs, Cu.reportError and
app-startup quit.

What is proven: ownership gates, refusal without writes or restart, staging +
same-directory rename commit, marker commit point, asset-directory swap, legacy
pre-marker adoption, force-adopt backup, idempotent no-op runs, and convergence
after a crash at *every* mutating step.

What is NOT proven here: that these file APIs behave the same inside a real
Firefox process, and that activation restarts Firefox as intended. That is V7's
requirement and belongs to the NixOS VM scenario
(tools/firefox-chrome/test-nixos-update-vm.sh). This file is the cheap,
exhaustive counterpart, not a substitute.

Exit 0 means every scenario below holds.
*/

const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');

const REPO = path.resolve(__dirname, '..', '..');
const NIX_FILE = path.join(REPO, 'nix', 'pkgs', 'wrapTextfox.nix');
const KEEP = process.env.TEXTFOX_TEST_KEEP === '1';

const DIRECTORY_TYPE = 1;
const SCRIPT_BEGIN = '/* TEXTFOX GENERATED CONFIG */';
const SCRIPT_END = '/* END TEXTFOX AUTOCONFIG */';

/* ------------------------------------------------------------------ script */

function generatedScriptTemplate() {
  const source = fs.readFileSync(NIX_FILE, 'utf8');
  const begin = source.indexOf(SCRIPT_BEGIN);
  const end = source.indexOf(SCRIPT_END, begin);
  assert(begin >= 0, `autoconfig script start marker missing from ${NIX_FILE}`);
  assert(end > begin, `autoconfig script end marker missing from ${NIX_FILE}`);
  let code = source.slice(begin + SCRIPT_BEGIN.length, end);
  const storeTokens = code.split('"${textfoxChrome}"').length - 1;
  assert.equal(storeTokens, 1, 'expected exactly one ${textfoxChrome} interpolation in the script');
  code = code.replace('"${textfoxChrome}"', '"@@STORE_DIR@@"');
  return code;
}

const CODE_TEMPLATE = generatedScriptTemplate();

function codeFor(storeAbsPath) {
  return CODE_TEMPLATE.replace('@@STORE_DIR@@', storeAbsPath);
}

/* ------------------------------------------------- fixtures (store layout) */

function sha256Hex(data) {
  return crypto.createHash('sha256').update(data).digest('hex');
}

function assetDigest(icons) {
  // Mirrors the builder: hash the sorted "path digest" listing of the icon set.
  const lines = Object.keys(icons)
    .sort()
    .map((name) => `./${name} ${sha256Hex(Buffer.from(icons[name]))}\n`);
  return sha256Hex(lines.join('')).slice(0, 8);
}

function storeLeafName(seed) {
  // Same shape as a store path leaf: <32 hex>-textfox-chrome. The legacy marker
  // detector keys on exactly that shape.
  return `${sha256Hex(seed).slice(0, 32)}-textfox-chrome`;
}

const GENERATION_A = {
  seed: 'textfox-generation-a',
  userChrome: '/* gen A */ #1111AA\n',
  userContent: '/* gen A content */\n',
  icons: {
    'a.svg': '<svg id="a"/>',
    'b.svg': '<svg id="b"/>',
  },
};

const LEGACY_GENERATION = {
  seed: 'textfox-legacy-generation',
  userChrome: '/* legacy */ #3333CC\n',
  userContent: '/* legacy content */\n',
  icons: { 'old.svg': '<svg id="old"/>' },
};

const GENERATION_B = {
  seed: 'textfox-generation-b',
  userChrome: '/* gen B */ #2222BB\n',
  userContent: '/* gen B content */\n',
  icons: {
    'a.svg': '<svg id="a"/>',
    'b.svg': '<svg id="b"/>',
    'c.svg': '<svg id="c"/>',
  },
};

function writeStore(sandbox, generation) {
  const leaf = storeLeafName(generation.seed);
  const dir = path.join(sandbox.storeRoot, leaf);
  const assetsDir = `textfox-assets-${assetDigest(generation.icons)}`;
  const css = {
    'userChrome.css': sha256Hex(Buffer.from(generation.userChrome)),
    'userContent.css': sha256Hex(Buffer.from(generation.userContent)),
  };

  fs.mkdirSync(path.join(dir, assetsDir), { recursive: true });
  for (const [name, body] of Object.entries(generation.icons)) {
    const iconPath = path.join(dir, assetsDir, name);
    fs.writeFileSync(iconPath, body);
    fs.chmodSync(iconPath, 0o444); // store files are read-only
  }
  fs.writeFileSync(path.join(dir, 'userChrome.css'), generation.userChrome);
  fs.writeFileSync(path.join(dir, 'userContent.css'), generation.userContent);
  fs.chmodSync(path.join(dir, 'userChrome.css'), 0o444);
  fs.chmodSync(path.join(dir, 'userContent.css'), 0o444);

  const manifest = [`assetsDir ${assetsDir}`];
  for (const name of Object.keys(css)) manifest.push(`css ${name} ${css[name]}`);
  for (const name of Object.keys(generation.icons).sort()) {
    manifest.push(`icon ${name} ${sha256Hex(Buffer.from(generation.icons[name]))}`);
  }
  fs.writeFileSync(path.join(dir, 'manifest.sha256'), `${manifest.join('\n')}\n`);
  fs.chmodSync(path.join(dir, 'manifest.sha256'), 0o444);

  return { leaf, dir, abs: `/nix/store/${leaf}`, assetsDir, css, manifest, icons: generation.icons };
}

/* ------------------------------------------------------------ xpcom shim */

class Sandbox {
  constructor() {
    this.root = fs.mkdtempSync(path.join(os.tmpdir(), 'textfox-autoconfig-'));
    this.profileDir = path.join(this.root, 'profile');
    this.storeRoot = path.join(this.root, 'store');
    fs.mkdirSync(this.profileDir, { recursive: true });
    fs.mkdirSync(this.storeRoot, { recursive: true });
    this.steps = []; // every mutating step, in order, across all runs
    this.runs = [];
    this.run = null;
    this.failIndex = -1;
    this.injection = null;
  }

  // The generated script addresses the store as /nix/store/<leaf>. The shim maps
  // that prefix into the temp directory, which is also what makes the legacy
  // adoption path (which hard-codes /nix/store/) testable.
  abs(target) {
    if (target === '/nix/store') return this.storeRoot;
    if (target.startsWith('/nix/store/')) return path.join(this.storeRoot, target.slice('/nix/store/'.length));
    return target;
  }

  label(absPath) {
    if (absPath === this.profileDir) return 'profile';
    if (absPath.startsWith(`${this.profileDir}${path.sep}`)) return `profile${absPath.slice(this.profileDir.length)}`;
    if (absPath.startsWith(`${this.storeRoot}${path.sep}`)) return `store${absPath.slice(this.storeRoot.length)}`;
    return absPath;
  }

  step(verb, absPath) {
    const entry = `${verb} ${this.label(absPath)}`;
    const index = this.steps.length;
    this.steps.push(entry);
    if (this.run) this.run.steps.push(entry);
    if (index === this.failIndex) {
      this.injection = { index, step: entry };
      throw new Error(`injected failure at step ${index}: ${entry}`);
    }
  }

  beginRun() {
    this.run = { steps: [], errors: [], restarts: 0, prefs: [] };
    this.runs.push(this.run);
    return this.run;
  }

  failAt(index) {
    this.failIndex = index;
  }

  clearFailure() {
    this.failIndex = -1;
  }

  cleanup() {
    if (KEEP) {
      console.log(`kept sandbox: ${this.root}`);
      return;
    }
    fs.rmSync(this.root, { recursive: true, force: true });
  }
}

function makeContext(sandbox, options = {}) {
  const prefs = options.prefs || {};
  const report = (verb, absPath) => sandbox.step(verb, absPath);

  class FsFile {
    constructor(absPath) {
      this.path = absPath;
    }
    get leafName() {
      return path.basename(this.path);
    }
    get parent() {
      return new FsFile(path.dirname(this.path));
    }
    clone() {
      return new FsFile(this.path);
    }
    append(name) {
      this.path = path.join(this.path, name);
    }
    exists() {
      return fs.existsSync(sandbox.abs(this.path));
    }
    isFile() {
      try {
        return fs.statSync(sandbox.abs(this.path)).isFile();
      } catch (error) {
        return false;
      }
    }
    isDirectory() {
      try {
        return fs.statSync(sandbox.abs(this.path)).isDirectory();
      } catch (error) {
        return false;
      }
    }
    get permissions() {
      return fs.statSync(sandbox.abs(this.path)).mode & 0o777;
    }
    set permissions(mode) {
      fs.chmodSync(sandbox.abs(this.path), mode);
    }
    create(type, permissions) {
      const abs = sandbox.abs(this.path);
      report(type === DIRECTORY_TYPE ? 'mkdir' : 'create', abs);
      if (type === DIRECTORY_TYPE) fs.mkdirSync(abs);
      else fs.writeFileSync(abs, '');
      fs.chmodSync(abs, permissions);
    }
    remove(recursive) {
      const abs = sandbox.abs(this.path);
      report('remove', abs);
      if (!recursive && this.isDirectory() && fs.readdirSync(abs).length) {
        throw new Error(`remove(false) on non-empty directory ${abs}`);
      }
      fs.rmSync(abs, { recursive: Boolean(recursive), force: false });
    }
    copyTo(dir, name) {
      const source = sandbox.abs(this.path);
      const dest = path.join(sandbox.abs(dir.path), name);
      report('copy', dest);
      if (fs.existsSync(dest)) throw new Error(`copyTo destination exists: ${dest}`);
      const mode = fs.statSync(source).mode & 0o777;
      if (fs.statSync(source).isDirectory()) fs.cpSync(source, dest, { recursive: true });
      else fs.copyFileSync(source, dest);
      fs.chmodSync(dest, mode);
    }
    moveTo(dir, name) {
      const source = sandbox.abs(this.path);
      const dest = path.join(sandbox.abs(dir.path), name);
      // nsIFile.moveTo replaces a regular file in the same directory: this is the
      // single atomic step the install protocol commits with.
      report('move', dest);
      fs.renameSync(source, dest);
    }
    get directoryEntries() {
      const abs = sandbox.abs(this.path);
      const names = fs.readdirSync(abs);
      let next = 0;
      return {
        hasMoreElements: () => next < names.length,
        getNext() {
          const entry = new FsFile(path.join(abs, names[next++]));
          entry.QueryInterface = () => entry;
          return entry;
        },
      };
    }
  }

  class FileInputStream {
    init(file) {
      this.file = file;
      this.data = null;
    }
    readAll() {
      if (this.data === null) this.data = fs.readFileSync(sandbox.abs(this.file.path));
      return this.data;
    }
    close() {}
  }

  class CryptoHash {
    init(algorithm) {
      this.hash = crypto.createHash('sha256');
      this.algorithm = algorithm;
    }
    updateFromStream(stream) {
      const data = stream.readAll();
      this.hash.update(data);
      return data.length;
    }
    finish(ascii) {
      assert.equal(ascii, false, 'autoconfig must ask for binary digest output');
      return this.hash.digest('latin1');
    }
  }

  class FileOutputStream {
    init(file, ioFlags, permissions) {
      const abs = sandbox.abs(file.path);
      report('write', abs);
      fs.writeFileSync(abs, '');
      fs.chmodSync(abs, permissions & 0o777 || 0o644);
      this.file = file;
    }
    write(text) {
      fs.appendFileSync(sandbox.abs(this.file.path), text);
    }
    close() {}
  }

  class ConverterOutputStream {
    init(stream) {
      this.stream = stream;
    }
    writeString(text) {
      this.stream.write(text);
    }
    close() {
      this.stream.close();
    }
  }

  class ConverterInputStream {
    init(stream) {
      this.stream = stream;
      this.text = null;
      this.offset = 0;
    }
    readString(count, result) {
      if (this.text === null) this.text = this.stream.readAll().toString('utf8');
      const chunk = this.text.slice(this.offset, this.offset + count);
      this.offset += chunk.length;
      result.value = chunk;
      return chunk.length;
    }
    close() {}
  }

  const Ci = {
    nsIFile: { DIRECTORY_TYPE, NORMAL_FILE_TYPE: 0 },
    nsICryptoHash: { SHA256: 3 },
    nsIConverterInputStream: { DEFAULT_REPLACEMENT_CHARACTER: 0xfffd },
    nsIAppStartup: { eForceQuit: 1, eRestart: 8 },
  };

  const Cc = {
    '@mozilla.org/network/file-input-stream;1': { createInstance: () => new FileInputStream() },
    '@mozilla.org/network/file-output-stream;1': { createInstance: () => new FileOutputStream() },
    '@mozilla.org/intl/converter-input-stream;1': { createInstance: () => new ConverterInputStream() },
    '@mozilla.org/intl/converter-output-stream;1': { createInstance: () => new ConverterOutputStream() },
    '@mozilla.org/security/hash;1': { createInstance: () => new CryptoHash() },
    '@mozilla.org/toolkit/app-startup;1': {
      getService: () => ({
        quit(flags) {
          const restart = (flags & Ci.nsIAppStartup.eForceQuit) && (flags & Ci.nsIAppStartup.eRestart);
          assert(restart, `quit() called with flags ${flags}: expected force-quit | restart`);
          sandbox.run.restarts += 1;
        },
      }),
    },
  };

  const Cu = {
    reportError(message) {
      sandbox.run.errors.push(String(message));
    },
  };

  class FileUtilsFile extends FsFile {}

  const FileUtils = {
    File: FileUtilsFile,
    PERMS_DIRECTORY: 0o755,
  };

  const Services = {
    dirsvc: {
      get(key) {
        assert.equal(key, 'ProfD', 'autoconfig must resolve the profile directory by name');
        return new FsFile(sandbox.profileDir);
      },
    },
    prefs: {
      getBoolPref(name, fallback) {
        if (Object.prototype.hasOwnProperty.call(prefs, name)) return prefs[name];
        if (arguments.length > 1) return fallback;
        throw new Error(`no such pref: ${name}`);
      },
    },
  };

  return {
    Components: { classes: Cc, interfaces: Ci, utils: Cu },
    ChromeUtils: {
      importESModule(uri) {
        assert.match(uri, /FileUtils\.sys\.mjs$/, `unexpected module import: ${uri}`);
        return { FileUtils };
      },
    },
    Services,
    Cc,
    Ci,
    Cu,
    FileUtils,
    pref(name, value) {
      sandbox.run.prefs.push([name, value]);
    },
  };
}

function install(sandbox, options = {}) {
  const run = sandbox.beginRun();
  sandbox.injection = null;
  let thrown = null;
  try {
    vm.runInNewContext(codeFor(options.storeAbs), makeContext(sandbox, options), { timeout: 30000 });
  } catch (error) {
    thrown = error;
  }
  if (sandbox.injection) {
    // Production code is allowed to catch and report a failure (removal is the one
    // guarded step). The harness still has to know the step was interrupted, so the
    // injection is re-reported here and flagged when the run swallowed it.
    const error = new Error(`injected failure at step ${sandbox.injection.index}: ${sandbox.injection.step}`);
    error.injectedFailure = true;
    error.swallowed = thrown === null;
    throw error;
  }
  if (thrown) throw thrown;
  return run;
}

/* --------------------------------------------------------------- assertions */

function chromeListing(sandbox) {
  const dir = path.join(sandbox.profileDir, 'chrome');
  const listing = {
    exists: fs.existsSync(dir),
    entries: [],
    css: {},
    files: {},
    modes: {},
    marker: null,
    markerRaw: null,
    staging: [],
    assetsDirs: [],
    backups: [],
  };
  if (!listing.exists) return listing;
  listing.entries = fs.readdirSync(dir).sort();
  for (const name of listing.entries) {
    const abs = path.join(dir, name);
    const stat = fs.statSync(abs);
    if (stat.isDirectory()) {
      if (name.startsWith('textfox-assets-')) listing.assetsDirs.push(name);
      else if (name.startsWith('textfox-backup-')) listing.backups.push(name);
      else listing.staging.push(name);
      continue;
    }
    const digest = sha256Hex(fs.readFileSync(abs));
    listing.modes[name] = stat.mode & 0o777;
    if (name.startsWith('.textfox-staging-')) {
      listing.staging.push(name);
      listing.files[name] = digest;
    } else if (name === 'textfox-installed.json') {
      listing.files[name] = digest;
      listing.markerRaw = fs.readFileSync(abs, 'utf8');
      try {
        listing.marker = JSON.parse(listing.markerRaw);
      } catch (error) {
        listing.marker = null;
      }
    } else if (name === 'userChrome.css' || name === 'userContent.css') {
      listing.css[name] = digest;
    } else {
      listing.files[name] = digest;
    }
  }
  return listing;
}

function profileFile(sandbox, rel, contents) {
  const abs = path.join(sandbox.profileDir, rel);
  fs.mkdirSync(path.dirname(abs), { recursive: true });
  fs.writeFileSync(abs, contents);
  return abs;
}

function hasRefusal(run) {
  return run.errors.some((message) => message.startsWith('textfox: refusing to install'));
}

function describe(sandbox) {
  const lines = [];
  for (const [index, run] of sandbox.runs.entries()) {
    lines.push(`run ${index}: steps=${JSON.stringify(run.steps)} prefs=${run.prefs.length} restarts=${run.restarts}`);
    for (const error of run.errors) lines.push(`  reportError: ${error}`);
  }
  lines.push(`chrome: ${JSON.stringify(chromeListing(sandbox), null, 2)}`);
  lines.push(`sandbox: ${sandbox.root}`);
  return lines.join('\n');
}

const tests = [];
function test(name, body) {
  tests.push({ name, body });
}

/* ------------------------------------------------------------------ scenarios */

test('fresh profile installs assets, css and marker, then restarts once', (sandbox) => {
  const gen = writeStore(sandbox, GENERATION_A);
  const run = install(sandbox, { storeAbs: gen.abs });

  const listing = chromeListing(sandbox);
  assert.equal(listing.exists, true, 'chrome directory must be created');
  assert.deepEqual(listing.css, gen.css, 'installed stylesheets must hold the store bytes');
  assert.deepEqual(listing.assetsDirs, [gen.assetsDir], 'asset directory must be named by the build digest');
  assert.equal(listing.staging.length, 0, 'no staging entries may survive a successful install');
  for (const name of Object.keys(gen.css)) {
    assert.equal(listing.modes[name], 0o644, `${name} must stay editable by the user`);
  }
  assert.equal(fs.statSync(path.join(sandbox.profileDir, 'chrome', gen.assetsDir)).mode & 0o777, 0o755);
  assert.deepEqual(
    fs.readdirSync(path.join(sandbox.profileDir, 'chrome', gen.assetsDir)).sort(),
    Object.keys(gen.icons).sort(),
    'every icon must be installed',
  );
  assert.deepEqual(listing.marker, { protocol: 1, store: gen.leaf, assetsDir: gen.assetsDir, css: gen.css });
  assert.equal(run.restarts, 1, 'a fresh install must restart Firefox once');
  assert.deepEqual(run.errors, [], 'a fresh install must not report errors');
});

test('prefs are set before any profile write', (sandbox) => {
  const gen = writeStore(sandbox, GENERATION_A);
  const run = install(sandbox, { storeAbs: gen.abs });
  assert.deepEqual(run.prefs.map(([name]) => name), [
    'toolkit.legacyUserProfileCustomizations.stylesheets',
    'svg.context-properties.content.enabled',
    'layout.css.has-selector.enabled',
    'browser.nova.enabled',
  ]);
  assert.deepEqual(run.prefs.find(([name]) => name === 'browser.nova.enabled'), ['browser.nova.enabled', false]);
});

test('second run is a no-op: no writes, no restart, no errors', (sandbox) => {
  const gen = writeStore(sandbox, GENERATION_A);
  install(sandbox, { storeAbs: gen.abs });
  const before = chromeListing(sandbox);
  const run = install(sandbox, { storeAbs: gen.abs });
  assert.deepEqual(run.steps, [], 'an up-to-date profile must not be written at all');
  assert.equal(run.restarts, 0, 'an up-to-date profile must not restart Firefox');
  assert.deepEqual(run.errors, []);
  assert.deepEqual(chromeListing(sandbox), before);
});

test('generation switch replaces css, swaps the asset set and keeps user files', (sandbox) => {
  const genA = writeStore(sandbox, GENERATION_A);
  const genB = writeStore(sandbox, GENERATION_B);
  profileFile(sandbox, 'chrome/config.css', '/* user queued config */\n');
  const untouched = [
    ['chrome/config.css', sha256Hex(Buffer.from('/* user queued config */\n'))],
    ['user.js', sha256Hex(Buffer.from('user prefs live here\n'))],
    ['chrome/unrelated.txt', sha256Hex(Buffer.from('not ours\n'))],
  ];
  profileFile(sandbox, 'user.js', 'user prefs live here\n');
  profileFile(sandbox, 'chrome/unrelated.txt', 'not ours\n');

  install(sandbox, { storeAbs: genA.abs });
  const run = install(sandbox, { storeAbs: genB.abs });
  const listing = chromeListing(sandbox);

  assert.deepEqual(listing.css["userChrome.css"], genB.css['userChrome.css']);
  assert.deepEqual(listing.css["userContent.css"], genB.css['userContent.css']);
  assert.deepEqual(listing.assetsDirs, [genB.assetsDir], 'the previous asset set must be swept');
  assert.deepEqual(listing.marker, { protocol: 1, store: genB.leaf, assetsDir: genB.assetsDir, css: genB.css });
  assert.equal(run.restarts, 1, 'a changed stylesheet must restart Firefox once');
  assert.deepEqual(run.errors, []);
  for (const [rel, digest] of untouched) {
    assert.equal(sha256Hex(fs.readFileSync(path.join(sandbox.profileDir, rel))), digest, `${rel} must be preserved`);
  }
});

test('a foreign chrome directory is refused, unwritten and never restarted', (sandbox) => {
  const gen = writeStore(sandbox, GENERATION_A);
  profileFile(sandbox, 'chrome/userChrome.css', '/* mine, hand written */\n');
  const before = chromeListing(sandbox);
  const run = install(sandbox, { storeAbs: gen.abs });

  assert.equal(hasRefusal(run), true, 'the refusal must be reported');
  assert.equal(run.errors.filter((m) => m.startsWith('textfox: refusing')).length, 1,
    'a refusal must be reported exactly once, not once per gate');
  assert.equal(run.restarts, 0, 'a refusal must never restart Firefox');
  assert.equal(run.steps.includes('mkdir profile/chrome'), false, 'a refusal must not create anything');
  assert.deepEqual(chromeListing(sandbox), before, 'a refusal must leave the profile untouched');
});

test('a chrome directory without any css is taken as ours and converges', (sandbox) => {
  const gen = writeStore(sandbox, GENERATION_A);
  profileFile(sandbox, 'chrome/config.css', '/* user config */\n');
  const run = install(sandbox, { storeAbs: gen.abs });
  const listing = chromeListing(sandbox);
  assert.deepEqual(run.errors, []);
  assert.deepEqual(listing.css["userChrome.css"], gen.css['userChrome.css']);
  assert.deepEqual(listing.marker, { protocol: 1, store: gen.leaf, assetsDir: gen.assetsDir, css: gen.css });
  assert.equal(run.restarts, 1);
});

test('a file edited after install is refused untouched', (sandbox) => {
  const gen = writeStore(sandbox, GENERATION_A);
  install(sandbox, { storeAbs: gen.abs });
  const edited = profileFile(sandbox, 'chrome/userContent.css', '/* user tweaked this */\n');
  const before = chromeListing(sandbox);
  const run = install(sandbox, { storeAbs: gen.abs });

  assert.equal(hasRefusal(run), true);
  assert.equal(run.errors.filter((m) => m.startsWith('textfox: refusing')).length, 1);
  assert.equal(run.restarts, 0);
  assert.deepEqual(run.steps, [], 'nothing may be written when a gate refuses');
  assert.deepEqual(chromeListing(sandbox), before);
  assert.equal(fs.readFileSync(edited, 'utf8'), '/* user tweaked this */\n');
});

test('force-adopt backs foreign files up, then converges', (sandbox) => {
  const gen = writeStore(sandbox, GENERATION_A);
  profileFile(sandbox, 'chrome/userChrome.css', '/* mine */\n');
  profileFile(sandbox, 'chrome/userContent.css', '/* mine too */\n');
  const run = install(sandbox, { storeAbs: gen.abs, prefs: { 'textfox.force-adopt': true } });
  const listing = chromeListing(sandbox);

  assert.equal(hasRefusal(run), false);
  assert.equal(listing.backups.length, 1, 'exactly one backup directory must be created');
  const backupDir = path.join(sandbox.profileDir, 'chrome', listing.backups[0]);
  assert.match(listing.backups[0], /^textfox-backup-\d{10}$/, 'the backup name must be a unix timestamp');
  assert.equal(fs.readFileSync(path.join(backupDir, 'userChrome.css'), 'utf8'), '/* mine */\n');
  assert.equal(fs.readFileSync(path.join(backupDir, 'userContent.css'), 'utf8'), '/* mine too */\n');
  assert.deepEqual(listing.css["userChrome.css"], gen.css['userChrome.css']);
  assert.deepEqual(listing.marker, { protocol: 1, store: gen.leaf, assetsDir: gen.assetsDir, css: gen.css });
  assert.equal(run.restarts, 1);
});

test('a legacy pre-marker install is adopted when its store path still confirms the bytes', (sandbox) => {
  const gen = writeStore(sandbox, GENERATION_A);
  // A pre-marker install from a store path that still exists and still holds the
  // bytes found in the profile, from a generation older than the current build.
  const legacy = writeStore(sandbox, LEGACY_GENERATION);
  profileFile(sandbox, 'chrome/userChrome.css', LEGACY_GENERATION.userChrome);
  profileFile(sandbox, 'chrome/userContent.css', LEGACY_GENERATION.userContent);
  profileFile(sandbox, `chrome/${legacy.leaf}`, '');
  const run = install(sandbox, { storeAbs: gen.abs });
  const listing = chromeListing(sandbox);

  assert.equal(hasRefusal(run), false, `adoption must not refuse: ${JSON.stringify(run.errors)}`);
  assert.deepEqual(listing.marker, { protocol: 1, store: gen.leaf, assetsDir: gen.assetsDir, css: gen.css });
  assert.equal(listing.entries.includes(legacy.leaf), false, 'the legacy marker must be removed');
  assert.deepEqual(listing.css, gen.css, 'the adopted install must be brought up to the current build');
  assert.deepEqual(listing.assetsDirs, [gen.assetsDir]);
  assert.equal(run.restarts, 1, 'an adopted install whose bytes changed must restart Firefox');
});

test('bytes that already match this build are taken as ours without a restart', (sandbox) => {
  const gen = writeStore(sandbox, GENERATION_A);
  profileFile(sandbox, 'chrome/userChrome.css', GENERATION_A.userChrome);
  profileFile(sandbox, 'chrome/userContent.css', GENERATION_A.userContent);
  const run = install(sandbox, { storeAbs: gen.abs });
  const listing = chromeListing(sandbox);
  assert.equal(hasRefusal(run), false);
  assert.deepEqual(listing.marker, { protocol: 1, store: gen.leaf, assetsDir: gen.assetsDir, css: gen.css });
  assert.equal(listing.staging.length, 0);
  assert.equal(run.restarts, 0, 'nothing was replaced, so no restart is owed');
});

test('a legacy install whose store path is gone is refused unless force-adopt', (sandbox) => {
  const gen = writeStore(sandbox, GENERATION_A);
  const legacy = writeStore(sandbox, LEGACY_GENERATION);
  // The store generation that installed the profile is gone from disk.
  fs.rmSync(legacy.dir, { recursive: true, force: true });
  profileFile(sandbox, 'chrome/userChrome.css', LEGACY_GENERATION.userChrome);
  profileFile(sandbox, 'chrome/userContent.css', LEGACY_GENERATION.userContent);
  profileFile(sandbox, `chrome/${legacy.leaf}`, '');
  const before = chromeListing(sandbox);

  const run = install(sandbox, { storeAbs: gen.abs });
  assert.equal(hasRefusal(run), true);
  assert.equal(run.errors.filter((m) => m.startsWith('textfox: refusing')).length, 1);
  assert.equal(run.restarts, 0);
  assert.deepEqual(chromeListing(sandbox), before, 'an unconfirmed legacy install must be left untouched');

  const forced = install(sandbox, { storeAbs: gen.abs, prefs: { 'textfox.force-adopt': true } });
  const listing = chromeListing(sandbox);
  assert.equal(hasRefusal(forced), false, `force-adopt must not refuse: ${JSON.stringify(forced.errors)}`);
  assert.equal(listing.backups.length, 1, 'exactly one backup directory must be created');
  assert.equal(
    fs.readFileSync(path.join(sandbox.profileDir, 'chrome', listing.backups[0], 'userChrome.css'), 'utf8'),
    LEGACY_GENERATION.userChrome,
  );
  assert.deepEqual(listing.marker, { protocol: 1, store: gen.leaf, assetsDir: gen.assetsDir, css: gen.css });
  assert.deepEqual(listing.css, gen.css);
  assert.equal(forced.restarts, 1);
});

test('a crash between the marker commit and cleanup self-heals on the next start', (sandbox) => {
  const gen = writeStore(sandbox, GENERATION_A);
  install(sandbox, { storeAbs: gen.abs });
  const installed = chromeListing(sandbox);
  const staleSet = path.join(sandbox.profileDir, 'chrome', 'textfox-assets-deadbeef');
  fs.mkdirSync(staleSet);
  fs.writeFileSync(path.join(staleSet, 'leftover.svg'), 'x');
  profileFile(sandbox, 'chrome/.textfox-staging-userChrome.css', 'half a copy\n');
  profileFile(sandbox, 'chrome/.textfox-staging-textfox-assets-deadbeef/leftover.svg', 'x');

  const run = install(sandbox, { storeAbs: gen.abs });
  const listing = chromeListing(sandbox);

  assert.deepEqual(listing.entries, installed.entries, 'leftovers from our own crash must be swept');
  assert.deepEqual(listing.css, installed.css, 'sweeping must not rewrite current stylesheets');
  assert.deepEqual(listing.marker, installed.marker);
  assert.equal(run.restarts, 0, 'nothing was replaced, so no restart is owed');
  assert.deepEqual(run.errors, []);
});

test('a corrupt marker is refused untouched', (sandbox) => {
  const gen = writeStore(sandbox, GENERATION_A);
  install(sandbox, { storeAbs: gen.abs });
  profileFile(sandbox, 'chrome/textfox-installed.json', '{ this is not json');
  const before = chromeListing(sandbox);
  const run = install(sandbox, { storeAbs: gen.abs });

  assert.equal(hasRefusal(run), true);
  assert.equal(run.errors.filter((m) => m.startsWith('textfox: refusing')).length, 1);
  assert.equal(run.restarts, 0);
  assert.deepEqual(run.steps, []);
  assert.deepEqual(chromeListing(sandbox), before);
});

test('a marker from an unknown protocol is refused untouched', (sandbox) => {
  const gen = writeStore(sandbox, GENERATION_A);
  install(sandbox, { storeAbs: gen.abs });
  const marker = chromeListing(sandbox).marker;
  profileFile(sandbox, 'chrome/textfox-installed.json', JSON.stringify({ ...marker, protocol: 2 }));
  const before = chromeListing(sandbox);
  const run = install(sandbox, { storeAbs: gen.abs });
  assert.equal(hasRefusal(run), true);
  assert.deepEqual(run.steps, []);
  assert.deepEqual(chromeListing(sandbox), before);
});

test('an incomplete store is refused untouched', (sandbox) => {
  const gen = writeStore(sandbox, GENERATION_A);
  fs.rmSync(path.join(gen.dir, 'manifest.sha256'));
  const run = install(sandbox, { storeAbs: gen.abs });
  assert.equal(hasRefusal(run), true);
  assert.equal(run.restarts, 0);
  assert.equal(chromeListing(sandbox).exists, false, 'a store without a manifest must install nothing');
});

test('an unrelated directory is never removed', (sandbox) => {
  const genA = writeStore(sandbox, GENERATION_A);
  const genB = writeStore(sandbox, GENERATION_B);
  fs.mkdirSync(path.join(sandbox.profileDir, 'chrome', 'textfox-assets-notadigest'), { recursive: true });
  fs.writeFileSync(path.join(sandbox.profileDir, 'chrome', 'textfox-assets-notadigest', 'x'), 'mine');
  install(sandbox, { storeAbs: genA.abs });
  install(sandbox, { storeAbs: genB.abs });
  assert.equal(fs.existsSync(path.join(sandbox.profileDir, 'chrome', 'textfox-assets-notadigest', 'x')), true);
});

const UPDATE_SCENARIOS = [
  {
    name: 'fresh install',
    // Profile state before the install under test; returns the store to install.
    prepare: (sandbox) => writeStore(sandbox, GENERATION_A),
  },
  {
    name: 'generation switch',
    prepare: (sandbox) => {
      const genA = writeStore(sandbox, GENERATION_A);
      const genB = writeStore(sandbox, GENERATION_B);
      profileFile(sandbox, 'chrome/config.css', '/* user config */\n');
      install(sandbox, { storeAbs: genA.abs });
      return genB;
    },
  },
  {
    // F1, F4: an adoption interrupted between or after its renames must neither
    // wedge the same generation nor strand the legacy marker.
    name: 'legacy adoption',
    prepare: (sandbox) => {
      const genA = writeStore(sandbox, GENERATION_A);
      const legacy = writeStore(sandbox, LEGACY_GENERATION);
      profileFile(sandbox, 'chrome/userChrome.css', LEGACY_GENERATION.userChrome);
      profileFile(sandbox, 'chrome/userContent.css', LEGACY_GENERATION.userContent);
      profileFile(sandbox, `chrome/${legacy.leaf}`, '');
      return genA;
    },
  },
];

// One crash window: interrupt the install immediately before the step at `index`,
// then let the next start retry, and require it to reach the clean-run state.
function checkCrashWindow(scenario, store, index, steps, cleanListing) {
  const sandbox = new Sandbox();
  try {
    scenario.prepare(sandbox);
    sandbox.failAt(sandbox.steps.length + index);
    let injected = null;
    try {
      install(sandbox, { storeAbs: store.abs });
    } catch (error) {
      injected = error;
    }
    assert.equal(
      Boolean(injected && injected.injectedFailure),
      true,
      `${scenario.name}: step ${index} (${steps[index]}) must be interrupted`,
    );
    const crashed = sandbox.runs[sandbox.runs.length - 1];
    if (!injected.swallowed) {
      assert.equal(crashed.restarts, 0, `${scenario.name}: an interrupted step must not restart Firefox`);
    }

    sandbox.clearFailure();
    const beforeRetry = chromeListing(sandbox);
    const retry = install(sandbox, { storeAbs: store.abs });
    assert.equal(
      hasRefusal(retry),
      false,
      `${scenario.name}: a crash before ${steps[index]} must stay retryable: ${JSON.stringify(retry.errors)}`,
    );
    assert.deepEqual(
      chromeListing(sandbox),
      cleanListing,
      `${scenario.name}: a crash before ${steps[index]} did not converge`,
    );
    assert.deepEqual(retry.errors, [], `${scenario.name}: a convergent retry must not report errors`);
    // Restart exactly when the surviving stylesheets were not this build's bytes:
    // this run is itself the startup that loads them, so a restart is only owed
    // when the bytes changed after this process read them.
    const staleAtRetry = Object.keys(store.css).some((name) => beforeRetry.css[name] !== store.css[name]);
    assert.equal(
      retry.restarts,
      staleAtRetry ? 1 : 0,
      `${scenario.name}: a crash before ${steps[index]} restarted the wrong number of times`,
    );  } catch (error) {
    error.message += `\n--- ${scenario.name}, crash window before step ${index}: ${steps[index]}\n${describe(sandbox)}`;
    throw error;
  } finally {
    sandbox.cleanup();
  }
}

test('a crash at every mutating step converges on the next start', () => {
  for (const scenario of UPDATE_SCENARIOS) {
    // Reference: the exact state a clean run produces. Every crash window must
    // retry into that same state.
    const reference = new Sandbox();
    try {
      const store = scenario.prepare(reference);
      const stepsBefore = reference.steps.length;
      install(reference, { storeAbs: store.abs });
      const steps = reference.steps.slice(stepsBefore);
      const cleanListing = chromeListing(reference);
      assert(
        steps.length >= 5,
        `${scenario.name}: expected several mutating steps, got ${JSON.stringify(steps)}`,
      );

      for (let index = 0; index < steps.length; index += 1) {
        checkCrashWindow(scenario, store, index, steps, cleanListing);
      }
    } finally {
      reference.cleanup();
    }
  }
});

// F2: textfox must own the bytes it is about to write before it writes them, so a
// crash leaves every file provably ours for any generation, not only the one that
// crashed. Each case crashes `first` at every step, then starts on `second`.
const CROSS_GENERATION_CASES = [
  { name: 'fresh A, then B', prepare: () => {}, first: GENERATION_A, second: GENERATION_B },
  {
    name: 'update A to B, then roll back to A',
    // Generation A (`second` here) is installed before the update under test.
    prepare: (sandbox, first, second) => install(sandbox, { storeAbs: second.abs }),
    first: GENERATION_B,
    second: GENERATION_A,
  },
];

test('a crash at every step stays retryable on a different generation', () => {
  for (const scenario of CROSS_GENERATION_CASES) {
    const reference = new Sandbox();
    let steps;
    let cleanListing;
    try {
      const first = writeStore(reference, scenario.first);
      const second = writeStore(reference, scenario.second);
      scenario.prepare(reference, first, second);
      const stepsBefore = reference.steps.length;
      install(reference, { storeAbs: first.abs });
      steps = reference.steps.slice(stepsBefore);
      install(reference, { storeAbs: second.abs });
      cleanListing = chromeListing(reference);
    } finally {
      reference.cleanup();
    }

    for (let index = 0; index < steps.length; index += 1) {
      const sandbox = new Sandbox();
      try {
        const first = writeStore(sandbox, scenario.first);
        const second = writeStore(sandbox, scenario.second);
        scenario.prepare(sandbox, first, second);
        sandbox.failAt(sandbox.steps.length + index);
        assert.throws(() => install(sandbox, { storeAbs: first.abs }), (error) => error.injectedFailure === true);
        sandbox.clearFailure();
        const retry = install(sandbox, { storeAbs: second.abs });
        assert.equal(hasRefusal(retry), false, `refused: ${JSON.stringify(retry.errors)}`);
        assert.deepEqual(retry.errors, []);
        assert.deepEqual(chromeListing(sandbox), cleanListing, 'did not converge');
      } catch (error) {
        error.message += `\n--- ${scenario.name}, crash before step ${index}: ${steps[index]}\n${describe(sandbox)}`;
        throw error;
      } finally {
        sandbox.cleanup();
      }
    }
  }
});

test('a partly removed asset directory is rebuilt, not reused by name', (sandbox) => {
  // F3: a removal of B's asset set that died part-way leaves the name behind with
  // icons missing. Coming back to B must not take the name as proof of content.
  const genA = writeStore(sandbox, GENERATION_A);
  const genB = writeStore(sandbox, GENERATION_B);
  install(sandbox, { storeAbs: genB.abs });
  install(sandbox, { storeAbs: genA.abs });
  profileFile(sandbox, `chrome/${genB.assetsDir}/c.svg`, GENERATION_B.icons['c.svg']);

  const run = install(sandbox, { storeAbs: genB.abs });
  const listing = chromeListing(sandbox);
  assert.deepEqual(run.errors, []);
  assert.deepEqual(listing.assetsDirs, [genB.assetsDir]);
  assert.deepEqual(listing.staging, []);
  const dir = path.join(sandbox.profileDir, 'chrome', genB.assetsDir);
  assert.deepEqual(fs.readdirSync(dir).sort(), Object.keys(genB.icons).sort(), 'every icon must be back');
  for (const [name, body] of Object.entries(genB.icons)) {
    assert.equal(fs.readFileSync(path.join(dir, name), 'utf8'), body, `${name} must hold the store bytes`);
  }
});

test('a real nix-built store layout is understood as-is', (sandbox) => {
  // Guards the harness itself: the fixture generator above must model the real
  // build exactly, or a green run here proves nothing about the build. Pass the
  // output of a `nix build` of the derivation to check it.
  const realStore = process.env.TEXTFOX_STORE;
  if (!realStore) {
    console.log('# skip - set TEXTFOX_STORE to the output of the textfox-chrome derivation');
    return;
  }
  const leaf = path.basename(realStore);
  const target = path.join(sandbox.storeRoot, leaf);
  fs.cpSync(realStore, target, { recursive: true });
  for (const name of fs.readdirSync(target)) {
    const abs = path.join(target, name);
    if (fs.statSync(abs).isFile()) fs.chmodSync(abs, 0o444);
  }

  const lines = fs.readFileSync(path.join(target, 'manifest.sha256'), 'utf8').trim().split('\n');
  const assetsDir = lines[0].replace(/^assetsDir /, '');
  assert.match(assetsDir, /^textfox-assets-[0-9a-f]{8}$/, `unexpected asset directory name: ${assetsDir}`);
  const css = {};
  const icons = {};
  for (const line of lines.slice(1)) {
    const [kind, name, digest] = line.split(' ');
    if (kind === 'css') css[name] = digest;
    else if (kind === 'icon') icons[name] = digest;
  }
  assert.deepEqual(Object.keys(css).sort(), ['userChrome.css', 'userContent.css']);

  // Recompute the manifest from the bytes on disk with the harness's own model of
  // the build, and require an exact match.
  assert.equal(assetsDir, `textfox-assets-${assetDigest(Object.fromEntries(
    Object.keys(icons).map((name) => [name, fs.readFileSync(path.join(target, assetsDir, name), 'utf8')]),
  ))}`, 'the harness model of the asset digest does not match the build');
  for (const name of Object.keys(css)) {
    assert.equal(css[name], sha256Hex(fs.readFileSync(path.join(target, name))), `${name} hash mismatch`);
  }
  for (const name of Object.keys(icons)) {
    assert.equal(icons[name], sha256Hex(fs.readFileSync(path.join(target, assetsDir, name))), `${name} icon hash mismatch`);
  }

  const run = install(sandbox, { storeAbs: `/nix/store/${leaf}` });
  const listing = chromeListing(sandbox);
  assert.deepEqual(listing.css, css, 'installed stylesheets must hold the built bytes');
  assert.deepEqual(listing.marker, { protocol: 1, store: leaf, assetsDir, css });
  assert.deepEqual(listing.assetsDirs, [assetsDir]);
  assert.equal(
    fs.readdirSync(path.join(sandbox.profileDir, 'chrome', assetsDir)).length,
    Object.keys(icons).length,
    'every icon in the build must be installed',
  );
  assert.equal(run.restarts, 1);
  assert.deepEqual(run.errors, []);
});

test('the script exercised here is the script Nix ships', () => {
  // Guards against the harness drifting from the build: everything above runs the
  // script text extracted from wrapTextfox.nix, so the shipped artefact has to be
  // that same text with the store path filled in.
  const shipped = process.env.TEXTFOX_AUTOCONFIG;
  if (!shipped) {
    console.log('# skip - set TEXTFOX_AUTOCONFIG to the built textfox autoconfig file');
    return;
  }
  const built = fs.readFileSync(shipped, 'utf8');
  const match = built.match(/"\/nix\/store\/([^"\/]+-textfox-chrome)"/);
  assert(match, 'the built autoconfig must name the store path it installs from');
  const expected = codeFor(`/nix/store/${match[1]}`);
  // The build dedents the script and appends the generated prefs, so compare the
  // script itself with indentation normalised, and require it to be the prefix.
  const normalise = (text) => text.split('\n').map((line) => line.trim()).join('\n').trim();
  assert.equal(
    normalise(built).includes(normalise(expected)),
    true,
    'the shipped autoconfig must contain the script extracted from wrapTextfox.nix',
  );
});

/* ---------------------------------------------------------------------- main */

function indent(text) {
  return String(text)
    .split('\n')
    .map((line) => `  ${line}`)
    .join('\n');
}

// The shim and fixtures above are shared with check-nixos-update-exhaustive.js,
// which drives the same unmodified script through every bounded event sequence.
module.exports = {
  Sandbox,
  makeContext,
  install,
  codeFor,
  writeStore,
  chromeListing,
  hasRefusal,
  sha256Hex,
  storeLeafName,
  GENERATION_A,
  GENERATION_B,
  LEGACY_GENERATION,
};

function main() {
  let failures = 0;
  for (const { name, body } of tests) {
    const sandbox = new Sandbox();
    let failure = null;
    try {
      body(sandbox);
    } catch (error) {
      failure = error;
    }
    if (failure) {
      failures += 1;
      console.log(`not ok - ${name}`);
      console.log(indent(failure.message));
      console.log(indent(describe(sandbox)));
    } else {
      console.log(`ok - ${name}`);
    }
    sandbox.cleanup();
  }

  if (failures) {
    console.log(`\n${failures} of ${tests.length} NixOS autoconfig protocol scenarios failed`);
    process.exit(1);
  }
  console.log(`\nall ${tests.length} NixOS autoconfig protocol scenarios hold`);
}

if (require.main === module) {
  main();
}
