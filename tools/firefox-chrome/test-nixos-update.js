#!/usr/bin/env node
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('nix/pkgs/wrapTextfox.nix', 'utf8');
const start = source.indexOf('    if (!chromeDir.exists()) {');
const end = source.indexOf('    // Needed prefs to use textfox', start);
assert(start >= 0 && end > start, 'autoconfig update block not found');
const code = source.slice(start, end);

function run(files, failAt, events = []) {
  function step(name, action) {
    events.push(name);
    if (name === failAt) throw new Error(`injected failure: ${name}`);
    action();
  }
  const context = {
    chromeDir: {
      exists: () => files.has('chrome/'),
      create: () => step('create directory', () => files.set('chrome/', 'directory')),
      remove: () => step('remove directory', () => files.clear()),
    },
    hashFile: {
      exists: () => files.has('chrome/store-hash'),
      create: () => step('create marker', () => files.set('chrome/store-hash', 'marker')),
    },
    userChrome: { copyTo: () => step('copy chrome', () => files.set('chrome/userChrome.css', 'generated chrome')) },
    userContent: { copyTo: () => step('copy content', () => files.set('chrome/userContent.css', 'generated content')) },
    textfoxChrome: { displayName: 'store-hash' },
    FileUtils: { PERMS_DIRECTORY: 0o755 },
    Ci: { nsIFile: { DIRECTORY_TYPE: 1, NORMAL_FILE_TYPE: 0 }, nsIAppStartup: { eForceQuit: 1, eRestart: 2 } },
    Cc: { '@mozilla.org/toolkit/app-startup;1': { getService: () => ({ quit: () => events.push('restart') }) } },
    Cu: { reportError: () => events.push('report error') },
    updated: false,
  };
  vm.runInNewContext(code, context);
  return events;
}

const fresh = new Map([['profile-marker', 'untouched']]);
assert.deepEqual(run(fresh), ['create directory', 'copy chrome', 'copy content', 'create marker', 'restart']);
assert.deepEqual([...fresh], [
  ['profile-marker', 'untouched'], ['chrome/', 'directory'],
  ['chrome/userChrome.css', 'generated chrome'], ['chrome/userContent.css', 'generated content'],
  ['chrome/store-hash', 'marker'],
]);
assert.deepEqual(run(fresh), [], 'current install must not be modified');

for (const [failure, expected] of [
  ['create directory', []],
  ['copy chrome', [['chrome/', 'directory']]],
  ['copy content', [['chrome/', 'directory'], ['chrome/userChrome.css', 'generated chrome']]],
  ['create marker', [['chrome/', 'directory'], ['chrome/userChrome.css', 'generated chrome'], ['chrome/userContent.css', 'generated content']]],
]) {
  const files = new Map([['profile-marker', 'untouched']]);
  const events = [];
  assert.throws(() => run(files, failure, events), new RegExp(`injected failure: ${failure}`));
  assert(!events.includes('restart'), `${failure} must not restart`);
  assert.deepEqual([...files], [['profile-marker', 'untouched'], ...expected], `${failure} leaves only completed operations`);
  if (failure === 'create directory') {
    assert.deepEqual(run(files), ['create directory', 'copy chrome', 'copy content', 'create marker', 'restart']);
  } else {
    const before = [...files];
    assert.deepEqual(run(files), ['report error'], `${failure} retry must refuse partial installation`);
    assert.deepEqual([...files], before, `${failure} retry must preserve partial installation`);
  }
}

for (const marker of [false, true]) {
  const files = new Map([['chrome/', 'directory'], ['chrome/config.css', 'user config'], ['chrome/unrelated', 'user data']]);
  if (marker) files.set('chrome/old-store-hash', 'old marker');
  const before = [...files];
  assert.deepEqual(run(files), ['report error'], 'existing or outdated installation must be refused');
  assert.deepEqual([...files], before, 'refusal must preserve existing files');
}

console.log('NixOS autoconfig update safety checks passed');
