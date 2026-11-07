import { Scheduler } from '../addon/scheduler/scheduler';
import { Bucket } from '../addon/scheduler/bucket';
import { mkdir, writeFile } from 'node:fs/promises';
async function main() {
  let now=0, seed=17, peak=0, bytes=0, completed=0, failed=0, merged=0, maxPending=0;
  const delays:number[]=[];
  const active:{remaining:number;start:number;finish:()=>void;error:boolean}[]=[];
  const budget=new Bucket(250000,32768,()=>now);
  const random=()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return 30001+Math.floor(seed/4294967296*59999);};
  const scheduler=new Scheduler(async job=>{
    const index=Number(job.id);delays.push(now-job.planned);merged+=job.merged;
    await new Promise<void>(resolve=>{active.push({remaining:index%17===0?500000:102400,start:now,finish:resolve,error:index%23===0});peak=Math.max(peak,active.length);});
  },()=>now,random);
  for(let i=0;i<100;i++)scheduler.add(String(i));
  for(now=0;now<=10800000;now+=50){
    if(now===180000)for(let i=0;i<100;i++)scheduler.manual(String(i));
    scheduler.tick();await Promise.resolve();
    maxPending=Math.max(maxPending,scheduler.snapshot().filter(s=>s.pending).length);
    for(const task of [...active]){
      if(now-task.start<200)continue;
      const n=Math.min(2048,task.remaining);
      if(!task.error && budget.take(n)){bytes+=n;task.remaining-=n;}
      if(task.remaining===0||now-task.start>=10000){active.splice(active.indexOf(task),1);if(task.remaining)failed++;else completed++;task.finish();}
    }
    for(let i=0;i<4;i++)await Promise.resolve();
  }
  scheduler.stop();for(const task of active)task.finish();
  delays.sort((a,b)=>a-b);
  const report={kind:'deterministic scheduler and bandwidth model, not real Veyon or wireless benchmark',devices:100,durationHours:3,completed,failed,bytes,averageMbit:bytes*8/10800/1e6,peakConcurrency:peak,maxPending,merged,delayP95Ms:delays[Math.floor(delays.length*.95)],delayMaxMs:delays.at(-1),simulationProcessMemory:process.memoryUsage()};
  await mkdir('reports',{recursive:true});await writeFile('reports/simulation.json',JSON.stringify(report,null,2)+'\n');console.log(report);
  if(peak>4||maxPending>100)throw new Error('Bounds exceeded');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
