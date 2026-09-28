// Fixed checks for this repository's Symphony workflow.
// Evidence stays in .local/workflow and is not a merge or deploy approval.
import { spawn, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createWriteStream, existsSync, lstatSync, mkdirSync, readFileSync, readlinkSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const KNOWN = ['motion', 'release', 'stats', 'site'];
const ALWAYS = ['site', 'stats', 'release'];
const MOTION_PATHS = [
  'src/lib/motion-math.ts',
  'src/lib/site-motion.ts',
  'src/components/common/MotionDirector.astro',
  'scripts/test-motion.mjs',
];
const PLAN_KEYS = ['acceptance', 'allowedPaths', 'checks', 'title'];
const SITE_ARTIFACTS = ['dist/index.html', 'dist/robots.txt', 'dist/rss.xml'];
const TIMEOUT_MS = {
  site: 15 * 60 * 1000,
  stats: 3 * 60 * 1000,
  release: 2 * 60 * 1000,
  motion: 2 * 60 * 1000,
};

class PolicyError extends Error {
  constructor(code, detail = '') {
    super(detail ? `${code}: ${detail}` : code);
    this.code = code;
  }
}

function npmCommand() {
  return process.platform === 'win32' ? 'npm.cmd' : 'npm';
}

function needsShell(file) {
  return process.platform === 'win32' && file.toLowerCase().endsWith('.cmd');
}

function iso() {
  return new Date().toISOString();
}

function git(cwd, args) {
  const result = spawnSync('git', args, { cwd, encoding: 'utf8' });
  if (result.error) throw new PolicyError('git_unavailable', result.error.message);
  if (result.status !== 0) {
    throw new PolicyError('git_failed', (result.stderr || result.stdout || '').trim());
  }
  return result.stdout;
}

function parseArgs(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (!token.startsWith('--')) {
      out._.push(token);
      continue;
    }
    const value = argv[i + 1];
    if (!value || value.startsWith('--')) throw new PolicyError('missing_argument', token);
    out[token.slice(2)] = value;
    i += 1;
  }
  return out;
}

function readJson(file) {
  let text;
  try {
    text = readFileSync(file, 'utf8');
  } catch (error) {
    throw new PolicyError('plan_unreadable', error.message);
  }
  try {
    return JSON.parse(text);
  } catch (error) {
    throw new PolicyError('plan_invalid_json', error.message);
  }
}

function writeJson(file, value) {
  mkdirSync(path.dirname(file), { recursive: true });
  const temp = `${file}.${process.pid}.tmp`;
  writeFileSync(temp, `${JSON.stringify(value, null, 2)}\n`);
  rmSync(file, { force: true });
  renameSync(temp, file);
}

function globToRegExp(pattern) {
  let source = '^';
  for (const char of pattern) {
    source += char === '*' ? '.*' : /[\\^$+?.()|[\]{}]/.test(char) ? `\\${char}` : char;
  }
  return new RegExp(`${source}$`);
}

function matches(file, pattern) {
  return globToRegExp(pattern).test(file);
}

function validPattern(pattern) {
  if (typeof pattern !== 'string' || pattern.trim() !== pattern || pattern === '') return false;
  if (pattern.startsWith('/') || /^[A-Za-z]:/.test(pattern) || pattern.includes('\\')) return false;
  return !pattern.split('/').includes('..');
}

function requiredChecks(allowedPaths) {
  const checks = [...ALWAYS];
  if (MOTION_PATHS.some((file) => allowedPaths.some((pattern) => matches(file, pattern)))) {
    checks.push('motion');
  }
  return checks;
}

