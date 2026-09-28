// Trusted, finite browser operations. Plans contain selectors/data, never JS.
const fs = require('node:fs');
const path = require('node:path');
const request = JSON.parse(fs.readFileSync(path.join(__dirname, 'request.json'), 'utf8'));
const { chromium } = require(request.playwright);
const report = { head_sha: request.head_sha, source: 'candidate_build', generated_at: new Date().toISOString(),
  pages: [], screenshots: [], status: 'passed' };
const normalize = text => text.replace(/\s+/g, ' ').trim();
const GOTO_RETRY_DELAYS_MS = [150, 300, 600];
const RETRYABLE_GOTO_ERROR = /net::ERR_CONNECTION_(?:REFUSED|RESET)\b/i;

async function gotoWithLocalRetry(page, url, options) {
  for (let retry = 0; ; retry++) {
    try {
      return await page.goto(url, options);
    } catch (error) {
      const message = String(error && error.message || error);
      if (!RETRYABLE_GOTO_ERROR.test(message) || retry >= GOTO_RETRY_DELAYS_MS.length) throw error;
      await new Promise(resolve => setTimeout(resolve, GOTO_RETRY_DELAYS_MS[retry]));
    }
  }
}

async function captureStepScreenshot(page, item, { pageIndex, path: pagePath, width, stepIndex }) {
  const target = page.locator(item.selector);
  if (await target.count() !== 1) throw Error('screenshot selector must match exactly one element');
  await target.waitFor({ state: 'visible' });
  await target.evaluate(element => element.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' }));
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const metadata = await target.evaluate(element => {
    const rect = element.getBoundingClientRect();
    const html = document.documentElement;
    const body = document.body;
    const numeric = value => Math.round(value * 100) / 100;
    const fullyVisible = rect.top >= 0 && rect.left >= 0 && rect.bottom <= innerHeight && rect.right <= innerWidth;
    return {
      theme: {
        html_data_theme: html.getAttribute('data-theme'),
        html_data_environment: html.getAttribute('data-environment'),
        html_data_lighting_preset: html.getAttribute('data-lighting-preset'),
        body_data_theme: body.getAttribute('data-theme'),
        body_data_environment: body.getAttribute('data-environment'),
        body_data_lighting_preset: body.getAttribute('data-lighting-preset'),
        color_scheme: getComputedStyle(html).colorScheme,
        background_color: getComputedStyle(body).backgroundColor,
        text_color: getComputedStyle(body).color,
      },
      scroll: {
        x: numeric(scrollX), y: numeric(scrollY), fully_visible: fullyVisible,
        viewport: { width: innerWidth, height: innerHeight },
        target: {
          top: numeric(rect.top), right: numeric(rect.right), bottom: numeric(rect.bottom), left: numeric(rect.left),
          width: numeric(rect.width), height: numeric(rect.height),
        },
      },
    };
  });
  if (!metadata.scroll.fully_visible) throw Error('screenshot target does not fit fully in viewport');
  const filename = `page-${pageIndex}-${width}-step-${stepIndex}.png`;
  await page.screenshot({ path: path.join(__dirname, filename), animations: 'disabled', fullPage: false });
  return { path: pagePath, width, step_index: stepIndex, label: item.label,
    selector: item.selector, file: filename, ...metadata };
}

async function step(page, item, screenshotContext) {
  const target = item.selector ? page.locator(item.selector) : null;
  switch (item.kind) {
    case 'visible': await target.waitFor({ state: 'visible' }); break;
    case 'absent': if (await target.count()) throw Error('element still exists'); break;
    case 'text_contains': {
      const text = normalize(await target.innerText());
      if (!text.includes(normalize(item.value))) throw Error('expected text not found: ' + text.slice(0, 180));
      break;
    }
    case 'attribute': if (await target.getAttribute(item.name) !== item.value) throw Error('attribute does not match'); break;
    case 'click': await target.click(); break;
    case 'press': await target.press(item.value); break;
    case 'focus': {
      await target.focus();
      if (!await target.evaluate(element => element === document.activeElement)) throw Error('element cannot receive focus');
      break;
    }
    case 'fragment': {
      await page.waitForFunction(fragment => location.hash === fragment, item.value);
      // Confirm that the anchor destination exists and is reached, beyond URL changes.
      await page.waitForFunction(fragment => {
        const element = document.getElementById(fragment.slice(1));
        if (!element) return false;
        const rect = element.getBoundingClientRect();
        return rect.top < innerHeight && rect.bottom > 0;
      }, item.value);
      break;
    }
    case 'screenshot': {
      const screenshot = await captureStepScreenshot(page, item, screenshotContext);
      screenshotContext.report.screenshots.push(screenshot);
      break;
    }
    default: throw Error('unsupported operation');
  }
  if (new URL(page.url()).origin !== request.base_url) throw Error('navigation left candidate preview');
}

async function runBrowser() {
  const browser = await chromium.launch({ executablePath: request.chrome, headless: true });
  const timer = setTimeout(() => browser.close().catch(() => {}), 270000);
  try {
    for (let index = 0; index < request.pages.length; index++) {
      const target = request.pages[index];
      for (const width of request.widths) {
        const context = await browser.newContext({ viewport: { width, height: width === 1440 ? 1000 : (request.mobile_height || 844) },
          deviceScaleFactor: 1, serviceWorkers: 'block' });
        const page = await context.newPage();
        page.setDefaultTimeout(6000);
        const row = { path: target.path, width, checks: [] };
        const add = (label, passed, error = '', stepIndex = undefined) => {
          row.checks.push({ label, passed, ...(stepIndex !== undefined ? { step_index: stepIndex } : {}),
            ...(error ? { error } : {}) });
          if (!passed) report.status = 'failed';
        };
        await context.route('**/*', async route => {
          const req = route.request();
          const url = new URL(req.url());
          if (url.origin === request.base_url && !url.pathname.startsWith('/api/')) {
            // Local ignored media is served from the configured production media
            // origin without exposing other hosts, services, or browser sessions.
            if (url.pathname.startsWith('/media/') && request.media_origin && req.method() === 'GET') {
              try {
                const local = await route.fetch();
                if (local.ok()) return route.fulfill({ response: local });
                const media = await route.fetch({ url: request.media_origin + url.pathname, maxRedirects: 0 });
                return route.fulfill({ response: media });
              } catch { return route.abort(); }
            }
            if (['GET', 'HEAD'].includes(req.method())) return route.continue();
          }
          // Preview checks never send analytics writes or contact arbitrary URLs.
          return route.abort();
        });
        try {
          const response = await gotoWithLocalRetry(page, request.base_url + target.path,
            { waitUntil: 'networkidle', timeout: 25000 });
          add('页面 HTTP 200', response.status() === 200);
          await page.evaluate(() => document.fonts.ready);
          add('无横向溢出', await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
          const filename = `page-${index}-${width}.png`;
          await page.screenshot({ path: path.join(__dirname, filename), animations: 'disabled' });
          report.screenshots.push({ path: target.path, width, file: filename });
          for (let stepIndex = 0; stepIndex < target.steps.length; stepIndex++) {
            const item = target.steps[stepIndex];
            if (!item.widths.includes(width)) continue;
            try {
              await step(page, item, { pageIndex: index, path: target.path, width, stepIndex, report });
              add(item.label, true, '', stepIndex);
            } catch (error) {
              add(item.label, false, String(error.message).split('\n')[0].slice(0, 240), stepIndex);
            }
          }
        } catch (error) { add('打开候选页面', false, String(error.message).split('\n')[0].slice(0, 240)); }
        report.pages.push(row);
        await context.close();
      }
    }
  } finally {
    clearTimeout(timer);
    await browser.close();
    fs.writeFileSync(path.join(__dirname, 'report.json'), JSON.stringify(report, null, 2));
  }
  console.log(JSON.stringify({ status: report.status, pages: report.pages.length }));
}

if (require.main === module) {
  runBrowser().catch(error => { console.error(String(error.message)); process.exitCode = 1; });
}

module.exports = { gotoWithLocalRetry };
