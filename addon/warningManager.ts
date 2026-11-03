export type Alert = { id: string; text: string };
export type UserInfo = { alerts: Alert[]; ip: string };

export class WarningManager {
    private userAlerts: Map<string, Alert[]> = new Map();
    private userBlocked: Map<string, Set<string>> = new Map();
    private lastActivity: Map<string, number> = new Map();
    private ipHistory: Map<string, string[]> = new Map(); // ip -> 最近用户名数组（最多 5 个）
    private userLastIp: Record<string, string> = {};
    private heartbeatIntervalMs: number;
    private inactiveThresholdMs: number;
    private timer?: NodeJS.Timeout;

    constructor(options?: { heartbeatIntervalMs?: number; inactiveThresholdMs?: number }) {
        this.heartbeatIntervalMs = options?.heartbeatIntervalMs ?? 5000; // 默认 5 秒检查一次
        this.inactiveThresholdMs = options?.inactiveThresholdMs ?? 30_000; // 默认 30 秒无活动视为失活
        this.startHeartbeat();
    }

    /**
     * 停止心跳计时器
     */
    stop() {
        if (this.timer) {
            clearInterval(this.timer);
            this.timer = undefined;
        }
    }

    private startHeartbeat() {
        if (this.timer) return;
        this.timer = setInterval(() => this.checkInactiveUsers(), this.heartbeatIntervalMs);
    }

    private checkInactiveUsers() {
        const now = Date.now();
        for (const [username, lastTs] of this.lastActivity.entries()) {
            if (now - lastTs > this.inactiveThresholdMs) {
                const arr = this.userAlerts.get(username) ?? [];
                const hasInactive = arr.some((a) => a.id === 'inactive');
                if (!hasInactive) {
                    const warnId = `inactive`;
                    const warn: Alert = { id: warnId, text: `No activity in last ${this.inactiveThresholdMs / 1000}s` };
                    arr.push(warn);
                    this.userAlerts.set(username, arr);
                }
            }
        }
    }

    /**
     * 更新警告：
     * - 清空该用户警告列表
     * - 更新心跳
     * - 检查 ip 历史（最近 5 次），如果该 ip 最近绑定的用户名与当前用户名不一致，则向该 ip 对应过的所有用户添加一条警告
     * - 将 alerts 中的告警添加到该用户警告列表
     */
    updateAlerts(username: string, ip: string, alerts: { id: string; text: string }[]) {
        this.userAlerts.set(username, []);
        this.lastActivity.set(username, Date.now());
        this.userLastIp[username] = ip;

        const hist = this.ipHistory.get(ip) ?? [];
        const newHist = hist.slice();
        newHist.push(username);
        while (newHist.length > 5) newHist.shift();
        this.ipHistory.set(ip, newHist);
        
        const uniqueUsers = Array.from(new Set(newHist));
        if (uniqueUsers.length > 1) {
            const warnId = `ip-conflict-${ip}`;
            const warn: Alert = { id: warnId, text: `IP ${ip} was used by ${JSON.stringify(uniqueUsers)}` };
            for (const u of uniqueUsers) {
                const arr = this.userAlerts.get(u) ?? [];
                arr.push(warn);
                this.userAlerts.set(u, arr);
            }
        }

        const userArr = this.userAlerts.get(username) ?? [];
        const seen = new Set(userArr.map((alert) => alert.id));
        for (const a of alerts) {
            if (a && typeof a.id === 'string' && typeof a.text === 'string' && !seen.has(a.id)) {
                userArr.push({ id: a.id, text: a.text });
                seen.add(a.id);
            }
        }
        this.userAlerts.set(username, userArr);
    }

    /**
     * 屏蔽一个警告 id
     */
    blockAlert(username: string, alertId: string) {
        const set = this.userBlocked.get(username) ?? new Set<string>();
        set.add(alertId);
        this.userBlocked.set(username, set);
        const alerts = this.userAlerts.get(username);
        if (alerts) this.userAlerts.set(username, alerts.filter((alert) => alert.id !== alertId));
    }

    /**
     * 清空所有屏蔽
     */
    clearBlocked() {
        this.userBlocked.clear();
    }

    /**
     * 获取警告列表，并过滤掉用户已屏蔽的警告
     */
    getInfo(): {sid: string, info: UserInfo}[] {
        const res: {sid: string, info: UserInfo}[] = [];
        for (const [username, alerts] of this.userAlerts.entries()) {
            const blocked = this.userBlocked.get(username) ?? new Set<string>();
            const filteredAlerts = alerts.filter((a) => !blocked.has(a.id));
            res.push({
                sid: username,
                info: {
                    alerts: filteredAlerts,
                    ip: this.userLastIp[username] || '',
                },
            });
        }
        return res;
    }
}

export default WarningManager;
