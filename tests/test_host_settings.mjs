// audience: internal
// # host-settings-tests
// 接管关闭时保留文件预览, 应用与恢复分别遵循控制状态.

import assert from 'node:assert/strict';
import test from 'node:test';
import { rendererRuntime } from './renderer-runtime.mjs';
import { createHostSettings } from '../desktop/renderer/host-settings.mjs';

// //// 暂停期间预览文件而保留显式恢复入口 [@x380kkm 2026-09-10] ////
test('暂停接管可以预览变化, 普通应用保持禁用且恢复可用', async (context) => {
  const calls = [];
  const file = { operation: 'replace', name: 'hooks.json', beforeBytes: 1, afterBytes: 2, diff: '-old\n+new' };
  rendererRuntime(context, async (method, params) => {
    calls.push({ method, params });
    if (method === 'host.status') return { enabled: false, initialized: true, targetRoot: '/host', backupRoot: '/backups',
      baseline: {}, backups: [{ id: 'initial', files: ['hooks.json'], createdAt: '2026-09-10' }] };
    if (method === 'host.preview') return { planId: 'apply-plan', files: [file], diagnostics: [] };
    if (method === 'host.preview_restore') return { planId: 'restore-plan', backupId: params.id, files: [file], diagnostics: [] };
    if (method === 'host.apply') return { enabled: false, initialized: true, targetRoot: '/host', backupRoot: '/backups', baseline: {}, backups: [] };
    throw new Error(method);
  });
  let pending;
  const settings = createHostSettings({ run: (action) => { pending = action(); return pending; },
    onSaved: async () => {}, onSource() {}, setStatus() {} });
  await settings.open();
  const dialog = document.body.querySelector('.host-settings-dialog');
  const preview = dialog.querySelectorAll('button').find((button) => button.textContent === '预览待应用设置');
  assert.equal(preview.disabled, false);
  await preview.emit('click'); await pending;
  const apply = dialog.querySelectorAll('button').find((button) => button.textContent === '应用到文件');
  assert.equal(apply.disabled, true);
  assert.ok(dialog.querySelectorAll('pre').some((node) => node.textContent === file.diff));
  await apply.emit('click'); await pending;
  assert.ok(calls.every((call) => !['host.apply', 'host.set_enabled'].includes(call.method)));
  const restore = dialog.querySelectorAll('button').find((button) => button.textContent === '预览恢复');
  await restore.emit('click'); await pending;
  const confirm = dialog.querySelectorAll('button').find((button) => button.textContent === '恢复所选备份');
  assert.equal(confirm.disabled, false);
  await confirm.emit('click'); await pending;
  assert.deepEqual(calls.filter((call) => call.method === 'host.apply').map((call) => call.params), [{ plan_id: 'restore-plan' }]);
});

// //// 保存回执更新控制基线并独立呈现卡片刷新错误 [@x380kkm 2026-09-10] ////
test('卡片刷新失败后接管开关沿用保存回执中的新基线', async (context) => {
  const calls = [];
  let enabled = false, revision = 1;
  const status = () => ({ enabled, initialized: true, targetRoot: '/host', backupRoot: '/backups',
    baseline: { revision }, backups: [] });
  rendererRuntime(context, async (method, params) => {
    calls.push({ method, params });
    if (method === 'host.status') return status();
    if (method === 'host.set_enabled') {
      assert.equal(params.baseline.revision, revision);
      enabled = params.enabled; revision += 1;
      return { ...status(), hostSync: { message: enabled ? '接管已开启.' : '接管已关闭.' } };
    }
    throw new Error(method);
  });
  let pending;
  const settings = createHostSettings({ run: (action) => { pending = action(); return pending; },
    onSaved: async () => { throw new Error('卡片读取失败.'); }, onSource() {}, setStatus() {} });
  await settings.open();
  for (const value of [true, false]) {
    const toggle = document.body.querySelector('[aria-label="开启配置接管"]');
    toggle.checked = value; await toggle.emit('change'); await pending;
    assert.equal(document.body.querySelector('[aria-label="开启配置接管"]').checked, value);
    assert.match(document.body.querySelector('.settings-feedback').textContent, /刷新目录失败: 卡片读取失败/);
  }
  assert.deepEqual(calls.filter((call) => call.method === 'host.set_enabled').map((call) => call.params.baseline.revision), [1, 2]);
  assert.equal(calls.filter((call) => call.method === 'host.status').length, 1);
});

