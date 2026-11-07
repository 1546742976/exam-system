import { describe, it, expect } from 'vitest';
import { Bucket } from '../addon/scheduler/bucket';
import { Scheduler, interval } from '../addon/scheduler/scheduler';
import { VeyonClient } from '../addon/transport/veyon';
import { mock } from './mock';
import { readdir } from 'node:fs/promises';
const flush = async () => { for(let i=0;i<8;i++) await Promise.resolve(); };
describe('scheduler', () => {
  it('strict interval and symmetric exact expectation', () => { for(let i=0;i<10000;i++) { const n=interval(); expect(n).toBeGreaterThan(30000); expect(n).toBeLessThan(90000); } expect((30001+89999)/2).toBe(60000); });
  it('bucket bounds burst and refill', () => { let t=0; const b=new Bucket(2,2,()=>t); expect(b.take(2)).toBe(true); expect(b.take(1)).toBe(false); t=499; expect(b.take(1)).toBe(false); t=500; expect(b.take(1)).toBe(true); });
  it('merges deadlines without resetting manual or running duplicate', async () => {
    let t=0; const jobs:any[]=[]; let done!:()=>void;
    const s=new Scheduler(async j=>{ jobs.push(j); await new Promise<void>(r=>done=r); },()=>t,()=>60000);
    s.add('a'); t=60000; s.tick(); await flush(); t=180000; s.tick(); s.manual('a');
    expect(s.snapshot()[0].pending).toMatchObject({planned:120000,merged:1,manual:true}); expect(jobs).toHaveLength(1);
    done(); await flush(); s.tick(); await flush(); expect(jobs).toHaveLength(2); expect(s.snapshot()[0].next).toBe(240000); s.stop(); done();
  });
  it('global concurrency never exceeds four and stop aborts', async () => {
    let t=0, running=0, peak=0; const s=new Scheduler(async(_, signal)=>{running++; peak=Math.max(peak,running); await new Promise<void>(r=>signal.addEventListener('abort',()=>r(),{once:true}));running--;},()=>t,()=>30001);
    for(let i=0;i<100;i++) s.add(String(i)); t=30001; s.tick(); await flush(); t+=1000; s.tick();await flush(); t+=1000;s.tick();await flush();expect(peak).toBe(4);s.stop();await flush();expect(running).toBe(0);
  });
});
describe('HTTPS capture', () => {
  it('unavailable framebuffer times out without saving an image',async()=>{const m=await mock();try{m.state.unavailable=true;const r=await new VeyonClient().capture(m.device,{directory:m.dir,timeoutMs:300});expect(r.error).toBe('TIMEOUT');expect(m.state.closed).toBe(1);expect(r.path).toBeUndefined();}finally{await m.close();}});
  it('rejects untrusted TLS and never sends authentication',async()=>{const m=await mock();const other=await mock();try{const r=await new VeyonClient().capture({...m.device,caFile:other.device.caFile},{directory:m.dir});expect(r.error).toBe('TLS_FAILED');expect(m.state.frames).toBe(0);}finally{await m.close();await other.close();}});
  it('two downloads share the same budget',async()=>{const m=await mock();try{m.state.size=50000;const c=new VeyonClient(new Bucket(100000,10000));const start=performance.now();const results=await Promise.all([c.capture(m.device,{directory:m.dir}),c.capture(m.device,{directory:m.dir})]);expect(results.every(r=>!r.error)).toBe(true);expect(performance.now()-start).toBeGreaterThanOrEqual(850);expect(m.state.closed).toBe(2);}finally{await m.close();}});
  it('authenticates, reads through shared limiter and closes', async () => { const m=await mock();try{ m.state.size=50000; const result=await new VeyonClient(new Bucket(100000,10000)).capture(m.device,{directory:m.dir});expect(result.error).toBeUndefined();expect(result.bytes).toBe(50005);expect(result.waitMs).toBeGreaterThan(250);expect(m.state.closed).toBe(1); }finally{await m.close();} });
  it('oversize removes partial files and closes', async()=>{const m=await mock();try{const r=await new VeyonClient().capture(m.device,{directory:m.dir,maxBytes:100});expect(r.error).toBe('IMAGE_TOO_LARGE');expect((await readdir(m.dir)).some(n=>n.endsWith('.part'))).toBe(false);expect(m.state.closed).toBe(1);}finally{await m.close();}});
  it('timeout closes connection',async()=>{const m=await mock();try{m.state.hang=true;const r=await new VeyonClient().capture(m.device,{directory:m.dir,timeoutMs:100});expect(r.error).toBe('TIMEOUT');expect(m.state.closed).toBe(1);}finally{await m.close();}});
  it('authentication failure produces no screenshot',async()=>{const m=await mock();try{m.state.authFail=true;const r=await new VeyonClient().capture(m.device,{directory:m.dir});expect(r.error).toBe('AUTH_FAILED');expect(m.state.frames).toBe(0);}finally{await m.close();}});
});
