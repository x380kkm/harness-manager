// 在隔离目录验证工作树, 宿主覆盖路径和会话时间边界.
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const scripts = fileURLToPath(new URL('../src/harness_manager/bundled/skills/agent-handoff/scripts/', import.meta.url));

function temporary(t) {
  const root = mkdtempSync(path.join(tmpdir(), 'harness-handoff-'));
  t.after(() => {
    assert.equal(path.dirname(root), path.resolve(tmpdir()));
    rmSync(root, { recursive: true, force: true });
  });
  return root;
}

function git(directory, ...args) {
  const result = spawnSync('git', ['-C', directory, ...args], { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
}

function resolve(project, args = [], env = {}) {
  const result = spawnSync(process.execPath, [path.join(scripts, 'resolve_paths.js'), '--project', project, ...args], {
    encoding: 'utf8', env: { ...process.env, ...env },
  });
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout);
}

test('关联工作树从子目录解析到同一主工作树', t => {
  const root = temporary(t);
  const main = path.join(root, '主项目 with spaces');
  const linked = path.join(root, '关联 worktree');
  mkdirSync(main);
  git(main, 'init');
  git(main, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '--allow-empty', '-m', 'fixture');
  git(main, 'worktree', 'add', '--detach', linked);
  const nested = path.join(linked, '子目录');
  mkdirSync(nested);
  const result = resolve(nested);
  assert.equal(realpathSync(result.projectRoot), realpathSync(linked));
  assert.equal(realpathSync(result.sharedRoot), realpathSync(main));
  assert.equal(result.handoffDir, path.join(result.sharedRoot, 'archive'));
  assert.equal(realpathSync(result.gitExcludeFile), realpathSync(path.join(main, '.git', 'info', 'exclude')));
  assert.equal(resolve(nested, ['--handoff-dir', 'private handoffs']).handoffDir, path.join(result.sharedRoot, 'private handoffs'));
});

test('独立 Git 元数据不被误当作交接目录', t => {
  const root = temporary(t);
  const main = path.join(root, '主项目');
  const metadata = path.join(root, 'git metadata');
  mkdirSync(main);
  git(main, 'init', '--separate-git-dir', metadata);
  const result = resolve(main);
  assert.equal(realpathSync(result.projectRoot), realpathSync(main));
  assert.equal(realpathSync(result.sharedRoot), realpathSync(main));
  assert.equal(realpathSync(result.gitExcludeFile), realpathSync(path.join(metadata, 'info', 'exclude')));
  assert.equal(result.warnings.length, 1);
});

test('宿主路径遵循明确覆盖, 环境变量和用户目录默认值', t => {
  const root = temporary(t);
  const codex = path.join(root, 'codex 自定义');
  const claude = path.join(root, 'claude 自定义');
  mkdirSync(path.join(codex, 'sessions'), { recursive: true });
  const env = { CODEX_HOME: codex, CLAUDE_CONFIG_DIR: claude };
  const result = resolve(root, [], env);
  assert.equal(result.gitExcludeFile, null);
  assert.equal(result.sharedRoot, root);
  assert.equal(result.codexSessions, path.join(codex, 'sessions'));
  assert.equal(result.claudeProjects, path.join(claude, 'projects'));
  assert.deepEqual(result.sessionDirectoriesExist, { codex: true, claude: false });
  const override = resolve(root, ['--codex-home', root, '--claude-home', root, '--handoff-dir', claude], env);
  assert.equal(override.codexSessions, path.join(root, 'sessions'));
  assert.equal(override.claudeProjects, path.join(root, 'projects'));
  assert.equal(override.handoffDir, claude);
  const defaults = resolve(root, [], { CODEX_HOME: '', CLAUDE_CONFIG_DIR: '' });
  assert.equal(defaults.codexSessions, path.join(homedir(), '.codex', 'sessions'));
  assert.equal(defaults.claudeProjects, path.join(homedir(), '.claude', 'projects'));
});

test('无效项目路径报告失败而非猜测位置', t => {
  const root = temporary(t);
  const result = spawnSync(process.execPath, [path.join(scripts, 'resolve_paths.js'), '--project', path.join(root, 'missing')], { encoding: 'utf8' });
  assert.equal(result.status, 1);
  assert.equal(result.stdout, '');
  assert.ok(result.stderr.length > 0);
});

test('会话摘取统一 UTC, 包含下界并排除上界', t => {
  const root = temporary(t);
  const session = path.join(root, 'session.jsonl');
  const entries = [
    { type: 'session_meta', payload: { cwd: root } },
    { timestamp: '2026-10-19T08:00:00+08:00', type: 'event_msg', payload: { type: 'user_message', message: '窗口起点' } },
    { timestamp: '2026-10-19T00:30:00Z', type: 'assistant', message: { content: [{ type: 'text', text: 'Claude 答复' }] } },
    { timestamp: 'invalid', type: 'event_msg', payload: { type: 'user_message', message: '无效时间' } },
    { timestamp: '2026-10-19T01:00:00Z', type: 'event_msg', payload: { type: 'user_message', message: '上界之外' } },
  ];
  writeFileSync(session, entries.map(entry => JSON.stringify(entry)).join('\n'), 'utf8');
  const result = spawnSync(process.execPath, [path.join(scripts, 'session_extract.js'), session, '2026-10-19T00:00', '2026-10-19T01:00'], {
    encoding: 'utf8', env: { ...process.env, TZ: 'America/New_York' },
  });
  assert.equal(result.status, 0, result.stderr);
  const extracted = JSON.parse(result.stdout);
  assert.equal(extracted.cwd, root);
  assert.deepEqual(extracted.userMessages, [{ at: '2026-10-19T00:00:00.000Z', text: '窗口起点' }]);
  assert.deepEqual(extracted.agentMessages, [{ at: '2026-10-19T00:30:00.000Z', text: 'Claude 答复' }]);
  assert.equal(extracted.timezone, 'UTC');
});