// //// 恢复回执在刷新失败时仍确定接管状态与后续操作基线 [@x380kkm 2026-09-10] ////
test('恢复后卡片刷新失败仍显示关闭且可重新操作接管', async (context) => {
  let enabled = true, revision = 1, pending;
  const status = () => ({ enabled, initialized: true, targetRoot: '/host', backupRoot: '/backups',
    baseline: { revision }, backups: [{ id: 'initial', files: ['hooks.json'], createdAt: '2026-09-10' }] });
  rendererRuntime(context, async (method, params) => {
    if (method === 'host.status') return status();
    if (method === 'host.preview_restore') return { planId: 'restore', backupId: params.id, files: [], diagnostics: [] };
    if (method === 'host.apply') { enabled = false; revision += 1; return status(); }
    if (method === 'host.set_enabled') {
      assert.equal(params.baseline.revision, revision);
      enabled = params.enabled; revision += 1;
      return { ...status(), hostSync: { message: '接管已开启.' } };
    }
    throw new Error(method);
  });
  const settings = createHostSettings({ run: (action) => { pending = action(); return pending; },
    onSaved: async () => { throw new Error('卡片读取失败.'); }, onSource() {}, setStatus() {} });
  await settings.open();
  const dialog = document.body.querySelector('.host-settings-dialog');
  await dialog.querySelectorAll('button').find((button) => button.textContent === '预览恢复').emit('click'); await pending;
  await dialog.querySelectorAll('button').find((button) => button.textContent === '恢复所选备份').emit('click'); await pending;
  const toggle = dialog.querySelector('[aria-label="开启配置接管"]');
  assert.equal(toggle.checked, false);
  assert.match(dialog.querySelector('.settings-feedback').textContent, /所选备份已恢复.*刷新目录失败/);
  toggle.checked = true; await toggle.emit('change'); await pending;
  assert.equal(dialog.querySelector('[aria-label="开启配置接管"]').checked, true);
});

// //// 重新关联成功后消费计划并保留独立的读取反馈 [@x380kkm 2026-09-10] ////
test('重新关联后状态读取失败保留成功结果且无法重复提交原计划', async (context) => {
  let saved = false, pending;
  const calls = [];
  rendererRuntime(context, async (method, params) => {
    calls.push({ method, params });
    if (method === 'host.status') {
      if (saved) throw new Error('状态读取失败.');
      return { enabled: false, initialized: true, targetRoot: '/project/.codex', backupRoot: '/backups', baseline: {}, backups: [] };
    }
    if (method === 'project.relocate_preview') return {
      plan: { oldWorkspace: '/old', workspace: '/project', sourceIds: [] }, warnings: [], sourceCandidates: [],
      changes: [{ scope: 'project-local', catalog: '/catalog', documents: [] }] };
    if (method === 'project.relocate_apply') { saved = true; return { changed: true, hostApplyRequired: ['project-local'] }; }
    throw new Error(method);
  });
  const settings = createHostSettings({ run: (action) => { pending = action(); return pending; },
    onSaved: async () => {}, onSource() {}, setStatus() {} });
  await settings.open('project-local');
  const dialog = document.body.querySelector('.host-settings-dialog');
  dialog.querySelector('[aria-label="项目原位置"]').value = '/old';
  await dialog.querySelectorAll('button').find((button) => button.textContent === '预览重新关联').emit('click'); await pending;
  const confirm = dialog.querySelectorAll('button').find((button) => button.textContent === '确认重新关联');
  await confirm.emit('click'); await pending;
  assert.equal(confirm.hidden, true);
  assert.equal(confirm.disabled, true);
  assert.match(dialog.querySelector('.settings-feedback').textContent, /项目配置已重新关联.*刷新目录失败: 状态读取失败/);
  await confirm.emit('click'); await pending;
  assert.equal(calls.filter((call) => call.method === 'project.relocate_apply').length, 1);
});
