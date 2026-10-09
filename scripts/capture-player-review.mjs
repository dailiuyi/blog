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
  const tempProfile = 'C:\\Users\\emla\\AppData\\Local\\Temp\\edge_cdp_profile_player_' + Date.now();
  
  const edge = spawn(edgePath, [
    '--headless=new',
    '--remote-debugging-port=9224',
    '--user-data-dir=' + tempProfile,
    '--disable-gpu',
    '--no-sandbox',
    '--window-size=1280,960',
    'about:blank'
  ]);

  await new Promise((r) => setTimeout(r, 1500));

  try {
    const targets = await fetchJson('http://localhost:9224/json');
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
    await send('Runtime.enable');
    await send('Emulation.setDeviceMetricsOverride', {
      width: 1280,
      height: 960,
      deviceScaleFactor: 1,
      mobile: false
    });

    console.log('Navigating to http://localhost:4321/ ...');
    await send('Page.navigate', { url: 'http://localhost:4321/' });
    await new Promise((r) => setTimeout(r, 2000));

    const evalJs = async (expr) => {
      const res = await send('Runtime.evaluate', { expression: expr, awaitPromise: true });
      return res;
    };

    const takeScreenshot = async (filePath) => {
      const { data } = await send('Page.captureScreenshot', { format: 'png' });
      fs.writeFileSync(filePath, Buffer.from(data, 'base64'));
      console.log('Captured:', filePath);
    };

    // 1. 折叠微态 (Mini Bar)
    console.log('1. Setting collapsed mini state...');
    await evalJs(`
      if (window.__nabunanaPlayerInstance) {
        window.__nabunanaPlayerInstance.setPinned(false);
        window.__nabunanaPlayerInstance.setExpanded(false);
      }
    `);
    await new Promise((r) => setTimeout(r, 400));
    await takeScreenshot('scripts/review-player-1-collapsed.png');

    // 2. 展开卡片态 (Expanded Card) - 固定展开
    console.log('2. Setting expanded card state...');
    await evalJs(`
      if (window.__nabunanaPlayerInstance) {
        window.__nabunanaPlayerInstance.setPinned(true);
        window.__nabunanaPlayerInstance.setExpanded(true);
      }
    `);
    await new Promise((r) => setTimeout(r, 500));
    await takeScreenshot('scripts/review-player-2-expanded.png');

    // 3. 浮动歌词视窗 (切换到带歌词的歌曲并打开视窗)
    console.log('3. Setting lyrics window with active track...');
    await evalJs(`
      if (window.__nabunanaPlayerInstance) {
        // 播放一首有歌词的歌曲，比如第 1 轨 "ルラ" 或第 2 轨 "三月と狼少年"
        window.__nabunanaPlayerInstance.playTrack(1);
        window.__nabunanaPlayerInstance.setLyricsOpen(true);
      }
    `);
    // 等待歌词 fetch 并渲染
    await new Promise((r) => setTimeout(r, 1200));
    // 模拟播放到某个时间点让某行高亮居中
    await evalJs(`
      if (window.__nabunanaPlayerInstance) {
        const audio = document.querySelector('#nabunana-audio-element');
        if (audio) {
          audio.currentTime = 32;
          audio.dispatchEvent(new Event('timeupdate'));
        }
      }
    `);
    await new Promise((r) => setTimeout(r, 500));
    await takeScreenshot('scripts/review-player-3-lyrics.png');

    // 4. 打开曲库抽屉大表 (Song Library Drawer)
    console.log('4. Setting song drawer state...');
    await evalJs(`
      if (window.__nabunanaPlayerInstance) {
        window.__nabunanaPlayerInstance.setLyricsOpen(false);
        window.__nabunanaPlayerInstance.setDrawerOpen(true);
      }
    `);
    await new Promise((r) => setTimeout(r, 600));
    await takeScreenshot('scripts/review-player-4-drawer.png');

    // 5. 模拟搜索过滤 (Search filtering)
    console.log('5. Testing search filtering...');
    await evalJs(`
      if (window.__nabunanaPlayerInstance) {
        const input = document.querySelector('[data-drawer-search]');
        if (input) {
          input.value = '春ひさぎ';
          input.dispatchEvent(new Event('input', { bubbles: true }));
        }
      }
    `);
    await new Promise((r) => setTimeout(r, 500));
    await takeScreenshot('scripts/review-player-5-search.png');

    ws.close();
  } finally {
    edge.kill();
  }
}

capture().catch((e) => {
  console.error('Error during capture:', e);
  process.exit(1);
});
