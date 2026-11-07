import {it} from 'vitest';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
it('archive regressions run with heap and time limits',async()=>{await promisify(execFile)(process.execPath,['--max-old-space-size=128','tools/archive-regression.cjs'],{timeout:10000,maxBuffer:100000});});
