// audience: internal
// # module-release-controls
// 模块版本的快捷操作保留当前绑定, 显式开启选择版本, 关闭与继承只修改所选版本.

import assert from 'node:assert/strict';
import test from 'node:test';
import { rendererRuntime } from './renderer-runtime.mjs';
import { createInventoryView } from '../desktop/renderer/inventory-view.mjs';

// //// 跨版本的启停和继承沿用原生宿主绑定且保留其他宿主 [@x380kkm 2026-09-10] ////
test('未选版本关闭保持原值, 显式开启切换版本并使用新基线', async (context) => {
  const plugin = 'plugin:module/versioned';
  const original = { id: 'binding:custom/codex', plugin: { id: plugin, constraint: '2.0.0', channel: 'preview' },
    enabled: true, target: { selector: { host: 'codex' } }, options: { mode: 'personal' } };
  const manager = { ...structuredClone(original), id: 'binding:custom/manager', target: { selector: { host: 'harness-manager' } } };
  let native = structuredClone(original), pending;
  const calls = [];
  const get = rendererRuntime(context, (method, params) => {
    calls.push({ method, params: structuredClone(params) });
    if (method === 'card.inventory') {
      const items = ['1.0.0', '2.0.0'].map((version) => ({ id: `module:${version}`, name: `模块 ${version}`, kind: 'module', path: '/catalog',
        details: { pluginId: plugin, documentId: `${plugin}@${version}`, members: [], memberCounts: {},
          configuredState: native?.plugin.constraint === version ? native.enabled ? 'enabled' : 'disabled' : 'inherit' } }));
      return { root: '/user', scannedAt: new Date().toISOString(), items, edges: [], groups: items.map((item) => ({
        id: `group:${item.id}`, primaryId: item.id, itemIds: [item.id], category: 'module', name: item.name, origin: 'user', counts: { module: 1 } })) };
    }
    if (method === 'usage.describe') return { plugin: params.plugin, release: { version: params.plugin.endsWith('@1.0.0') ? '1.0.0' : '2.0.0', channel: params.plugin.endsWith('@1.0.0') ? 'stable' : 'preview' },
      contextBindings: native ? [structuredClone(native)] : [], selectedPlugin: native ? `${plugin}@${native.plugin.constraint}` : null,
      bindings: [manager, ...(native ? [structuredClone(native)] : [])], suggestedId: 'binding:new/codex' };
    if (method === 'usage.preview') {
      assert.deepEqual(params.baseline, native);
      const after = structuredClone(params.baseline);
      after.enabled = params.settings.state === 'enabled';
      if (params.settings.constraint) after.plugin.constraint = params.settings.constraint;
      if ('channel' in params.settings) after.plugin.channel = params.settings.channel;
      return { plan: { after } };
    }
    if (method === 'document.preview_remove') { assert.deepEqual(params.baseline, native); return { plan: { id: params.id, after: null } }; }
    if (method === 'document.apply') { native = params.plan.after; return {}; }
    throw new Error(method);
  });
  const view = createInventoryView({ run: (action) => { pending = action(); return pending; }, setStatus() {}, onDirty() {} });
  await view.refresh();
  const versionControl = (version) => get('inventory-list').querySelectorAll('select')
    .find((control) => control.getAttribute('aria-label') === `模块 ${version} 的模块状态`);
  const choose = async (version, value) => {
    const control = versionControl(version);
    control.value = value; await control.emit('change'); await pending;
  };
  await choose('1.0.0', 'disabled'); await choose('1.0.0', 'inherit');
  assert.ok(calls.every((call) => !['usage.preview', 'document.preview_remove', 'document.apply'].includes(call.method)));
  assert.deepEqual(native, original);
  await choose('1.0.0', 'enabled');
  assert.deepEqual(native, { ...original, plugin: { id: plugin, constraint: '1.0.0', channel: 'stable' } });
  assert.equal(versionControl('2.0.0').children[0].textContent, '尚未开启');
  await choose('2.0.0', 'disabled');
  assert.equal(native.enabled, true);
  await choose('1.0.0', 'inherit');
  assert.equal(native, null);
  assert.equal(manager.plugin.constraint, '2.0.0');
  assert.deepEqual(calls.filter((call) => call.method === 'document.preview_remove').map((call) => call.params.id), [original.id]);
});
