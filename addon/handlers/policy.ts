export function mayManage(user: { id: number; edit: boolean; editSelf: boolean }, contest: { owner: number }) { return user.edit || (user.id === contest.owner && user.editSelf); }
export function validMappings(value: unknown, registered: Set<string>) {
  if (!Array.isArray(value) || !value.length || value.length > 1000) throw new Error('Invalid mappings');
  const students = new Set<number>(); const devices = new Set<string>();
  for (const row of value) {
    if (!row || !Number.isSafeInteger(row.studentId) || row.studentId <= 0 || !registered.has(row.deviceId) || students.has(row.studentId) || devices.has(row.deviceId)) throw new Error('Invalid or duplicate mapping');
    students.add(row.studentId); devices.add(row.deviceId);
  }
  return value.map(r => ({ studentId: r.studentId as number, deviceId: r.deviceId as string }));
}
