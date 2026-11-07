// Uses the installed HydroOJ runtime and a disposable home; no running database.
const fs=require('node:fs'); const path=require('node:path');const os=require('node:os');
const root=path.resolve('data/hydro-smoke');fs.mkdirSync(root,{recursive:true});os.homedir=()=>root;
require('@hydrooj/register');require('hydrooj/src/init');
const originalGet=global.app.get.bind(global.app);
global.app.get=(name)=>name==='db'?{collection:()=>({})}:originalGet(name);
async function main(){
  const registry=path.join(root,'devices.json');fs.writeFileSync(registry,'[]');process.env.EXAM_MONITOR_DEVICES=registry;delete process.env.EXAM_MONITOR_COLLECTOR;
  const routes=[];
  const fakeCollection={createIndex:async()=>{}};
  await require('../addon/index.ts').apply({db:{db:{collection:()=>fakeCollection}},Route:(...args)=>routes.push(args),effect:()=>{}});
  if(routes.length!==1||routes[0][0]!=='exam_monitor')throw Error('Route registration failed');
  console.log(JSON.stringify({hydroVersion:require('hydrooj/package.json').version,route:routes[0][1],result:'module load and route registration passed; database and HTTP not exercised'}));
}
main().catch(e=>{console.error(e);process.exitCode=1;});
