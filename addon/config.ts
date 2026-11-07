export const defaults = Object.freeze({ rate: 2, burst: 2, concurrency: 4, bytesPerSecond: 250000, byteBurst: 32768, timeoutMs: 10000, maxBytes: 2 * 1024 * 1024, retentionDays: 7 });
export interface Device { id: string; origin: string; caFile: string; keyFile: string; keyName: string }
export interface CaptureOptions { directory: string; manual?: boolean; timeoutMs?: number; maxBytes?: number }
export interface CaptureResult { path?: string; bytes: number; startedAt: number; completedAt: number; waitMs: number; error?: string; cleanupError?: boolean }