function validatePlan(input) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) throw new PolicyError('plan_must_be_object');
  const keys = Object.keys(input).sort();
  if (keys.join() !== PLAN_KEYS.join()) {
    throw new PolicyError('plan_fields_must_be_title_acceptance_allowedPaths_checks');
  }
  for (const field of ['title', 'acceptance']) {
    if (typeof input[field] !== 'string' || input[field].trim() === '' || input[field].length > 6000) {
      throw new PolicyError('invalid_text', field);
    }
  }
  if (!Array.isArray(input.allowedPaths) || input.allowedPaths.length === 0) {
    throw new PolicyError('invalid_paths');
  }
  if (input.allowedPaths.some((pattern) => !validPattern(pattern))) throw new PolicyError('invalid_paths');
  if (!Array.isArray(input.checks) || input.checks.some((name) => typeof name !== 'string')) {
    throw new PolicyError('invalid_checks');
  }
  if (new Set(input.checks).size !== input.checks.length) throw new PolicyError('duplicate_checks');
  const unknown = input.checks.filter((name) => !KNOWN.includes(name));
  if (unknown.length) throw new PolicyError('unknown_checks', unknown.join(','));
  const required = requiredChecks(input.allowedPaths);
  const missing = required.filter((name) => !input.checks.includes(name));
  if (missing.length) throw new PolicyError('missing_checks', missing.join(','));
  const checks = KNOWN.filter((name) => input.checks.includes(name));
  return {
    plan: {
      title: input.title.trim(),
      acceptance: input.acceptance.trim(),
      allowedPaths: input.allowedPaths,
      checks,
    },
    checks,
    required,
  };
}

function extractPlan(text) {
  const start = text.indexOf('<!-- workflow-plan');
  const end = start < 0 ? -1 : text.indexOf('-->', start);
  if (start < 0 || end < 0) throw new PolicyError('plan_marker_missing');
  try {
    return JSON.parse(text.slice(start + '<!-- workflow-plan'.length, end));
  } catch (error) {
    throw new PolicyError('plan_invalid_json', error.message);
  }
}

function assertIssue(issue) {
  if (issue !== 'proof' && !/^GH-[1-9][0-9]*$/.test(issue || '')) {
    throw new PolicyError('issue_must_be_GH_number_or_proof');
  }
  return issue;
}

function issueHome(root, issue) {
  return path.join(root, '.local', 'workflow', issue);
}

function readState(home) {
  const file = path.join(home, 'state.json');
  if (!existsSync(file)) return { runs: [] };
  return readJson(file);
}

function planHash(plan) {
  return createHash('sha256').update(JSON.stringify(plan)).digest('hex');
}

function fingerprint(root) {
  const listed = git(root, ['ls-files', '-z', '--cached', '--others', '--exclude-standard']);
  const names = [...new Set(listed.split('\0').filter(Boolean))].sort();
  const regular = [];
  const other = new Map();
  for (const name of names) {
    const abs = path.join(root, ...name.split('/'));
    if (!existsSync(abs)) {
      other.set(name, 'missing');
      continue;
    }
    const stat = lstatSync(abs);
    if (stat.isSymbolicLink()) other.set(name, 'link:' + readlinkSync(abs));
    else if (stat.isFile()) regular.push(name);
    else other.set(name, 'other');
  }
  // Git-normalized blobs keep core.autocrlf rewrites from looking like source edits.
  const blobs = new Map();
  for (let i = 0; i < regular.length; i += 64) {
    const batch = regular.slice(i, i + 64);
    const output = git(root, ['hash-object', '--', ...batch]).trim();
    const hashes = output ? output.split(/\r?\n/) : [];
    if (hashes.length !== batch.length || hashes.some((hash) => !/^[0-9a-f]{40,64}$/.test(hash))) {
      throw new PolicyError('git_hash_failed');
    }
    batch.forEach((name, index) => blobs.set(name, hashes[index]));
  }
  const digest = createHash('sha256');
  for (const name of names) {
    digest.update(name);
    digest.update('\0');
    digest.update(blobs.get(name) || other.get(name));
    digest.update('\0');
  }
  return digest.digest('hex');
}
function evaluateAttempt(state, sourceFingerprint, hash) {
  const runs = Array.isArray(state.runs) ? state.runs : [];
  const last = runs[runs.length - 1];
  if (last?.status === 'passed' && last.fingerprint === sourceFingerprint && last.planHash === hash) return 'reuse';
  if (last?.status === 'failed' && last.fingerprint === sourceFingerprint) {
    throw new PolicyError('unchanged_failed_submission');
  }
  // The external acceptance controller owns the shared check/review repair
  // budget. This checker only prevents reusing an unchanged failed source.
  return 'execute';
}

function killProcess(child) {
  if (!child.pid) return;
  if (process.platform === 'win32') {
    spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
    return;
  }
  child.kill('SIGTERM');
}

