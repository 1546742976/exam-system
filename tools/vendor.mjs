import { execFileSync } from 'node:child_process';
import { mkdirSync, readdirSync, readFileSync, writeFileSync, copyFileSync, lstatSync, readlinkSync, symlinkSync } from 'node:fs';
import { createHash } from 'node:crypto';
import path from 'node:path';
const source = '.source-cache/veyon';
const git = (...args) => execFileSync('git', ['-C', source, ...args], { encoding: 'utf8' }).trim();
const commit = git('rev-parse','HEAD');
if (commit !== '1cebf45f6c1af8bab2c96278a58db1cf2f56685d') throw Error('Unexpected Veyon commit');
const modules = git('submodule','status','--recursive');
if (modules.split('\n').some(s => /^[+U-]/.test(s))) throw Error('Incomplete submodules');
const files = [];
function copy(relative = '') {
  const dir = path.join(source, relative);
  for (const item of readdirSync(dir)) {
    if (item === '.git') continue;
    const rel = path.join(relative, item); const from = path.join(source, rel); const to = path.join('vendor/veyon', rel); const st = lstatSync(from);
    if (st.isDirectory()) { mkdirSync(to, { recursive: true }); copy(rel); }
    else { mkdirSync(path.dirname(to), { recursive: true }); if (st.isSymbolicLink()) symlinkSync(readlinkSync(from), to); else copyFileSync(from, to); files.push({ path: rel.replaceAll('\\','/'), sha256: createHash('sha256').update(readFileSync(from)).digest('hex') }); }
  }
}
copy(); mkdirSync('third-party', { recursive: true });
writeFileSync('third-party/veyon-manifest.json', JSON.stringify({ repository: 'https://github.com/veyon/veyon', tag: 'v4.11.4', commit, submodules: modules.split('\n'), files }, null, 2) + '\n');
console.log(JSON.stringify({ commit, files: files.length, submodules: modules.split('\n').length }));
