"""Execute a bounded browser plan against the exact candidate's built site."""
import functools
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
from uuid import uuid4

from .core import PipelineError

WIDTHS = [1440, 390, 320]
KINDS = ['visible', 'absent', 'text_contains', 'attribute', 'click', 'press', 'focus', 'fragment', 'screenshot']
ATTRIBUTE_NAMES = ['', 'href', 'aria-label', 'aria-hidden', 'role', 'type', 'data-theme',
                   'data-environment', 'data-lighting-preset', 'aria-pressed', 'aria-expanded']
MAX_STEPS = 24
MAX_SCREENSHOT_STEPS = 4
STEP_PROPERTIES = {
    'label': {'type': 'string'}, 'kind': {'type': 'string', 'enum': KINDS},
    'selector': {'type': 'string'}, 'name': {'type': 'string', 'enum': ATTRIBUTE_NAMES}, 'value': {'type': 'string'},
    'widths': {'type': 'array', 'items': {'type': 'integer', 'enum': WIDTHS}},
}
BROWSER_PLAN_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {'pages': {'type': 'array', 'minItems': 1, 'maxItems': 5, 'items': {
        'type': 'object', 'additionalProperties': False,
        'properties': {'path': {'type': 'string'}, 'steps': {'type': 'array', 'minItems': 1, 'maxItems': MAX_STEPS,
            'items': {'type': 'object', 'additionalProperties': False,
                      'properties': STEP_PROPERTIES, 'required': list(STEP_PROPERTIES)}}},
        'required': ['path', 'steps']}}}, 'required': ['pages'],
}


def validate_browser_plan(plan):
    if not isinstance(plan, dict) or set(plan) != {'pages'} or not isinstance(plan['pages'], list) or not 1 <= len(plan['pages']) <= 5:
        raise PipelineError('invalid_browser_plan')
    seen = set()
    for page in plan['pages']:
        if not isinstance(page, dict) or set(page) != {'path', 'steps'}:
            raise PipelineError('invalid_browser_page')
        path = page['path']
        if (not isinstance(path, str) or not re.fullmatch(r'/(?:[A-Za-z0-9_-]+/)*', path)
                or path in seen or len(path) > 200):
            raise PipelineError('invalid_browser_path')
        seen.add(path)
        if not isinstance(page['steps'], list) or not 1 <= len(page['steps']) <= MAX_STEPS:
            raise PipelineError('invalid_browser_steps')
        for step in page['steps']:
            if (not isinstance(step, dict) or set(step) != set(STEP_PROPERTIES)
                    or step['kind'] not in KINDS or any(not isinstance(step[k], str) or len(step[k]) > 400
                                                      for k in ('label', 'selector', 'name', 'value'))
                    or not step['label'].strip() or not isinstance(step['widths'], list)
                    or not step['widths'] or any(type(w) is not int or w not in WIDTHS for w in step['widths'])
                    or len(set(step['widths'])) != len(step['widths'])):
                raise PipelineError('invalid_browser_step')
            if step['kind'] != 'fragment' and not step['selector'].strip():
                raise PipelineError('browser_selector_required')
            if step['kind'] == 'press' and step['value'] not in ('Enter', 'Tab', 'Escape', 'Space'):
                raise PipelineError('invalid_browser_key')
            if step['kind'] == 'fragment' and not re.fullmatch(r'#[A-Za-z0-9_-]+', step['value']):
                raise PipelineError('invalid_browser_fragment')
            if step['name'] not in ATTRIBUTE_NAMES:
                raise PipelineError('invalid_browser_attribute')
            if step['kind'] == 'attribute' and step['name'] == '':
                raise PipelineError('invalid_browser_attribute')
        screenshot_steps = [step for step in page['steps'] if step['kind'] == 'screenshot']
        if not screenshot_steps:
            raise PipelineError('browser_screenshot_required')
        if len(screenshot_steps) > MAX_SCREENSHOT_STEPS:
            raise PipelineError('too_many_browser_screenshots')
        covered_widths = {width for step in screenshot_steps for width in step['widths']}
        if covered_widths != set(WIDTHS):
            raise PipelineError('browser_screenshots_must_cover_all_widths')
    return plan


