"""In-process security regressions; no production network/database writes."""
import asyncio
import hashlib
import hmac
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'server'), str(ROOT/'client')]
import httpx
import gateway
from db import Store
from common import _check_claims
from local_server import _range_response

class Regression(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cfg = gateway.ServerConfig()
        self.cfg.session_secret = 'test-session-secret'*3
        self.cfg.fingerprint_enforce = False
        self.store = Store(str(Path(self.temp.name)/'test.db'))
        gateway.UPLOAD_DIR = Path(self.temp.name)/'uploads'
        gateway.SESSIONS.clear()
        self.app = gateway.build_app(self.cfg, self.store)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://test',trust_env=False)
    async def asyncTearDown(self):
        await self.client.aclose()
        for handler in self.app.router.on_shutdown:
            await handler()
        self.store._conn().close()
        self.temp.cleanup()
    def login(self, name='student', role='student'):
        self.client.cookies.set(self.cfg.cookie_name,gateway.make_session(self.cfg,{'sub':name,'role':role}))
    async def test_anonymous_alert_cannot_create_student(self):
        r=await self.client.post('/exam/alert',json={'uname':'victim','alerts':[]})
        self.assertEqual(r.status_code,401)
        self.assertEqual(self.store.list_students(),[])
    async def test_wrong_role(self):
        self.login(role='admin')
        self.assertEqual((await self.client.get('/exam')).status_code,401)
    async def test_malformed_alert(self):
        self.login()
        for payload in ([],{'hb_seq':'bad'},{'alerts':[1]}):
            self.assertEqual((await self.client.post('/exam/alert',json=payload)).status_code,400)
    async def test_restart_does_not_accept_shared_key(self):
        self.login()
        self.assertEqual((await self.client.post('/exam/upload?seq=1',content=b'x')).status_code,401)
    async def test_upload_path_and_signature(self):
        name='../../outside'; self.login(name)
        gateway.SESSIONS[name]={'key':'secret'}
        body=b'frame'; sig=hmac.new(b'secret',name.encode()+b'|1|'+body,hashlib.sha256).hexdigest()
        r=await self.client.post('/exam/upload?seq=1&kind=frame&name=a.jpg',content=body,headers={'x-exam-sig':sig})
        self.assertEqual(r.status_code,200)
        repeat=await self.client.post('/exam/upload?seq=1&kind=frame&name=a.jpg',content=body,headers={'x-exam-sig':sig})
        self.assertEqual(repeat.status_code,200)
        self.assertEqual(self.store.upload_stats()[0]['n'],1)
        files=list(gateway.UPLOAD_DIR.rglob('*.jpg')); self.assertEqual(len(files),1)
        self.assertEqual(files[0].parent.name,hashlib.sha256(name.encode()).hexdigest())
        r=await self.client.post('/exam/upload?seq=2',content=body,headers={'x-exam-sig':'bad'})
        self.assertEqual(r.status_code,403)
    async def test_stream_limit(self):
        self.login(); gateway.SESSIONS['student']={'key':'secret'}; self.cfg.upload_max_mb=0
        self.assertEqual((await self.client.post('/exam/upload?seq=1',content=b'x')).status_code,413)
    async def test_fingerprint_enforcement(self):
        self.login(); self.cfg.fingerprint_enforce=True
        self.cfg.allowlist_path=str(Path(self.temp.name)/'missing.json')
        self.assertEqual((await self.client.get('/exam/session?fp=unknown')).status_code,403)
        self.assertEqual((await self.client.get('/exam')).status_code,403)
    async def test_teacher_claims(self):
        import time
        for claims in ({'sub':'teacher'},{'sub':'student','exp':time.time()+60},{'sub':'teacher','exp':time.time()+60}):
            with self.assertRaises(ValueError): _check_claims(claims,'ckyf-exam')
        _check_claims({'sub':'teacher','exp':time.time()+60,'aud':'ckyf-exam'},'ckyf-exam')
    async def test_upload_queue_bound(self):
        import threading
        from uploader import Uploader
        from common import Config
        cfg=Config(log_dir=str(Path(self.temp.name)/'logs'))
        uploader=Uploader(cfg); uploader.enabled=True
        gate=threading.Event()
        for _ in range(9): uploader._submit(lambda: gate.wait(2))
        self.assertEqual(uploader.failed,1)
        gate.set(); uploader.close()
        for handler in list(uploader.log.handlers):
            handler.close()
            uploader.log.removeHandler(handler)

    async def test_110_concurrent_students(self):
        import time
        async def student(i):
            name='load-%d'%i
            token=gateway.make_session(self.cfg,{'sub':name,'role':'student'})
            headers={'cookie':self.cfg.cookie_name+'='+token}
            r=await self.client.post('/exam/alert',headers=headers,json={'alerts':[],'hb_seq':1})
            self.assertEqual(r.status_code,200)
            gateway.SESSIONS[name]={'key':'key'}
            body=b'jpeg-placeholder'*4096
            signature=hmac.new(b'key',name.encode()+b'|1|'+body,hashlib.sha256).hexdigest()
            headers['x-exam-sig']=signature
            r=await self.client.post('/exam/upload?seq=1&kind=frame&name=frame.jpg',headers=headers,content=body)
            self.assertEqual(r.status_code,200)
        started=time.perf_counter()
        await asyncio.gather(*(student(i) for i in range(110)))
        self.assertEqual(len(self.store.list_students()),110)
        self.assertEqual(len(self.store.upload_stats()),110)
        print('110 simulated students, heartbeat+upload: %.3fs (ASGI only)'%(time.perf_counter()-started))

    async def test_suffix_range(self):
        p=Path(self.temp.name)/'video.ts'; p.write_bytes(b'0123456789')
        r=_range_response(p,SimpleNamespace(headers={'range':'bytes=-3'}),SimpleNamespace(warning=lambda *a:None))
        self.assertEqual(r.status_code,206); self.assertEqual(r.headers['content-range'],'bytes 7-9/10')
        chunks=[chunk async for chunk in r.body_iterator]; self.assertEqual(b''.join(chunks),b'789')

if __name__=='__main__': unittest.main(verbosity=2)
