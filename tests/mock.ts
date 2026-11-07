import https from 'node:https';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import selfsigned from 'selfsigned';
export async function mock() {
  const dir = await mkdtemp(path.join(tmpdir(), 'veyon-test-'));
  const cert = selfsigned.generate([{ name: 'commonName', value: 'localhost' }], { days: 1, keySize: 2048, extensions: [{ name: 'subjectAltName', altNames: [{ type: 2, value: 'localhost' }] }] });
  await writeFile(path.join(dir, 'ca.pem'), cert.cert); await writeFile(path.join(dir, 'key.pem'), 'TEST-ONLY');
  const state = { closed: 0, frames: 0, authFail: false, unavailable: false, size: 10000, hang: false };
  const server = https.createServer({ key: cert.private, cert: cert.cert }, (req, res) => {
    req.resume();
    if (req.method === 'POST') { res.writeHead(state.authFail ? 401 : 200); res.end(JSON.stringify({ 'connection-uid': 'test-connection' })); }
    else if (req.method === 'DELETE') { state.closed++; res.end('{}'); }
    else if (req.headers['connection-uid'] !== 'test-connection') { res.writeHead(401); res.end(); }
    else { state.frames++; if (state.hang) return; if (state.unavailable) { res.writeHead(503); res.end('{}'); return; } res.setHeader('Content-Type','image/jpeg'); res.end(Buffer.concat([Buffer.from('ffd8ff','hex'), Buffer.alloc(state.size), Buffer.from('ffd9','hex')])); }
  });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = (server.address() as {port:number}).port;
  // Use localhost for TLS identity; listen on IPv4 and resolve explicitly through URL host.
  await new Promise<void>(resolve => server.close(() => resolve()));
  await new Promise<void>(resolve => server.listen(port, resolve));
  return { dir, state, device: { id: 'test', origin: `https://localhost:${port}`, caFile: path.join(dir,'ca.pem'), keyFile:path.join(dir,'key.pem'), keyName:'test' }, close: async () => { server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); await rm(dir, { recursive:true, force:true }); } };
}