function spawnWait(file, args, options) {
  mkdirSync(path.dirname(options.logPath), { recursive: true });
  const log = createWriteStream(options.logPath, { flags: 'w' });
  log.write(`$ ${[file, ...args].join(' ')}\n`);
  const launched = needsShell(file)
    ? { file: process.env.ComSpec || 'cmd.exe', args: ['/d', '/s', '/c', [file, ...args].join(' ')] }
    : { file, args };
  return new Promise((resolve, reject) => {
    let settled = false;
    let timer;
    const finish = (settle, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      log.end(() => settle(value));
    };
    const child = spawn(launched.file, launched.args, {
      cwd: options.cwd,
      env: options.env,
      windowsHide: true,
    });
    timer = setTimeout(() => {
      killProcess(child);
      finish(reject, new Error(`${file} timed out`));
    }, options.timeoutMs);
    child.stdout?.pipe(log, { end: false });
    child.stderr?.pipe(log, { end: false });
    child.on('error', (error) => finish(reject, error));
    child.on('close', (code) => finish(resolve, code ?? 1));
  });
}

function writePosixScripts(root, home) {
  // Git stores these scripts as LF. A Windows checkout rewrites them as CRLF, which
  // makes the POSIX runner reject the file before any assertion runs.
  const dir = path.join(home, 'posix-scripts');
  mkdirSync(dir, { recursive: true });
  for (const name of ['test-deploy-release.sh', 'deploy-release.sh']) {
    const text = readFileSync(path.join(root, 'scripts', name), 'utf8').replace(/\r\n/g, '\n').replace(/\r/g, '\n');
    writeFileSync(path.join(dir, name), text, 'utf8');
  }
  return dir;
}

function profileSpec(root, profile, home = path.join(root, '.local', 'workflow', 'spec')) {
  if (profile === 'site') {
    return {
      file: npmCommand(),
      args: ['run', 'build'],
      cwd: root,
      source: 'npm run build',
      timeoutMs: TIMEOUT_MS.site,
      after: () => {
        const missing = SITE_ARTIFACTS.filter((rel) => !existsSync(path.join(root, rel)));
        if (missing.length) throw new Error(`missing ${missing.join(', ')}`);
      },
    };
  }
  if (profile === 'stats') {
    return {
      file: 'go',
      args: ['test', './...'],
      cwd: path.join(root, 'stats'),
      source: 'go test ./...',
      timeoutMs: TIMEOUT_MS.stats,
    };
  }
  if (profile === 'release') {
    const dir = writePosixScripts(root, home);
    return {
      file: 'bash',
      args: ['test-deploy-release.sh'],
      cwd: dir,
      source: 'bash scripts/test-deploy-release.sh',
      detail: 'LF text of the repository scripts, matching the Git blobs rather than the CRLF checkout',
      timeoutMs: TIMEOUT_MS.release,
    };
  }
  if (profile === 'motion') {
    return {
      file: process.execPath,
      args: ['--test', 'scripts/test-motion.mjs'],
      cwd: root,
      source: 'node --test scripts/test-motion.mjs',
      timeoutMs: TIMEOUT_MS.motion,
    };
  }
  throw new PolicyError('unknown_checks', profile);
}

async function runProfile(root, home, profile) {
  const spec = profileSpec(root, profile, home);
  const logPath = path.join(home, `${profile}.log`);
  const startedAt = iso();
  const command = [spec.file, ...spec.args];
  let exitCode = null;
  try {
    exitCode = await spawnWait(spec.file, spec.args, {
      cwd: spec.cwd,
      env: { ...process.env, ASTRO_TELEMETRY_DISABLED: '1' },
      logPath,
      timeoutMs: spec.timeoutMs,
    });
  } catch (error) {
    return {
      profile,
      status: 'failed',
      mode: 'executed',
      command,
      source: spec.source,
      exitCode,
      reason: 'command_failed',
      detail: error.message,
      log: path.relative(root, logPath).replaceAll('\\', '/'),
      startedAt,
      finishedAt: iso(),
    };
  }
  if (exitCode !== 0) {
    return {
      profile,
      status: 'failed',
      mode: 'executed',
      command,
      source: spec.source,
      detail: spec.detail,
      exitCode,
      reason: `${profile}_failed`,
      log: path.relative(root, logPath).replaceAll('\\', '/'),
      startedAt,
      finishedAt: iso(),
    };
  }
  try {
    spec.after?.();
  } catch (error) {
    return {
      profile,
      status: 'failed',
      mode: 'executed',
      command,
      source: spec.source,
      exitCode,
      reason: 'artifact_missing',
      detail: error.message,
      log: path.relative(root, logPath).replaceAll('\\', '/'),
      startedAt,
      finishedAt: iso(),
    };
  }
  return {
    profile,
    status: 'passed',
    mode: 'executed',
    command,
    source: spec.source,
    detail: spec.detail,
    exitCode: 0,
    log: path.relative(root, logPath).replaceAll('\\', '/'),
    startedAt,
    finishedAt: iso(),
  };
}

