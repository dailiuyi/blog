import copy
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import sys
import threading
import tempfile
import unittest
import subprocess

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from symphony_acceptance.browser import (
    ATTRIBUTE_NAMES, BROWSER_PLAN_SCHEMA, STEP_PROPERTIES, WIDTHS, _validate_browser_screenshots,
    validate_browser_plan, needs_browser,
)
from symphony_acceptance.core import PipelineError
from symphony_acceptance.evidence import publish_browser_evidence
from symphony_acceptance.github import APIError
from symphony_acceptance.reporting import render_summary, pr_description


class BrowserContractTests(unittest.TestCase):
    def setUp(self):
        self.plan = {'pages': [{'path': '/about/', 'steps': [
            {'label': '进入作品区', 'kind': 'click', 'selector': '.text-link',
             'name': '', 'value': '', 'widths': [1440, 390, 320]},
            {'label': '页尾截图', 'kind': 'screenshot', 'selector': 'footer',
             'name': '', 'value': '', 'widths': list(WIDTHS)}]}]}

    def test_only_finite_operations_on_local_static_routes(self):
        self.assertEqual(validate_browser_plan(self.plan), self.plan)
        for path in ['https://example.com/', '//example.com/', '/../private/', '/%2e%2e/', '/a?x=1']:
            invalid = copy.deepcopy(self.plan)
            invalid['pages'][0]['path'] = path
            with self.subTest(path=path), self.assertRaises(PipelineError):
                validate_browser_plan(invalid)
        for fields in [{'kind': 'evaluate'}, {'widths': [True]}, {'kind': 'press', 'value': 'Control+L'},
                       {'kind': 'attribute', 'name': 'onclick'}, {'selector': ''}]:
            invalid = copy.deepcopy(self.plan)
            invalid['pages'][0]['steps'][0].update(fields)
            with self.subTest(fields=fields), self.assertRaises(PipelineError):
                validate_browser_plan(invalid)

    def test_ui_scope_requires_browser_but_backend_does_not(self):
        config = {'browser': {'enabled': True}}
        self.assertTrue(needs_browser({'plan': {'allowedPaths': ['src/pages/about.astro']}}, config))
        self.assertFalse(needs_browser({'plan': {'allowedPaths': ['stats/main.go']}}, config))

    def test_screenshot_steps_are_bounded_and_cover_each_viewport(self):
        screenshot = {'label': '页尾截图', 'kind': 'screenshot', 'selector': 'footer',
                      'name': '', 'value': '', 'widths': list(WIDTHS)}
        plan = {'pages': [{'path': '/about/', 'steps': [copy.deepcopy(screenshot)]}]}
        self.assertEqual(validate_browser_plan(plan), plan)
        self.assertEqual(BROWSER_PLAN_SCHEMA['properties']['pages']['items']['properties']['steps']['maxItems'], 24)

        no_screenshot = copy.deepcopy(self.plan)
        no_screenshot['pages'][0]['steps'] = no_screenshot['pages'][0]['steps'][:1]
        with self.assertRaisesRegex(PipelineError, 'browser_screenshot_required'):
            validate_browser_plan(no_screenshot)

        missing_width = copy.deepcopy(plan)
        missing_width['pages'][0]['steps'][0]['widths'] = [1440, 390]
        with self.assertRaisesRegex(PipelineError, 'browser_screenshots_must_cover_all_widths'):
            validate_browser_plan(missing_width)

        too_many = copy.deepcopy(plan)
        too_many['pages'][0]['steps'] = [copy.deepcopy(screenshot) for _ in range(5)]
        with self.assertRaisesRegex(PipelineError, 'too_many_browser_screenshots'):
            validate_browser_plan(too_many)

        too_many_steps = copy.deepcopy(plan)
        too_many_steps['pages'][0]['steps'] = [copy.deepcopy(self.plan['pages'][0]['steps'][0]) for _ in range(25)]
        with self.assertRaisesRegex(PipelineError, 'invalid_browser_steps'):
            validate_browser_plan(too_many_steps)

        no_selector = copy.deepcopy(plan)
        no_selector['pages'][0]['steps'][0]['selector'] = ' '
        with self.assertRaisesRegex(PipelineError, 'browser_selector_required'):
            validate_browser_plan(no_selector)

    def test_theme_and_aria_attributes_are_allowed_but_event_attributes_are_not(self):
        self.assertIn('data-environment', STEP_PROPERTIES['name']['enum'])
        self.assertIn('aria-pressed', STEP_PROPERTIES['name']['enum'])
        self.assertNotIn('onclick', ATTRIBUTE_NAMES)

        theme_assertion = copy.deepcopy(self.plan)
        theme_assertion['pages'][0]['steps'][0].update(
            kind='attribute', selector='html', name='data-environment', value='summer')
        self.assertEqual(validate_browser_plan(theme_assertion), theme_assertion)

        unsafe = copy.deepcopy(theme_assertion)
        unsafe['pages'][0]['steps'][0]['name'] = 'onclick'
        with self.assertRaisesRegex(PipelineError, 'invalid_browser_attribute'):
            validate_browser_plan(unsafe)

    def test_missing_step_capture_requires_a_bound_failed_check_but_keeps_initial_images(self):
        step = {'label': '页尾截图', 'kind': 'screenshot', 'selector': 'footer',
                'name': '', 'value': '', 'widths': list(WIDTHS)}
        plan = {'pages': [{'path': '/about/', 'steps': [step]}]}
        theme = {'html_data_theme': None, 'html_data_environment': 'summer',
                 'html_data_lighting_preset': None, 'body_data_theme': None,
                 'body_data_environment': None, 'body_data_lighting_preset': None,
                 'color_scheme': 'normal', 'background_color': 'rgb(0, 0, 0)',
                 'text_color': 'rgb(255, 255, 255)'}

        def step_capture(width):
            height = 1000 if width == 1440 else 844
            return {'path': '/about/', 'width': width, 'step_index': 0, 'label': '页尾截图',
                    'selector': 'footer', 'file': f'page-0-{width}-step-0.png', 'theme': theme,
                    'scroll': {'x': 0, 'y': 800, 'fully_visible': True,
                               'viewport': {'width': width, 'height': height},
                               'target': {'top': 0, 'right': 300, 'bottom': 100,
                                          'left': 0, 'width': 300, 'height': 100}}}

        screenshots = [{'path': '/about/', 'width': width, 'file': f'page-0-{width}.png'}
                       for width in WIDTHS]
        screenshots.extend(step_capture(width) for width in WIDTHS if width != 320)
        report = {'status': 'failed', 'pages': [
            {'path': '/about/', 'width': width, 'checks': [
                {'label': '页尾截图', 'step_index': 0, 'passed': width != 320,
                 **({'error': 'screenshot target does not fit fully in viewport'} if width == 320 else {})}
            ]} for width in WIDTHS
        ], 'screenshots': screenshots}

        self.assertEqual(_validate_browser_screenshots(plan, report), screenshots)
        report['pages'][2]['checks'][0]['passed'] = True
        report['pages'][2]['checks'][0].pop('error')
        with self.assertRaisesRegex(PipelineError, 'incomplete_browser_screenshots'):
            _validate_browser_screenshots(plan, report)

        report['status'] = 'failed'
        report['pages'][2]['checks'][0].update(passed=False, error='target does not fit')
        report['screenshots'] = [shot for shot in report['screenshots'] if shot['file'] != 'page-0-390.png']
        with self.assertRaisesRegex(PipelineError, 'incomplete_browser_screenshots'):
            _validate_browser_screenshots(plan, report)

    @unittest.skipUnless(
        Path('C:/Users/emla/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright').exists(),
        'local Playwright is not installed; this fixture never downloads it',
    )
    def test_local_goto_retry_only_retries_refused_or_reset_and_stays_bounded(self):
        playwright = 'C:/Users/emla/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'
        node = 'C:/Users/emla/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
        if not Path(node).exists():
            node = shutil.which('node')
        if not node:
            self.skipTest('no local Node.js runtime is available')
        script = r'''const assert = require('node:assert/strict');
const { gotoWithLocalRetry } = require('./browser_runner.cjs');
(async () => {
  let refusedAttempts = 0;
  const result = await gotoWithLocalRetry({ goto: async () => {
    refusedAttempts++;
    if (refusedAttempts < 3) throw Error('page.goto: net::ERR_CONNECTION_REFUSED at http://127.0.0.1');
    return 'connected';
  } }, 'http://127.0.0.1/');
  assert.equal(result, 'connected');
  assert.equal(refusedAttempts, 3);

  let resetAttempts = 0;
  const started = Date.now();
  await assert.rejects(() => gotoWithLocalRetry({ goto: async () => {
    resetAttempts++;
    throw Error('page.goto: net::ERR_CONNECTION_RESET at http://127.0.0.1');
  } }, 'http://127.0.0.1/'), /ERR_CONNECTION_RESET/);
  assert.equal(resetAttempts, 4, 'initial attempt plus at most three retries');
  assert.ok(Date.now() - started < 5000, 'retry delays stay below five seconds');

  let otherAttempts = 0;
  await assert.rejects(() => gotoWithLocalRetry({ goto: async () => {
    otherAttempts++;
    throw Error('page.goto: net::ERR_NAME_NOT_RESOLVED');
  } }, 'http://127.0.0.1/'), /ERR_NAME_NOT_RESOLVED/);
  assert.equal(otherAttempts, 1, 'unrelated navigation failures are not retried');
})().catch(error => { console.error(error); process.exitCode = 1; });'''
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            shutil.copyfile(Path(__file__).resolve().parents[1] / 'scripts/symphony_acceptance/browser_runner.cjs',
                            job / 'browser_runner.cjs')
            (job / 'request.json').write_text(json.dumps({'head_sha': 'a' * 40, 'playwright': playwright}),
                                              encoding='utf-8')
            result = subprocess.run([node, '-e', script], cwd=job, capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    @unittest.skipUnless(
        Path('C:/Users/emla/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright').exists()
        and Path('C:/Program Files/Google/Chrome/Application/chrome.exe').exists(),
        'local Playwright and Chrome are not installed; this fixture never downloads them',
    )
    def test_real_runner_captures_footer_after_scroll_in_both_themes(self):
        from symphony_acceptance.browser import __file__ as browser_module

        html = '''<!doctype html><html data-environment="summer"><head><meta charset="utf-8">
          <style>html,body{margin:0}body{font:16px sans-serif}main{height:1100px;padding:12px}
          footer{height:100px;box-sizing:border-box;padding:20px;background:#f5d99b;color:#201800}
          html[data-environment="night"] footer{background:#132d38;color:#edf8ff}</style></head>
          <body><main><button id="theme-toggle" onclick="document.documentElement.dataset.environment =
          document.documentElement.dataset.environment === 'summer' ? 'night' : 'summer'">切换主题</button></main>
          <footer>完整页尾 · <span id="environment"></span><script>
          const update=()=>document.querySelector('#environment').textContent=document.documentElement.dataset.environment;
          new MutationObserver(update).observe(document.documentElement,{attributes:true,attributeFilter:['data-environment']});update();
          </script></footer></body></html>'''.encode('utf-8')

        class FixtureHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != '/about/':
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(html)))
                self.end_headers()
                self.wfile.write(html)

            def log_message(self, *_args):
                pass

        fixture_server = ThreadingHTTPServer(('127.0.0.1', 0), FixtureHandler)
        server_thread = threading.Thread(target=fixture_server.serve_forever, daemon=True)
        server_thread.start()
        try:
            playwright = 'C:/Users/emla/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'
            chrome = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
            node = 'C:/Users/emla/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
            if not Path(node).exists():
                node = shutil.which('node')
            if not node:
                self.skipTest('no local Node.js runtime is available')

            with tempfile.TemporaryDirectory() as directory:
                job = Path(directory)
                shutil.copyfile(Path(browser_module).with_name('browser_runner.cjs'), job / 'browser_runner.cjs')
                steps = [
                    {'label': 'Summer 页尾截图', 'kind': 'screenshot', 'selector': 'footer',
                     'name': '', 'value': '', 'widths': [320]},
                    {'label': '切换 Night', 'kind': 'click', 'selector': '#theme-toggle',
                     'name': '', 'value': '', 'widths': [320]},
                    {'label': 'Night 页尾截图', 'kind': 'screenshot', 'selector': 'footer',
                     'name': '', 'value': '', 'widths': [320]},
                    {'label': '过高区域预期失败', 'kind': 'screenshot', 'selector': 'main',
                     'name': '', 'value': '', 'widths': [320]},
                ]
                request = {'head_sha': 'a' * 40, 'base_url': f'http://127.0.0.1:{fixture_server.server_port}',
                           'pages': [{'path': '/about/', 'steps': steps}], 'widths': [320],
                           'mobile_height': 320, 'playwright': playwright, 'chrome': chrome}
                (job / 'request.json').write_text(json.dumps(request), encoding='utf-8')
                result = subprocess.run([node, str(job / 'browser_runner.cjs')], cwd=job,
                                        capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
                report = json.loads((job / 'report.json').read_text(encoding='utf-8'))
                screenshot_bytes = {shot['file']: (job / shot['file']).read_bytes() for shot in report['screenshots']}

            self.assertEqual(report['status'], 'failed', report['pages'])
            step_shots = [shot for shot in report['screenshots'] if 'step_index' in shot]
            self.assertEqual([(shot['step_index'], shot['label']) for shot in step_shots],
                             [(0, 'Summer 页尾截图'), (2, 'Night 页尾截图')])
            self.assertEqual([shot['theme']['html_data_environment'] for shot in step_shots], ['summer', 'night'])
            for shot in step_shots:
                self.assertEqual(shot['path'], '/about/')
                self.assertEqual(shot['width'], 320)
                self.assertTrue(shot['scroll']['fully_visible'])
                self.assertEqual(shot['scroll']['viewport'], {'width': 320, 'height': 320})
                self.assertGreater(shot['scroll']['y'], 0)
                self.assertGreaterEqual(shot['scroll']['target']['top'], 0)
                self.assertLessEqual(shot['scroll']['target']['bottom'], 320)
                self.assertTrue(screenshot_bytes[shot['file']].startswith(b'\x89PNG\r\n\x1a\n'))
            self.assertEqual(len({shot['file'] for shot in report['screenshots']}), len(report['screenshots']))
            failure = next(check for check in report['pages'][0]['checks'] if check.get('step_index') == 3)
            self.assertFalse(failure['passed'])
            self.assertTrue(failure['error'])
            self.assertNotIn('step_index', next(shot for shot in report['screenshots'] if shot['width'] == 320
                                                  and shot['file'] == 'page-0-320.png'))
        finally:
            fixture_server.shutdown()
            fixture_server.server_close()
            server_thread.join(timeout=3)

    def test_evidence_publication_retries_without_force_or_losing_another_commit(self):
        class API:
            repo = 'example/repo'
            head = 'old'
            calls = []
            collision = True

            def branch_head(self, branch):
                return self.head

            def request(self, method, path, body=None):
                self.calls.append((method, path, body))
                if path.endswith('/git/blobs'):
                    return {'sha': hashlib.sha1(body['content'].encode()).hexdigest()}
                if method == 'GET':
                    return {'tree': {'sha': self.head + '-tree'}}
                if path.endswith('/git/trees'):
                    return {'sha': 'new-tree'}
                if path.endswith('/git/commits'):
                    return {'sha': 'new-' + self.head}
                if method == 'PATCH':
                    if self.collision:
                        self.collision = False
                        self.head = 'concurrent'
                        raise APIError('non_fast_forward', status=422)
                    self.head = body['sha']
                    return {}
                raise AssertionError(path)

        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'page-0-320.png'
            image.write_bytes(b'\x89PNG\r\n\x1a\ntest')
            step_image = Path(directory) / 'page-0-320-step-2.png'
            step_image.write_bytes(b'\x89PNG\r\n\x1a\nstep')
            digest = hashlib.sha256(image.read_bytes()).hexdigest()
            report = {'status': 'passed', 'pages': [], 'screenshots': [{'file': image.name,
                      'path': '/about/', 'width': 320, 'local_path': str(image), 'sha256': digest},
                      {'file': step_image.name, 'path': '/about/', 'width': 320, 'step_index': 2,
                       'label': 'Night 页尾截图', 'selector': 'footer', 'local_path': str(step_image),
                       'sha256': hashlib.sha256(step_image.read_bytes()).hexdigest()}]}
            api = API()
            result = publish_browser_evidence(api, {'issue': 21, 'run_id': 'a' * 32, 'head_sha': 'b' * 40}, report)
            commits = [body for method, path, body in api.calls if path.endswith('/git/commits')]
            self.assertEqual([c['parents'] for c in commits], [['old'], ['concurrent']])
            self.assertTrue(all(body['force'] is False for method, _, body in api.calls if method == 'PATCH'))
            self.assertIn('/new-concurrent/', result['screenshots'][0]['url'])
            self.assertTrue(result['screenshots'][1]['url'].endswith('/page-0-320-step-2.png'))
            self.assertEqual(result['screenshots'][0]['local_path'], str(image))
            image.write_bytes(b'changed')
            with self.assertRaisesRegex(PipelineError, 'browser_evidence_changed'):
                publish_browser_evidence(api, {'issue': 21, 'run_id': 'a' * 32, 'head_sha': 'b' * 40}, report)

    def test_reader_summary_preserves_human_acceptance_and_separates_merge(self):
        state = {'phase': 'ready', 'issue': 21, 'head_sha': 'abc', 'base_sha': 'base',
                 'plan': {'title': '删除介绍段落，将链接改为最近在做的'},
                 'review': {'verdict': 'pass', 'summary': '需求已实现，未发现阻塞缺陷。', 'criteria': [], 'findings': []},
                 'human_acceptance': {'head_sha': 'abc', 'actor': 'owner', 'recorded_at': 'now'},
                 'ci': {'state': 'success'}}
        config = {'reviewer': {'model': 'gpt-6-astra', 'effort': 'high'}}
        text = render_summary(state, config)
        self.assertIn('需求已实现', text)
        self.assertIn('owner 已确认', text)
        self.assertNotIn('页面视觉效果待人工验收', text)
        self.assertIn('浏览器验证：本轮未执行', text)
        state.update(phase='closed', pr_merged=True)
        self.assertTrue(render_summary(state, config).startswith('**已合并。**'))
        state['human_acceptance']['head_sha'] = 'stale'
        self.assertNotIn('owner 已确认', render_summary(state, config))
        self.assertNotIn('publishing', pr_description(state))
        self.assertNotIn('尚未发布', pr_description(state))


if __name__ == '__main__':
    unittest.main()
