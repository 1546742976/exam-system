import https from 'node:https';
import { IncomingMessage } from 'node:http';
import { readFile, mkdir, open, rename, rm } from 'node:fs/promises';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { setTimeout as sleep } from 'node:timers/promises';
import { Bucket } from '../scheduler/bucket';
import { defaults, Device, CaptureOptions, CaptureResult } from '../config';

export class VeyonClient {
  constructor(private budget = new Bucket(defaults.bytesPerSecond, defaults.byteBurst)) {}
  async capture(device: Device, options: CaptureOptions, external = new AbortController().signal): Promise<CaptureResult> {
    const result: CaptureResult = { bytes: 0, startedAt: Date.now(), completedAt: 0, waitMs: 0 };
    const signal = AbortSignal.any([external, AbortSignal.timeout(options.timeoutMs ?? defaults.timeoutMs)]);
    const origin = new URL(device.origin);
    if (origin.protocol !== 'https:' || origin.username || origin.password || origin.pathname !== '/' || origin.search || origin.hash) throw new Error('Invalid HTTPS origin');
    let uid: string | undefined; let partial: string | undefined;
    const ca = await readFile(device.caFile);
    const request = (method: string, route: string, body: unknown, requestSignal: AbortSignal) => new Promise<IncomingMessage>((resolve, reject) => {
      const data = body === undefined ? undefined : JSON.stringify(body);
      const req = https.request(new URL(route, origin), { method, ca, rejectUnauthorized: true, signal: requestSignal, headers: { ...(uid ? { 'Connection-Uid': uid } : {}), ...(data ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) } : {}) } }, resolve);
      req.on('error', reject); req.end(data);
    });
    const json = async (method: string, body: unknown, requestSignal: AbortSignal) => {
      const res = await request(method, '/api/v1/authentication/localhost', body, requestSignal);
      let bytes = 0; const parts: Buffer[] = [];
      for await (const chunk of res) { bytes += chunk.length; if (bytes > 16384) { res.destroy(); throw new Error('AUTH_RESPONSE_TOO_LARGE'); } parts.push(chunk); }
      if (res.statusCode !== 200) throw new Error('AUTH_FAILED');
      return JSON.parse(Buffer.concat(parts).toString());
    };
    try {
      const keydata = await readFile(device.keyFile, 'utf8'); signal.throwIfAborted();
      const auth = await json('POST', { method: '0c69b301-81b4-42d6-8fae-128cdd113314', credentials: { keyname: device.keyName, keydata } }, signal);
      uid = auth['connection-uid']; if (typeof uid !== 'string' || !uid || /[\r\n]/.test(uid)) throw new Error('AUTH_FAILED');
      const width = options.manual ? 1920 : 1280; const quality = options.manual ? 65 : 55;
      let res: IncomingMessage;
      while (true) {
        res = await request('GET', `/api/v1/framebuffer?format=jpeg&width=${width}&quality=${quality}`, undefined, signal);
        if (res.statusCode === 200) break;
        const code = res.statusCode; res.destroy();
        if (code !== 503) throw new Error('FRAME_UNAVAILABLE');
        await sleep(200, undefined, { signal });
      }
      res.pause();
      // Keep errors observed even while waiting for a shared bandwidth permit.
      let streamError: Error | undefined;
      res.on('error', e => { streamError = e; });
      await mkdir(options.directory, { recursive: true });
      const filename = path.join(options.directory, `${randomUUID()}.jpg`); partial = `${filename}.part`;
      const file = await open(partial, 'wx');
      let prefix = Buffer.alloc(0); let tail = Buffer.alloc(0);
      try {
        while (true) {
          signal.throwIfAborted(); if (streamError) throw streamError;
          if (!res.readableLength) {
            res.read(0);
            if (res.readableEnded) break;
            if (res.destroyed) throw new Error('FRAME_UNAVAILABLE');
            await sleep(2, undefined, { signal }); continue;
          }
          const n = Math.min(res.readableLength, this.budget.capacity);
          const before = performance.now(); await this.budget.acquire(n, signal); result.waitMs += performance.now() - before;
          signal.throwIfAborted(); const chunk = res.read(n) as Buffer | null; if (!chunk) continue;
          result.bytes += chunk.length;
          if (result.bytes > (options.maxBytes ?? defaults.maxBytes)) throw new Error('IMAGE_TOO_LARGE');
          if (prefix.length < 3) prefix = Buffer.concat([prefix, chunk]).subarray(0, 3);
          tail = Buffer.concat([tail, chunk.subarray(-2)]).subarray(-2);
          let offset = 0;
          while (offset < chunk.length) { signal.throwIfAborted(); const { bytesWritten } = await file.write(chunk, offset, chunk.length - offset); offset += bytesWritten; }
        }
      } finally { res.destroy(); await file.close(); }
      if (prefix.toString('hex') !== 'ffd8ff' || tail.toString('hex') !== 'ffd9') throw new Error('INVALID_JPEG');
      signal.throwIfAborted(); await rename(partial, filename); partial = undefined; result.path = filename;
    } catch (error: any) {
      result.error = signal.aborted ? (external.aborted ? 'CANCELLED' : 'TIMEOUT') : /CERT|SELF_SIGNED|ALTNAME|VERIFY/.test(error.code ?? '') ? 'TLS_FAILED' : ['ECONNREFUSED','EHOSTUNREACH','ENETUNREACH','ENOTFOUND'].includes(error.code) ? 'UNREACHABLE' : /^[A-Z_]+$/.test(error.message ?? '') ? error.message : 'CAPTURE_FAILED';
    } finally {
      if (partial) await rm(partial, { force: true });
      if (uid) { try { await json('DELETE', undefined, AbortSignal.timeout(1000)); } catch { result.cleanupError = true; } }
      result.completedAt = Date.now();
    }
    return result;
  }
}
