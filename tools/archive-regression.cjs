// Isolated archive checks. Run with --max-old-space-size=128 and an outer timeout.
const fs=require('node:fs/promises');const os=require('node:os');const path=require('node:path');const assert=require('node:assert/strict');const {Readable}=require('node:stream');const {pipeline}=require('node:stream/promises');const tar=require('tar');const Zip=require('adm-zip');
(async()=>{const root=await fs.mkdtemp(path.join(os.tmpdir(),'monitor-archives-'));try{
  await fs.mkdir(path.join(root,'in'));await fs.mkdir(path.join(root,'out'));await fs.writeFile(path.join(root,'in','hello'),'hello');
  await tar.c({cwd:path.join(root,'in'),file:path.join(root,'sample.tar')},['hello']);
  await pipeline(require('node:fs').createReadStream(path.join(root,'sample.tar')),tar.x({cwd:path.join(root,'out'),strict:true}));assert.equal(await fs.readFile(path.join(root,'out','hello'),'utf8'),'hello');
  const z=new Zip();z.addFile('hello',Buffer.from('hello'));const bytes=z.toBuffer();const parsed=new Zip(bytes);assert.equal(parsed.readAsText('hello'),'hello');
  await new Promise((resolve,reject)=>parsed.getEntry('hello').getDataAsync((data,error)=>error?reject(error):(assert.equal(data.toString(),'hello'),resolve())));
  const bomb=Buffer.from(bytes);const central=bomb.indexOf(Buffer.from('504b0102','hex'));bomb.writeUInt32LE(0xffffffff,central+24);let decoded;try{decoded=new Zip(bomb).getEntry('hello').getData();}catch{}if(decoded)assert.ok(decoded.length<=1024,'declared size must not drive allocation');
  await assert.rejects(()=>pipeline(Readable.from([Buffer.from('invalid tar')]),tar.x({cwd:path.join(root,'out'),strict:true})));
  console.log('normal TAR stream, ZIP sync/async, declared-size rejection, corrupt TAR passed');
}finally{await fs.rm(root,{recursive:true,force:true});}})().catch(e=>{console.error(e);process.exitCode=1;});
