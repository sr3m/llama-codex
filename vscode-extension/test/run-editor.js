'use strict';
const { runTests } = require('@vscode/test-electron');
const path = require('node:path');
const os = require('node:os');
const fs = require('node:fs/promises');
const { existsSync } = require('node:fs');

async function main() {
  const temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'llama-inline-editor-'));
  try {
    const executable = process.env.VSCODE_EXECUTABLE_PATH || (process.platform === 'darwin'
      ? ['Code', 'Electron'].map(name => '/Applications/Visual Studio Code.app/Contents/MacOS/' + name).find(existsSync) : undefined);
    await runTests({
      ...(executable ? { vscodeExecutablePath: executable } : {}),
      extensionDevelopmentPath: path.resolve(__dirname, '..'),
      extensionTestsPath: path.resolve(__dirname, 'editor-suite.js'),
      launchArgs: ['--user-data-dir', path.join(temporary, 'user'), '--extensions-dir', path.join(temporary, 'extensions'),
        '--disable-extensions', '--skip-welcome', '--skip-release-notes', '--disable-workspace-trust', '--new-window']
    });
  } finally { await fs.rm(temporary, { recursive: true, force: true }); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
