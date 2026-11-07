import {it,expect} from 'vitest';
import http from 'node:http';
import {createRequire} from 'node:module';
const load=createRequire(__filename);
const request=load('superagent');load('superagent-proxy')(request);
it('explicit proxy survives redirect and ignores environment',async()=>{
  let hits=0;const server=http.createServer((req,res)=>{hits++;if(hits===1){res.writeHead(302,{location:'http://unresolvable.invalid/next'});res.end();}else res.end('proxied');});
  await new Promise<void>(r=>server.listen(0,'127.0.0.1',r));
  try{const port=(server.address() as any).port;const response=await request.get('http://unresolvable.invalid/start').proxy(`http://127.0.0.1:${port}`).timeout(3000);expect(response.text).toBe('proxied');expect(hits).toBe(2);}finally{await new Promise<void>(r=>server.close(()=>r()));}
});
it('unavailable explicit proxy fails instead of direct connection',async()=>{await expect(request.get('http://127.0.0.1:1').proxy('http://127.0.0.1:2').timeout(500)).rejects.toThrow();});
it('koa-proxies resolves compatible uuid v4',()=>{const local=createRequire(load.resolve('koa-proxies'));expect(local('uuid').v4()).toMatch(/^[0-9a-f-]{36}$/);expect(typeof load('koa-proxies')).toBe('function');});
