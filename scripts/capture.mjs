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
    '--window-size=1280,1000',
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
    await send('Page.navigate', { url: 'http://localhost:4321/blog/minimalloc-from-scratch/' });
    await new Promise((r) => setTimeout(r, 1500));

    // 滚动到第一个代码块并居中
    await send('Runtime.evaluate', {
      expression: `(() => {
        const el = document.querySelector('.code-block');
        if (el) el.scrollIntoView({ block: 'center' });
      })()`
    });
    await new Promise((r) => setTimeout(r, 500));

    // 截取 Summer 模式视口截图
    const resLight = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync('viewport_summer.png', Buffer.from(resLight.data, 'base64'));
    console.log('Saved viewport_summer.png');

    // 切换到 Night 模式
    await send('Runtime.evaluate', {
      expression: `(() => {
        document.documentElement.dataset.environment = 'night';
        document.documentElement.dataset.theme = 'dark';
      })()`
    });
    await new Promise((r) => setTimeout(r, 600));

    const resDark = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync('viewport_night.png', Buffer.from(resDark.data, 'base64'));
    console.log('Saved viewport_night.png');

    ws.close();
  } finally {
    edge.kill();
  }
}

capture().catch(console.error);
