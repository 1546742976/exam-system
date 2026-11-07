import { setTimeout as sleep } from 'node:timers/promises';
export class Bucket {
  private tokens: number;
  private last: number;
  constructor(readonly rate: number, readonly capacity: number, private now = () => performance.now()) {
    if (!(rate > 0 && capacity > 0)) throw new Error('Invalid bucket');
    this.tokens = capacity; this.last = now();
  }
  private refill() { const t = this.now(); this.tokens = Math.min(this.capacity, this.tokens + Math.max(0, t - this.last) * this.rate / 1000); this.last = t; }
  take(n: number) { this.refill(); if (n <= 0 || n > this.capacity) throw new Error('Invalid token amount'); if (this.tokens < n) return false; this.tokens -= n; return true; }
  delay(n: number) { this.refill(); return Math.max(1, Math.ceil((n - this.tokens) * 1000 / this.rate)); }
  async acquire(n: number, signal: AbortSignal) { while (true) { signal.throwIfAborted(); if (this.take(n)) return; await sleep(this.delay(n), undefined, { signal }); } }
}