function snapshotIdentity(root) {
  let head = '';
  try {
    head = git(root, ['rev-parse', 'HEAD']).trim();
  } catch {
    head = '';
  }
  let dirty = true;
  try {
    dirty = git(root, ['status', '--porcelain', '--untracked-files=normal']).trim() !== '';
  } catch {
    dirty = true;
  }
  return { head, dirty };
}

async function checkIssue(root, issue, planInput) {
  const validated = validatePlan(planInput);
  const home = issueHome(root, issue);
  mkdirSync(home, { recursive: true });
  const statePath = path.join(home, 'state.json');
  const state = readState(home);
  const sourceFingerprint = fingerprint(root);
  const hash = planHash(validated.plan);
  const decision = evaluateAttempt(state, sourceFingerprint, hash);
  if (decision === 'reuse') {
    const last = state.runs[state.runs.length - 1];
    return { ...last, mode: 'reused' };
  }
  const identity = snapshotIdentity(root);
  const run = {
    issue,
    plan: validated.plan,
    planHash: hash,
    fingerprint: sourceFingerprint,
    head: identity.head,
    dirty: identity.dirty,
    status: 'running',
    reason: '',
    fingerprintAfter: '',
    profiles: [],
    startedAt: iso(),
    finishedAt: '',
  };
  state.runs.push(run);
  writeJson(statePath, state);
  for (const profile of validated.checks) {
    process.stdout.write(`RUN ${profile}\n`);
    const result = await runProfile(root, home, profile);
    run.profiles.push(result);
    writeJson(statePath, state);
    if (result.status !== 'passed') {
      run.status = 'failed';
      run.reason = result.reason;
      run.fingerprintAfter = '';
      delete run.fingerprintAfter;
      run.finishedAt = iso();
      writeJson(statePath, state);
      writeJson(path.join(home, 'evidence.json'), run);
      return run;
    }
  }
  const after = fingerprint(root);
  if (after !== sourceFingerprint) {
    run.status = 'failed';
    run.reason = 'source_changed_during_checks';
    run.fingerprintAfter = after;
    run.finishedAt = iso();
    writeJson(statePath, state);
    writeJson(path.join(home, 'evidence.json'), run);
    return run;
  }
  run.status = 'passed';
  run.reason = '';
  run.fingerprintAfter = '';
  run.finishedAt = iso();
  delete run.reason;
  delete run.fingerprintAfter;
  writeJson(statePath, state);
  writeJson(path.join(home, 'evidence.json'), run);
  return run;
}

function gitEmail(cwd, args) {
  try {
    return git(cwd, args).trim().toLowerCase();
  } catch {
    return '';
  }
}