def needs_browser(state, config):
    if not config.get('browser', {}).get('enabled'):
        return False
    patterns = state['plan']['allowedPaths']
    return any(p.startswith(('src/', 'public/')) or p.startswith('*') for p in patterns)


def _validate_browser_screenshots(plan, report):
    expected = {}
    page_reports = {(page.get('path'), page.get('width')): page
                    for page in report.get('pages', []) if isinstance(page, dict)}
    for page_index, page in enumerate(plan['pages']):
        for width in WIDTHS:
            expected[(page['path'], width, None)] = {'file': f'page-{page_index}-{width}.png'}
            for step_index, step in enumerate(page['steps']):
                if step['kind'] == 'screenshot' and width in step['widths']:
                    expected[(page['path'], width, step_index)] = {
                        'file': f'page-{page_index}-{width}-step-{step_index}.png',
                        'label': step['label'], 'selector': step['selector'],
                    }

    screenshots = report.get('screenshots')
    if not isinstance(screenshots, list) or len(screenshots) > len(expected):
        raise PipelineError('incomplete_browser_screenshots')
    seen = set()
    for screenshot in screenshots:
        if not isinstance(screenshot, dict):
            raise PipelineError('invalid_browser_screenshot')
        path = screenshot.get('path')
        width = screenshot.get('width')
        step_index = screenshot.get('step_index')
        if step_index is not None and type(step_index) is not int:
            raise PipelineError('invalid_browser_screenshot')
        identity = (path, width, step_index)
        expected_shot = expected.get(identity)
        if (expected_shot is None or identity in seen
                or screenshot.get('file') != expected_shot['file']
                or not re.fullmatch(r'page-[0-9]+-[0-9]+(?:-step-[0-9]+)?\.png', screenshot['file'])):
            raise PipelineError('invalid_browser_screenshot')
        if step_index is not None:
            if any(screenshot.get(key) != expected_shot[key] for key in ('label', 'selector')):
                raise PipelineError('invalid_browser_screenshot')
            theme = screenshot.get('theme')
            scroll = screenshot.get('scroll')
            viewport = scroll.get('viewport') if isinstance(scroll, dict) else None
            target = scroll.get('target') if isinstance(scroll, dict) else None
            if (not isinstance(theme, dict)
                    or not {'html_data_theme', 'html_data_environment', 'html_data_lighting_preset',
                            'body_data_theme', 'body_data_environment', 'body_data_lighting_preset',
                            'color_scheme', 'background_color', 'text_color'} <= set(theme)
                    or any(value is not None and not isinstance(value, str) for value in theme.values())
                    or not isinstance(scroll, dict) or scroll.get('fully_visible') is not True
                    or type(scroll.get('x')) not in (int, float) or type(scroll.get('y')) not in (int, float)
                    or not isinstance(viewport, dict) or viewport.get('width') != width
                    or type(viewport.get('height')) not in (int, float) or viewport['height'] <= 0
                    or not isinstance(target, dict)
                    or any(type(target.get(key)) not in (int, float)
                           for key in ('top', 'right', 'bottom', 'left', 'width', 'height'))):
                raise PipelineError('invalid_browser_screenshot_metadata')
            if (target['top'] < 0 or target['left'] < 0 or target['bottom'] > viewport['height'] + 1
                    or target['right'] > width + 1 or target['width'] <= 0 or target['height'] <= 0):
                raise PipelineError('invalid_browser_screenshot_metadata')
        seen.add(identity)

    for identity, expected_shot in expected.items():
        if identity in seen:
            continue
        path, width, step_index = identity
        if step_index is None:
            raise PipelineError('incomplete_browser_screenshots')
        checks = page_reports.get((path, width), {}).get('checks', [])
        failures = [check for check in checks if isinstance(check, dict)
                    and check.get('step_index') == step_index
                    and check.get('label') == expected_shot['label']
                    and check.get('passed') is False
                    and isinstance(check.get('error'), str) and check['error'].strip()]
        if report.get('status') != 'failed' or len(failures) != 1:
            raise PipelineError('incomplete_browser_screenshots')
    return screenshots


class PreviewHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_head(self):
        path = Path(self.translate_path(self.path)).resolve()
        if not path.is_relative_to(Path(self.directory).resolve()):
            self.send_error(403)
            return None
        if path.is_dir() and not (path / 'index.html').is_file():
            self.send_error(404)
            return None
        if path.is_dir() and not (path / 'index.html').resolve().is_relative_to(Path(self.directory).resolve()):
            self.send_error(403)
            return None
        return super().send_head()


def run_browser(root, state, config, plan):
    plan = validate_browser_plan(plan)
    settings = config['browser']
    root = Path(root).resolve()
    if not (root / 'dist').resolve().is_relative_to(root) or not (root / 'dist/index.html').is_file():
        raise PipelineError('browser_build_missing')
    # Use a dedicated bridge directory on a Windows-mounted drive. No browser
    # installation or personal Chrome profile is required.
    job = Path(settings['bridge_root']) / uuid4().hex
    job.mkdir(parents=True)
    script = job / 'browser_runner.cjs'
    shutil.copyfile(Path(__file__).with_name('browser_runner.cjs'), script)
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(PreviewHandler, directory=str(root / 'dist')))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = {'head_sha': state['head_sha'], 'base_url': f'http://127.0.0.1:{server.server_port}',
                   'pages': plan['pages'], 'widths': WIDTHS,
                   'mobile_height': settings.get('mobile_height', 844),
                   'playwright': settings['playwright_windows'], 'chrome': settings['chrome_windows'],
                   'media_origin': settings.get('media_origin')}
        (job / 'request.json').write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
        windows_script = subprocess.check_output(['wslpath', '-w', str(script)], text=True).strip()
        environment = {k: v for k, v in os.environ.items()
                       if not any(secret in k.upper() for secret in ('TOKEN', 'KEY', 'SECRET', 'PASSWORD'))}
        result = subprocess.run([settings['node_executable'], windows_script], env=environment,
                                capture_output=True, text=True, timeout=settings.get('timeout_seconds', 300))
        if not (job / 'report.json').is_file():
            raise PipelineError('browser_runtime_failed')
        report = json.loads((job / 'report.json').read_text(encoding='utf-8'))
        if report.get('head_sha') != state['head_sha'] or report.get('status') not in {'passed', 'failed'}:
            raise PipelineError('invalid_browser_report')
        expected = {(page['path'], width) for page in plan['pages'] for width in WIDTHS}
        observed = {(page.get('path'), page.get('width')) for page in report.get('pages', [])}
        if observed != expected or len(report['pages']) != len(expected):
            raise PipelineError('incomplete_browser_report')
        if result.returncode and report['status'] == 'passed':
            raise PipelineError('browser_runtime_failed')
        screenshots = _validate_browser_screenshots(plan, report)
        evidence = root / '.local/browser-review' / uuid4().hex
        evidence.mkdir(parents=True)
        for screenshot in screenshots:
            name = screenshot.get('file', '')
            source = job / name
            if not source.is_file() or not source.read_bytes().startswith(b'\x89PNG\r\n\x1a\n'):
                raise PipelineError('browser_screenshot_missing')
            target = evidence / name
            shutil.copyfile(source, target)
            screenshot['sha256'] = hashlib.sha256(target.read_bytes()).hexdigest()
            screenshot['local_path'] = str(target)
        (evidence / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        return report
    except subprocess.TimeoutExpired as exc:
        raise PipelineError('browser_timeout') from exc
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
