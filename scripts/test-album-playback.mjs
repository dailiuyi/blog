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

async function testAll() {
  const edgePath = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
  const tempProfile = 'C:\\Users\\emla\\AppData\\Local\\Temp\\edge_cdp_profile_all_' + Date.now();
  
  const edge = spawn(edgePath, [
    '--headless=new',
    '--remote-debugging-port=9228',
    '--user-data-dir=' + tempProfile,
    '--disable-gpu',
    '--no-sandbox',
    '--window-size=1280,960',
    'about:blank'
  ]);

  await new Promise((r) => setTimeout(r, 1500));

  try {
    const targets = await fetchJson('http://localhost:9228/json');
    const pageTarget = targets.find((t) => t.type === 'page');
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

    await send('Page.navigate', { url: 'http://localhost:4321/' });
    await new Promise((r) => setTimeout(r, 2000));

    // Scroll music section into view
    await send('Runtime.evaluate', {
      expression: `document.querySelector('#music').scrollIntoView({ block: 'start' });`
    });
    await new Promise((r) => setTimeout(r, 600));

    // Helper for mouse click at element center
    const clickElement = async (selector) => {
      const bounds = await send('Runtime.evaluate', {
        expression: `
          (() => {
            const el = document.querySelector('${selector}');
            const rect = el.getBoundingClientRect();
            return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
          })()
        `,
        returnByValue: true
      });
      const { x, y } = bounds.result.value;
      await send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 });
      await new Promise((r) => setTimeout(r, 80));
      await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 });
      await new Promise((r) => setTimeout(r, 600));
    };

    const takeScreenshot = async (filePath) => {
      const { data } = await send('Page.captureScreenshot', { format: 'png' });
      fs.writeFileSync(filePath, Buffer.from(data, 'base64'));
      console.log('Saved screenshot:', filePath);
    };

    // 1. Initial view: album 0 centered
    await takeScreenshot('scripts/review-album-1-initial.png');

    // 2. Click on card 2 (album "だから僕は音楽を辞めた")
    console.log('Clicking card 2...');
    await clickElement('.album-card[data-album-index="2"]');

    const stateAfterCard2 = await send('Runtime.evaluate', {
      expression: `
        (() => {
          const audio = document.querySelector('#nabunana-audio-element');
          const card2 = document.querySelector('.album-card[data-album-index="2"]');
          return {
            audioPaused: audio?.paused,
            audioCurrentTime: audio?.currentTime,
            audioSrc: audio?.src,
            card2Playing: card2?.dataset.playing,
            card2AriaCurrent: card2?.getAttribute('aria-current')
          };
        })()
      `,
      returnByValue: true
    });
    console.log('State after clicking card 2:', stateAfterCard2.result.value);

    // Capture screenshot showing active and playing state of card 2
    await takeScreenshot('scripts/review-album-2-playing.png');

    // 3. Click card 2 again (toggle pause)
    console.log('Clicking card 2 again (toggle pause)...');
    await clickElement('.album-card[data-album-index="2"]');

    const stateAfterPause = await send('Runtime.evaluate', {
      expression: `
        (() => {
          const audio = document.querySelector('#nabunana-audio-element');
          const card2 = document.querySelector('.album-card[data-album-index="2"]');
          return {
            audioPaused: audio?.paused,
            card2Playing: card2?.dataset.playing
          };
        })()
      `,
      returnByValue: true
    });
    console.log('State after toggling pause:', stateAfterPause.result.value);

    await takeScreenshot('scripts/review-album-3-paused.png');

    ws.close();
  } finally {
    edge.kill();
  }
}

testAll().catch(console.error);
