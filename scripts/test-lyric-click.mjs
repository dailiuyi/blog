import http from 'node:http';
import { spawn } from 'node:child_process';

async function fetchJson(url) {
  return new Promise((resolve, reject) => {
    http.get(url, (res) => {
      let data = '';
      res.on('data', (chunk) => (data += chunk));
      res.on('end', () => resolve(JSON.parse(data)));
    }).on('error', reject);
  });
}

async function testClick() {
  const edgePath = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
  const tempProfile = 'C:\\Users\\emla\\AppData\\Local\\Temp\\edge_cdp_profile_test_' + Date.now();
  
  const edge = spawn(edgePath, [
    '--headless=new',
    '--remote-debugging-port=9225',
    '--user-data-dir=' + tempProfile,
    '--disable-gpu',
    '--no-sandbox',
    '--window-size=1280,960',
    'about:blank'
  ]);

  await new Promise((r) => setTimeout(r, 1500));

  try {
    const targets = await fetchJson('http://localhost:9225/json');
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

    console.log('Navigating to http://localhost:4321/ ...');
    await send('Page.navigate', { url: 'http://localhost:4321/' });
    await new Promise((r) => setTimeout(r, 2000));

    const evalJs = async (expr) => {
      const res = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
      return res;
    };

    // 播放一首歌曲并打开歌词
    const res = await evalJs(`
      (async () => {
        const inst = window.__nabunanaPlayerInstance;
        inst.playTrack(1); // 播放 "ルラ"
        inst.setLyricsOpen(true);
        await new Promise(r => setTimeout(r, 1500));

        const audio = inst.audio;
        const line = document.querySelectorAll('.lyric-line')[3]; // 第 4 句歌词
        const targetTime = Number(line?.dataset?.time);

        const beforeTime = audio.currentTime;
        console.log('Clicking lyric line with time:', targetTime);

        // 模拟真实用户点击
        line.click();

        await new Promise(r => setTimeout(r, 1000));
        return {
          targetTime,
          beforeTime,
          currentTime: audio.currentTime,
          paused: audio.paused,
          duration: audio.duration,
          readyState: audio.readyState,
          seekable: audio.seekable.length > 0 ? [audio.seekable.start(0), audio.seekable.end(0)] : null
        };
      })()
    `);

    console.log('Result:', JSON.stringify(res.result.value, null, 2));

    ws.close();
  } finally {
    edge.kill();
  }
}

testClick().catch(console.error);
