import type { Db } from 'mongodb';
import { rm } from 'node:fs/promises';
import path from 'node:path';
export interface Mapping { studentId: number; deviceId: string }
export interface Exam { _id: string; domainId: string; contestId: string; endAt: Date; active: boolean; mappings: Mapping[]; manual?: string[] }
export class Store {
  readonly exams; readonly results;
  constructor(db: Db, readonly root: string) { this.exams = db.collection<Exam>('exam.monitor.sessions'); this.results = db.collection<any>('exam.monitor.results'); }
  async init() { await this.results.createIndex({ examId: 1, completedAt: -1 }); await this.exams.createIndex({ active: 1 }); await this.exams.createIndex({'mappings.deviceId':1},{unique:true,partialFilterExpression:{active:true}}); }
  directory(examId: string, studentId: number) { return path.join(this.root, Buffer.from(examId).toString('hex'), String(studentId)); }
  async clean(now = Date.now()) {
    const expired = await this.exams.find({ endAt: { $lt: new Date(now - 7 * 86400000) } }).limit(20).toArray();
    for (const exam of expired) { await rm(path.join(this.root, Buffer.from(exam._id).toString('hex')), { recursive: true, force: true }); await this.results.deleteMany({ examId: exam._id }); await this.exams.deleteOne({ _id: exam._id }); }
  }
}
