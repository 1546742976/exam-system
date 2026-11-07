import { randomInt } from 'node:crypto';
import { Bucket } from './bucket';
export const interval = () => randomInt(30001, 90000);
export interface Job { id: string; planned: number; manual: boolean; merged: number }
interface State { next: number; pending?: Job; running: boolean; controller?: AbortController }
export class Scheduler {
  private states = new Map<string, State>();
  private active = 0;
  private bucket: Bucket;
  constructor(private run: (job: Job, signal: AbortSignal) => Promise<void>, private now = () => performance.now(), private random = interval, private onError: (e: unknown) => void = () => {}) { this.bucket = new Bucket(2, 2, now); }
  add(id: string) { if (!this.states.has(id)) this.states.set(id, { next: this.now() + this.random(), running: false }); }
  remove(id: string) { this.states.get(id)?.controller?.abort(); this.states.delete(id); }
  stop() { for (const id of this.states.keys()) this.remove(id); }
  manual(id: string) { const s = this.states.get(id); if (!s) throw new Error('Device not scheduled'); if (s.pending) s.pending.manual = true; else s.pending = { id, planned: this.now(), manual: true, merged: 0 }; }
  snapshot() { return [...this.states].map(([id, s]) => ({ id, next: s.next, running: s.running, pending: s.pending && { ...s.pending } })); }
  tick() {
    const now = this.now();
    for (const [id, s] of this.states) {
      while (s.next <= now) {
        if (s.pending) s.pending.merged++;
        else s.pending = { id, planned: s.next, manual: false, merged: 0 };
        s.next += this.random();
      }
    }
    const ready = [...this.states.values()].filter(s => s.pending && !s.running).sort((a,b) => a.pending!.planned - b.pending!.planned);
    for (const s of ready) {
      if (this.active >= 4 || !this.bucket.take(1)) break;
      const job = s.pending!; s.pending = undefined; s.running = true; this.active++;
      s.controller = new AbortController();
      Promise.resolve().then(() => this.run(job, s.controller!.signal)).catch(this.onError).finally(() => { s.running = false; this.active--; });
    }
  }
}
