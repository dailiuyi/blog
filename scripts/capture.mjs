import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs';

async function fetchJson(url) {
  return new Promise((resolve, reject) => {
    http.get(url, (res) => {
      let data = '';
      res.on('data', (chunk) => (data += chunk));
      res.on('end', () => resolve(JSON.parse(data)));
    }).on('error', reject);
  });
}

async function capture() {
  const edgePath = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
  const tempProfile = 'C:\\Users\\emla\\AppData\\Local\\Temp\\edge_cdp_profile_' + Date.now();
  
  const edge = spawn(edgePath, [
    '--headless=new',
    '--remote-debugging-port=9222',
    '--user-data-dir=' + tempProfile,
    '--disable-gpu',
    '--no-sandbox',
    '--window-size=1280,960',
    'about:blank'
  ]);

  await new Promise((r) => setTimeout(r, 1500));

  try {
    const targets = await fetchJson('http://localhost:9222/json');
    const pageTarget = targets.find((t) => t.type === 'page');
    if (!pageTarget) throw new Error('No page target found');

    const ws = new WebSocket(pageTarget.webSocketDebuggerUrl);
    await new Promise((resolve) => (ws.onopen = resolve));

    let msgId = 1;
    const send = (method, params = {}) =>
      new Promise((resolve) => {
        const id = msgId++;
        const handler = (evt) => {
          const res = JSON.parse(evt.data);
          if (res.id === id) {
            ws.removeEventListener('message', handler);
            resolve(res.result);
          }
        };
        ws.addEventListener('message', handler);
        ws.send(JSON.stringify({ id, method, params }));
      });

    await send('Page.enable');

    // 1. 截图：首页 Summer 模式
    await send('Page.navigate', { url: 'http://localhost:4321/' });
    await new Promise((r) => setTimeout(r, 1500));
    let snap = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync('home_summer.png', Buffer.from(snap.data, 'base64'));
    console.log('Saved home_summer.png');

    // 2. 截图：首页 Night 模式
    await send('Runtime.evaluate', {
      expression: `(() => {
        document.documentElement.dataset.environment = 'night';
        document.documentElement.dataset.theme = 'dark';
      })()`
    });
    await new Promise((r) => setTimeout(r, 600));
    snap = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync('home_night.png', Buffer.from(snap.data, 'base64'));
    console.log('Saved home_night.png');

    // 3. 截图：关于页 Summer 模式
    await send('Page.navigate', { url: 'http://localhost:4321/about/' });
    await new Promise((r) => setTimeout(r, 1500));
    snap = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync('about_summer.png', Buffer.from(snap.data, 'base64'));
    console.log('Saved about_summer.png');

    // 4. 截图：关于页 Night 模式
    await send('Runtime.evaluate', {
      expression: `(() => {
        document.documentElement.dataset.environment = 'night';
        document.documentElement.dataset.theme = 'dark';
      })()`
    });
    await new Promise((r) => setTimeout(r, 600));
    snap = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync('about_night.png', Buffer.from(snap.data, 'base64'));
    console.log('Saved about_night.png');

    // 5. 截图：关于页“经历与背景卡片”区域特写
    await send('Runtime.evaluate', {
      expression: `(() => {
        const el = document.querySelector('.background');
        if (el) el.scrollIntoView({ block: 'start' });
      })()`
    });
    await new Promise((r) => setTimeout(r, 600));
    snap = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync('about_experience_cards.png', Buffer.from(snap.data, 'base64'));
    console.log('Saved about_experience_cards.png');

    ws.close();
  } finally {
    edge.kill();
  }
}

capture().catch(console.error);
