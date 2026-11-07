import { readFile } from 'node:fs/promises';
import { VeyonClient } from '../addon/transport/veyon';
async function main() {
  if (!process.argv[2]) throw new Error('Usage: npm run probe -- device.json [output-directory]');
  const device = JSON.parse(await readFile(process.argv[2], 'utf8'));
  const result = await new VeyonClient().capture(device, { directory: process.argv[3] ?? 'data/probe' });
  console.log(JSON.stringify(result, null, 2)); if (result.error) process.exitCode = 1;
}
main().catch(() => { console.error('Probe setup failed; check device configuration and credential files.'); process.exitCode = 1; });
