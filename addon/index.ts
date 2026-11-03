import {
  Context, ForbiddenError, Handler, param, PERM, Schema, Types,
} from 'hydrooj';
import { createPrivateKey, sign as signData, type KeyObject } from 'node:crypto';
import { readFileSync } from 'node:fs';
import WarningManager from './warningManager';

let privKey: KeyObject | null = null;
let privateKeyPath = "";
let warningManager: WarningManager;
let contestId: string;
let clientDownloadUrl: string;

const MAX_ALERT_COUNT = 100;
const MAX_ALERT_ID_LENGTH = 128;
const MAX_ALERT_TEXT_LENGTH = 1000;

function encodeJwtPart(value: object) {
    return Buffer.from(JSON.stringify(value)).toString('base64url');
}

function loadPrivateKey(): KeyObject {
    if (privKey) return privKey;
    const keyPath = String(privateKeyPath || process.env.CKYF_EXAM_PRIVATE_KEY_FILE || '').trim();
    if (!keyPath) {
        throw new Error('缺少考试签名私钥：请设置插件配置 exam.privateKeyFile 或环境变量 CKYF_EXAM_PRIVATE_KEY_FILE');
    }
    privKey = createPrivateKey(readFileSync(keyPath, 'utf8'));
    return privKey;
}


function createExamToken(uname: string) {
    const now = Math.floor(Date.now() / 1000);
    const header = encodeJwtPart({ alg: 'RS256' });
    const payload = encodeJwtPart({
        uname,
        iss: 'ckyf:exam_system',
        exp: now + 5 * 60 * 60,
    });
    const signingInput = `${header}.${payload}`;
    const signature = signData('RSA-SHA256', Buffer.from(signingInput), loadPrivateKey()).toString('base64url');
    return `${signingInput}.${signature}`;
}

declare global {
  interface Window {
    _ncec?: boolean;
  }
}

class ExamBackendHandler extends Handler {
    async get() {
        this.response.template = 'exam_main.html';
    }
}

class ExamTokenHandler extends Handler {
    async get() {
        const uname = this.user.uname;
        const token = createExamToken(uname);
        this.response.body = { token };
    }
}

class ExamAlertHandler extends Handler {
    @param('alerts', Types.Content)
    async post({alerts}: {alerts: string}) {
        let parsed: unknown;
        try {
            parsed = JSON.parse(alerts);
        } catch {
            this.response.status = 400;
            this.response.body = { success: false, error: 'alerts must be valid JSON' };
            return;
        }
        if (!Array.isArray(parsed) || parsed.length > MAX_ALERT_COUNT) {
            this.response.status = 400;
            this.response.body = { success: false, error: `alerts must be an array of at most ${MAX_ALERT_COUNT} items` };
            return;
        }
        const normalized = parsed.filter((item): item is { id: string; text: string } => (
            item !== null
            && typeof item === 'object'
            && typeof item.id === 'string'
            && item.id.length > 0
            && item.id.length <= MAX_ALERT_ID_LENGTH
            && typeof item.text === 'string'
            && item.text.length > 0
            && item.text.length <= MAX_ALERT_TEXT_LENGTH
        ));
        if (normalized.length !== parsed.length) {
            this.response.status = 400;
            this.response.body = { success: false, error: 'one or more alerts are invalid' };
            return;
        }
        warningManager.updateAlerts(this.user.uname, this.request.ip, normalized);
        this.response.body = { success: true };
    }
}

class ExamGetAlertsHandler extends Handler {
    async get() {
        this.response.body = warningManager.getInfo();
    }
}

class ExamBlockAlertHandler extends Handler {
    async get() {
        const alertId = this.request.query.alert_id;
        const username = this.request.query.username;
        if (typeof alertId === 'string' && typeof username === 'string' && alertId && username) {
            warningManager.blockAlert(username, alertId);
        }
        this.response.body = { success: typeof alertId === 'string' && typeof username === 'string' && !!alertId && !!username };
    }
}

class ExamClearBlockedHandler extends Handler {
    async get() {
        warningManager.clearBlocked();
        this.response.body = { success: true };
    }
}

