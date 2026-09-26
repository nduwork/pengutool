#!/usr/bin/env node
/**
 * npm has been dropping the executable bit from node-pty's prebuilt `spawn-helper`, which makes
 * every pty spawn fail with "posix_spawnp failed". Restore it after install so control mode works.
 * Runs on macOS/Linux; silently does nothing where node-pty ships no prebuild for this platform.
 */
const fs = require('node:fs');
const path = require('node:path');

const prebuilds = path.join(__dirname, '..', 'node_modules', 'node-pty', 'prebuilds');
try {
  for (const platform of fs.readdirSync(prebuilds)) {
    const helper = path.join(prebuilds, platform, 'spawn-helper');
    if (fs.existsSync(helper)) { fs.chmodSync(helper, 0o755); }
  }
} catch { /* node-pty not installed or not a prebuild platform */ }
