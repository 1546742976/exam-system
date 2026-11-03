import { addPage, AutoloadPage } from '@hydrooj/ui-default';
import { retryRequest } from './retry';

declare global {
  interface Window {
    _ncec?: boolean;
  }
}

let connectCacheCount = 0;

const testConnect = async () => {
    try {
        await fetch('https://img.alicdn.com/tfs/TB1..favicon.ico'); 
        connectCacheCount = 4;
        return true;
    } catch(e) {
        connectCacheCount--;
        if (connectCacheCount <= 0) {
            connectCacheCount = 0;
        } else {
            return true;
        }
        return false;
    }
}

async function sendAlert(alerts: { id: string; text: string }[]) {
    await fetch('/exam/alert', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ alerts: JSON.stringify(alerts) })
    })
}

addPage(new AutoloadPage('exam_no_popup', () => {
    console.log('Exam no popup script loaded.', window.UserContext.uname);
    if (window.UserContext.uname.endsWith('_ncec')) {
        setInterval(async () => {
            const connected = await testConnect();
            if (connected) {
                await retryRequest(() => sendAlert([{ id: 'net-reachable', text: 'Internet reachable: Ali CDN' }]), 10, 5000);
            } else {
                await sendAlert([]);
            }
        }, 10000);
    } else {
        document.addEventListener('click', function(e) {
            const target = e.target;
            if (target instanceof HTMLAnchorElement && target.target === '_blank') {
                e.preventDefault();
                window.location.href = target.href;
            }
        });
    }
}));
