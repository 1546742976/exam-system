const fs=require('node:fs/promises');const path=require('node:path');const os=require('node:os');const assert=require('node:assert/strict');
async function main(){
  const root=path.resolve('data/hydro-integration');await fs.mkdir(root,{recursive:true});os.homedir=()=>root;
  process.env.MONGOMS_DOWNLOAD_DIR=path.join(root,'mongodb-binaries');
  const {MongoMemoryServer}=require('mongodb-memory-server');const {MongoClient,ObjectId}=require('mongodb');
  const server=await MongoMemoryServer.create({binary:{version:'7.0.14'}});const mongo=await MongoClient.connect(server.getUri());
  try {
    const db=mongo.db('monitor-test');require('@hydrooj/register');require('hydrooj/src/init');const get=global.app.get.bind(global.app);global.app.get=name=>name==='db'?{collection:n=>db.collection(n)}:get(name);
    const key=path.join(root,'device.pem');await fs.writeFile(key,'TEST-ONLY');const registry=path.join(root,'devices.json');await fs.writeFile(registry,JSON.stringify([{id:'a',origin:'https://localhost:11080',caFile:key,keyFile:key,keyName:'test'}]));
    process.env.EXAM_MONITOR_DEVICES=registry;process.env.EXAM_MONITOR_DATA=path.join(root,'images');delete process.env.EXAM_MONITOR_COLLECTOR;
    let Handler;await require('../addon/index.ts').apply({db:{db},Route:(name,route,handler)=>Handler=handler,effect:()=>{}});
    const tid=new ObjectId();await db.collection('document').insertOne({domainId:'test',docType:30,docId:tid,owner:1,endAt:new Date(Date.now()+60000)});
    async function handler(domain='test',allowed=true){const context={session:{},HydroContext:{args:{domainId:domain,tid},request:{query:{},body:{}},response:{},UiContext:{}}};const h=new Handler(context,{});h.user={_id:1,hasPerm:()=>allowed};await h.prepare({domainId:domain,tid});return h;}
    const h=await handler();h.request.body={action:'import',mappings:[{studentId:2,deviceId:'a'}]};await h.post();h.request.body={action:'start'};await h.post();assert.equal((await db.collection('exam.monitor.sessions').findOne({})).active,true);
    h.request.body={action:'capture',studentId:2};await h.post();assert.equal((await db.collection('exam.monitor.sessions').findOne({})).manual.length,1);
    await assert.rejects(()=>db.collection('exam.monitor.sessions').insertOne({_id:'conflict',active:true,mappings:[{studentId:3,deviceId:'a'}]}));
    await assert.rejects(()=>handler('other'));await assert.rejects(()=>handler('test',false));
    const image=await db.collection('exam.monitor.results').insertOne({examId:'other/'+tid,studentId:2,path:'not-accessible',completedAt:Date.now()});h.request.query={image:image.insertedId.toHexString()};await assert.rejects(()=>h.get());
    const ownPath=path.join(root,'own.jpg');await fs.writeFile(ownPath,Buffer.from('ffd8ffffd9','hex'));const own=await db.collection('exam.monitor.results').insertOne({examId:'test/'+tid,studentId:2,path:ownPath,completedAt:Date.now()});h.request.query={history:'2'};await h.get();assert.equal(h.response.body.rows.length,1);assert.equal(h.response.body.rows[0].path,undefined);h.request.query={image:own.insertedId.toHexString()};await h.get();let imageBytes=0;for await(const chunk of h.response.body)imageBytes+=chunk.length;assert.equal(imageBytes,5);
    h.request.query={status:'1'};await h.get();assert.equal(h.response.body.active,true);
    h.request.body={action:'stop'};await h.post();assert.equal((await db.collection('exam.monitor.sessions').findOne({})).active,false);
    const {Store}=require('../addon/storage/store.ts');const store=new Store(db,process.env.EXAM_MONITOR_DATA);const expired='test/expired';await store.exams.insertOne({_id:expired,domainId:'test',contestId:'expired',endAt:new Date(0),active:false,mappings:[]});const folder=store.directory(expired,2);await fs.mkdir(folder,{recursive:true});await fs.writeFile(path.join(folder,'a.jpg'),'test');await store.clean();assert.equal(await store.exams.findOne({_id:expired}),null);await assert.rejects(()=>fs.access(folder));
    const report={hydroVersion:'5.1.0',mongoVersion:'7.0.14',passed:['module load','route registration','mapping import','start','manual request','device uniqueness across active exams','cross-domain denial','permission denial','cross-exam image denial','own image history','own image stream','status','stop','expired file and index cleanup'],boundary:'real HydroOJ handler methods and real MongoDB; no full HTTP server, browser rendering, or real Veyon'};await fs.mkdir('reports',{recursive:true});await fs.writeFile('reports/hydro-integration.json',JSON.stringify(report,null,2)+'\n');console.log(report);
  } finally {await mongo.close();await server.stop();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
