#!/usr/bin/env node
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('nix/pkgs/wrapTextfox.nix', 'utf8');
const start = source.indexOf('    if (!chromeDir.exists()) {');
const end = source.indexOf('    // Needed prefs to use textfox', start);
assert(start >= 0 && end > start, 'autoconfig update block not found');
const code = source.slice(start, end);

function run(exists, markerExists, failContentCopy = false) {
  const events = [];
  const context = {
    chromeDir: {
      exists: () => exists,
      create: () => events.push('create directory'),
      remove: () => events.push('remove directory'),
    },
    hashFile: { exists: () => markerExists, create: () => events.push('create marker') },
    userChrome: { copyTo: () => events.push('copy chrome') },
    userContent: { copyTo: () => { events.push('copy content'); if (failContentCopy) throw new Error('copy failed'); } },
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
assert.deepEqual(run(true, false), ['report error'], 'existing profile must not be modified on update');
assert.deepEqual(run(true, true), [], 'current install must not be modified');
assert.deepEqual(run(false, false), ['create directory', 'copy chrome', 'copy content', 'create marker', 'restart'], 'fresh install must be installed');
assert.throws(() => run(false, false, true), /copy failed/, 'failed fresh install must not report success');
assert.deepEqual(run(true, false), ['report error'], 'retry after partial installation must refuse to overwrite files');
console.log('NixOS autoconfig update safety checks passed');