class ExamHomeHandler extends Handler {
    async get() {
        const ua = this.request.headers['user-agent'] || '';
        if (ua.includes('exam-client') || this.user.uname.endsWith('_ncec')) {
            if (contestId) {
                this.response.redirect = this.url('contest_detail', { tid: contestId });
            } else {
                this.response.redirect = this.url('contest_main');
            }
        } else {
            this.response.redirect = clientDownloadUrl;
        }
    }
}

export const name = 'Exam System';
export const Config = Schema.object({
    exam: Schema.object({
        contestId: Schema.string().default(''),
        clientDownloadUrl: Schema.string().default('/exam/ckyf_exam.zip'),
        privateKeyFile: Schema.string().default(''),
    }).description('Exam settings').experimental(),
});

export async function apply(ctx: Context, config: ReturnType<typeof Config>) {
    // Hydro loads addons after the server service and installs Route on this
    // context. Register routes before optional configuration/manager setup so
    // a stale configuration cannot make the entire HTTP surface disappear.
    ctx.Route('exam_backend', '/exam/backend', ExamBackendHandler, PERM.PERM_EDIT_CONTEST);
    ctx.Route('exam_token', '/exam/token', ExamTokenHandler, PERM.PERM_EDIT_CONTEST);
    ctx.Route('exam_get_alerts', '/exam/get_alerts', ExamGetAlertsHandler, PERM.PERM_EDIT_CONTEST);
    ctx.Route('exam_block_alert', '/exam/block_alert', ExamBlockAlertHandler, PERM.PERM_EDIT_CONTEST);
    ctx.Route('exam_clear_blocked', '/exam/clear_blocked', ExamClearBlockedHandler, PERM.PERM_EDIT_CONTEST);
    ctx.Route('exam_alert', '/exam/alert', ExamAlertHandler);
    ctx.Route('exam_home', '/exam', ExamHomeHandler);

    privateKeyPath = String(
        config?.exam?.privateKeyFile
        || process.env.CKYF_EXAM_PRIVATE_KEY_FILE
        || '',
    ).trim();
    if (!privateKeyPath) {
        console.warn('Exam addon: privateKeyFile 未配置，签发考试令牌会失败。');
    }
    const manager = new WarningManager();
    warningManager = manager;
    ctx.effect(() => () => manager.stop());
    contestId = config?.exam?.contestId || '';
    clientDownloadUrl = config?.exam?.clientDownloadUrl || '/exam/ckyf_exam.zip';

    ctx.on('handler/after/ContestList', async (h) => {
        if (h.context.HydroContext.user.uname.endsWith('_ncec')) {
            console.log(`${h.context.HydroContext.user.uname} skip exam test.`);
            return;
        }
        const ua = h.request.headers['user-agent'] || '';
        if (ua.includes('exam-client')) {
            console.log('Exam client detected, skipping exam user switch.');
            return;
        }
        if (h.context.HydroContext.user.hasPerm(PERM.PERM_EDIT_CONTEST)) {
            console.log('User has edit contest permission, skipping exam user switch.');
            return;
        }
        throw new ForbiddenError('Access to contest list is forbidden during exam.');
    });
    ctx.on('handler/after/ContestDetail', async (h) => {
        if (h.context.HydroContext.user.uname.endsWith('_ncec')) {
            console.log(`${h.context.HydroContext.user.uname} skip exam test.`);
            return;
        }
        const ua = h.request.headers['user-agent'] || '';
        if (ua.includes('exam-client')) {
            console.log('Exam client detected, skipping exam user switch.');
            return;
        }
        if (h.context.HydroContext.user.hasPerm(PERM.PERM_EDIT_CONTEST)) {
            console.log('User has edit contest permission, skipping exam user switch.');
            return;
        }
        throw new ForbiddenError('Access to contest detail without exam client is forbidden during exam.');
    });
    // 安全说明：绝不能在 UserLogin 后把普通用户切换成 Guest 并授予 PERM_ALL。
    // 普通浏览器即使登录 HydroOJ，也只会保留自己的原账号权限；比赛页由上面的
    // ContestList / ContestDetail 钩子和考试网关共同拦截。

}
