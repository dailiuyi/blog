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
    await send('Emulation.setDeviceMetricsOverride', {
      width: 1280,
      height: 960,
      deviceScaleFactor: 1,
      mobile: false
    });

    // 1. 首页 Hero + 导航
    await send('Page.navigate', { url: 'http://localhost:4321/' });
    await new Promise((r) => setTimeout(r, 1600));
    let snap = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync('homepage_hero.png', Buffer.from(snap.data, 'base64'));
    console.log('Saved homepage_hero.png');

    // 2. 状态与追番区域 (#life)
    await send('Runtime.evaluate', {
      expression: `(() => {
        const el = document.getElementById('life');
        if (el) {
          const top = el.getBoundingClientRect().top + window.scrollY - 30;
          window.scrollTo({ top, behavior: 'instant' });
        }
      })()`
    });
    await new Promise((r) => setTimeout(r, 600));
    snap = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync('homepage_now_and_watching.png', Buffer.from(snap.data, 'base64'));
    console.log('Saved homepage_now_and_watching.png');

    // 3. 音乐暗房 3D 唱片画廊 (#music)
    await send('Runtime.evaluate', {
      expression: `(() => {
        const el = document.getElementById('music');
        if (el) {
          const top = el.getBoundingClientRect().top + window.scrollY - 30;
          window.scrollTo({ top, behavior: 'instant' });
        }
      })()`
    });
    await new Promise((r) => setTimeout(r, 600));
    snap = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync('homepage_music_gallery.png', Buffer.from(snap.data, 'base64'));
    console.log('Saved homepage_music_gallery.png');

    // 4. 追番卡片近景特写
    await send('Runtime.evaluate', {
      expression: `(() => {
        document.documentElement.dataset.environment = 'summer';
        document.documentElement.dataset.theme = 'light';
        const el = document.querySelector('.watching-section');
        if (el) {
          const top = el.getBoundingClientRect().top + window.scrollY - 30;
          window.scrollTo({ top, behavior: 'instant' });
        }
      })()`
    });
    await new Promise((r) => setTimeout(r, 600));
    snap = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync('homepage_watching_focus.png', Buffer.from(snap.data, 'base64'));
    console.log('Saved homepage_watching_focus.png');

    ws.close();
  } finally {
    edge.kill();
  }
}

capture().catch(console.error);
