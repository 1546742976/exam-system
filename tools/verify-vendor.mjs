import { readFileSync, readdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
const manifest=JSON.parse(readFileSync('third-party/veyon-manifest.json','utf8'));
for(const file of manifest.files){const actual=createHash('sha256').update(readFileSync(`vendor/veyon/${file.path}`)).digest('hex');if(actual!==file.sha256)throw Error(`Snapshot differs: ${file.path}`);}
function scan(dir){for(const entry of readdirSync(dir,{withFileTypes:true})){if(entry.name==='.git')throw Error('Git metadata leaked into snapshot');if(entry.isDirectory())scan(`${dir}/${entry.name}`);}}
scan('vendor/veyon');console.log(`Verified ${manifest.files.length} files; ${manifest.submodules.length} pinned submodules; no .git metadata.`);