function reviewIssue(root, issue, planInput, reviewedBy, reviewedSha) {
  const validated = validatePlan(planInput);
  const home = issueHome(root, issue);
  const state = readState(home);
  const latest = state.runs?.at(-1);
  if (!latest || latest.status !== 'passed') throw new PolicyError('passing_evidence_required');
  if (latest.planHash !== planHash(validated.plan)) throw new PolicyError('plan_fingerprint_mismatch');
  const current = fingerprint(root);
  if (latest.fingerprint !== current) throw new PolicyError('evidence_fingerprint_mismatch');
  const head = git(root, ['rev-parse', 'HEAD']).trim();
  if (reviewedSha !== head) throw new PolicyError('reviewed_sha_must_equal_head');
  const reviewer = (reviewedBy || '').trim().toLowerCase();
  if (!reviewer.includes('@')) throw new PolicyError('reviewer_email_required');
  const authors = [gitEmail(root, ['log', '-1', '--format=%ae']), gitEmail(root, ['config', 'user.email'])].filter(Boolean);
  if (authors.includes(reviewer)) throw new PolicyError('implementer_cannot_self_attest');
  const dirty = git(root, ['status', '--porcelain', '--untracked-files=normal']).trim() !== '';
  const review = {
    issue,
    status: 'recorded',
    reviewedBy: reviewedBy.trim(),
    reviewedSha: head,
    fingerprint: current,
    dirty,
    snapshotOnly: dirty,
    merge: false,
    deploy: false,
    recordedAt: iso(),
  };
  writeJson(path.join(home, 'review.json'), review);
  return review;
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function throwsCode(fn, code) {
  try {
    fn();
  } catch (error) {
    assert(error instanceof PolicyError && error.code === code, `expected ${code}, got ${error.code || error.message}`);
    return;
  }
  throw new Error(`expected ${code}`);
}

function selfTest() {
  const content = {
    title: 'Update one post',
    acceptance: 'The post renders and the repository checks pass.',
    allowedPaths: ['src/content/blog/**'],
    checks: ['site', 'stats', 'release'],
  };
  const accepted = validatePlan(content);
  assert(accepted.checks.join() === 'release,stats,site', 'content plan checks');
  throwsCode(() => validatePlan({ ...content, extraField: [] }), 'plan_fields_must_be_title_acceptance_allowedPaths_checks');
  throwsCode(
    () => validatePlan({ ...content, checks: ['site', 'stats', 'release', 'not-a-blog-check'] }),
    'unknown_checks',
  );
  throwsCode(() => validatePlan({ ...content, checks: ['site', 'release'] }), 'missing_checks');
  throwsCode(() => validatePlan({ ...content, allowedPaths: ['../outside'] }), 'invalid_paths');
  throwsCode(() => validatePlan({ ...content, allowedPaths: ['C:/Windows/system'] }), 'invalid_paths');
  const withMotion = validatePlan({
    ...content,
    title: 'Adjust motion',
    allowedPaths: ['src/**'],
    checks: ['site', 'stats', 'release', 'motion'],
  });
  assert(withMotion.checks.includes('motion'), 'motion required for src');
  throwsCode(
    () => validatePlan({ ...content, allowedPaths: ['src/**'], checks: ['site', 'stats', 'release'] }),
    'missing_checks',
  );

  const example = validatePlan(extractPlan('<!-- workflow-plan\n' + JSON.stringify(content) + '\n-->'));
  assert(example.checks.join() === 'release,stats,site', 'embedded example plan');
  const workflowFile = path.join(ROOT, 'WORKFLOW.md');
  if (existsSync(workflowFile)) {
    const workflowExample = validatePlan(extractPlan(readFileSync(workflowFile, 'utf8')));
    assert(workflowExample.checks.join() === 'release,stats,site', 'workflow example plan');
  }
  assert(profileSpec(ROOT, 'site').source === 'npm run build', 'site command');
  assert(profileSpec(ROOT, 'stats').source === 'go test ./...', 'stats command');
  assert(profileSpec(ROOT, 'release').source === 'bash scripts/test-deploy-release.sh', 'release command');
  assert(profileSpec(ROOT, 'motion').source === 'node --test scripts/test-motion.mjs', 'motion command');

  const repo = path.join(tmpdir(), `workflow-verify-${process.pid}`);
  rmSync(repo, { recursive: true, force: true });
  mkdirSync(repo);
  git(repo, ['init', '-q']);
  git(repo, ['config', 'user.email', 'author@example.com']);
  git(repo, ['config', 'user.name', 'Author']);
  writeFileSync(path.join(repo, '.gitignore'), '.local/\n');
  writeFileSync(path.join(repo, 'README.md'), 'one\n');
  git(repo, ['add', '.gitignore', 'README.md']);
  git(repo, ['-c', 'commit.gpgsign=false', 'commit', '-q', '-m', 'init']);
  const first = fingerprint(repo);
  git(repo, ['config', 'core.autocrlf', 'true']);
  writeFileSync(path.join(repo, 'README.md'), 'one\r\n');
  assert(fingerprint(repo) === first, 'line ending conversion keeps fingerprint');
  writeFileSync(path.join(repo, 'README.md'), 'two\n');
  assert(fingerprint(repo) !== first, 'fingerprint tracks file bytes');
  const restored = 'one\n';
  writeFileSync(path.join(repo, 'README.md'), restored);
  assert(fingerprint(repo) === first, 'fingerprint is stable');

  const hash = planHash(accepted.plan);
  assert(evaluateAttempt({ runs: [] }, first, hash) === 'execute', 'first run executes');
  assert(
    evaluateAttempt({ runs: [{ status: 'passed', fingerprint: first, planHash: hash }] }, first, hash) === 'reuse',
    'same pass is reused',
  );
  throwsCode(
    () => evaluateAttempt({ runs: [{ status: 'failed', fingerprint: first }] }, first, hash),
    'unchanged_failed_submission',
  );
  assert(
    evaluateAttempt(
      { runs: [{ status: 'failed', fingerprint: 'older' }] },
      first,
      hash,
    ) === 'execute',
    'one repair is allowed',
  );
  assert(
      evaluateAttempt(
        {
          runs: [
            { status: 'failed', fingerprint: 'one' },
            { status: 'failed', fingerprint: 'two' },
          ],
        },
        first,
        hash,
      ) === 'execute',
    'changed submissions are budgeted by the external controller',
  );

  const home = issueHome(repo, 'proof');
  const passing = {
    runs: [
      {
        status: 'passed',
        fingerprint: fingerprint(repo),
        planHash: hash,
      },
    ],
  };
  writeJson(path.join(home, 'state.json'), passing);
  const head = git(repo, ['rev-parse', 'HEAD']).trim();
  throwsCode(() => reviewIssue(repo, 'proof', content, 'author@example.com', head), 'implementer_cannot_self_attest');
  throwsCode(() => reviewIssue(repo, 'proof', content, 'reviewer@example.com', '0'.repeat(40)), 'reviewed_sha_must_equal_head');
  writeJson(path.join(home, 'state.json'), { runs: [{ status: 'failed', fingerprint: fingerprint(repo), planHash: hash }] });
  throwsCode(() => reviewIssue(repo, 'proof', content, 'reviewer@example.com', head), 'passing_evidence_required');
  writeJson(path.join(home, 'state.json'), passing);
  writeFileSync(path.join(repo, 'README.md'), 'changed\n');
  throwsCode(() => reviewIssue(repo, 'proof', content, 'reviewer@example.com', head), 'evidence_fingerprint_mismatch');
  writeFileSync(path.join(repo, 'README.md'), restored);
  const review = reviewIssue(repo, 'proof', content, 'reviewer@example.com', head);
  assert(review.merge === false && review.deploy === false && review.reviewedSha === head, 'review does not approve merge');
  rmSync(repo, { recursive: true, force: true });
  process.stdout.write('self-test passed\n');
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const command = args._[0];
  if (command === 'self-test') {
    selfTest();
    return;
  }
  const root = ROOT;
  if (command === 'gate') {
    const input = args['issue-body'] ? extractPlan(readFileSync(args['issue-body'], 'utf8')) : readJson(args.plan);
    const validated = validatePlan(input);
    process.stdout.write(`${JSON.stringify({ status: 'passed', checks: validated.checks })}\n`);
    return;
  }
  const issue = assertIssue(args.issue);
  const plan = readJson(args.plan);
  if (command === 'check') {
    const run = await checkIssue(root, issue, plan);
    process.stdout.write(`${JSON.stringify({
      status: run.status,
      mode: run.mode || 'executed',
      reason: run.reason || '',
      fingerprint: run.fingerprint,
      evidence: path.relative(root, path.join(issueHome(root, issue), 'evidence.json')).replaceAll('\\', '/'),
    })}\n`);
    if (run.status !== 'passed') process.exitCode = 1;
    return;
  }
  if (command === 'review') {
    const review = reviewIssue(root, issue, plan, args['reviewed-by'], args['reviewed-sha']);
    process.stdout.write(`${JSON.stringify(review)}\n`);
    return;
  }
  throw new PolicyError('usage', 'gate|check|review|self-test');
}

const entry = process.argv[1] ? path.resolve(process.argv[1]) : '';
if (entry === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    process.stderr.write(`${error.message}\n`);
    process.exit(error instanceof PolicyError ? 2 : 1);
  });
}
