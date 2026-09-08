// audience: internal
// # inventory-visibility-tests
// 卡片停用筛选同时约束成员与依赖边, 工具图标的分类保持用途可辨认.

import assert from 'node:assert/strict';
import test from 'node:test';
import { inventoryModel } from '../desktop/renderer/inventory-model.mjs';
import { groupDisabled, visibleInventory } from '../desktop/renderer/inventory-visibility.mjs';
import { toolIconName } from '../desktop/renderer/tool-icon.mjs';

// //// 创建含停用文件、共享插件与独立来源的观察样本 [@x380kkm 2026-09-07] ////
function model() {
  const items = [
    { id: 'codegraph', name: 'CodeGraph', kind: 'skill', details: {} },
    { id: 'writer', name: 'CN writing', kind: 'skill', details: { configuredEnabled: false } },
    { id: 'plugin', name: 'official', kind: 'plugin', details: { observation: '配置开关', enabled: true } },
    { id: 'included', name: 'Included', kind: 'skill', details: { pluginConfiguredEnabled: true } },
    { id: 'closed', name: 'Closed', kind: 'skill', details: { configuredEnabled: false, pluginConfiguredEnabled: true } },
    { id: 'disabled-plugin', name: 'chrome', kind: 'plugin', details: { observation: '配置开关', enabled: false } },
    { id: 'disabled-tool', name: 'chrome skill', kind: 'skill', details: { pluginConfiguredEnabled: false, configuredEnabled: true } },
  ];
  const group = (id, ids, category = 'skill', origin = 'user') => ({ id, name: id, category, origin, itemIds: ids, primaryId: ids[0], counts: {} });
  return inventoryModel({ items, groups: [group('code', ['codegraph']), group('writing', ['writer']), group('official', ['plugin', 'included', 'closed'], 'plugin', 'official'), group('chrome', ['disabled-plugin', 'disabled-tool'], 'plugin', 'official')],
    edges: [{ from: 'writer', to: 'codegraph', label: '依赖' }, { from: 'codegraph', to: 'included', label: '可选配合' },
      { from: 'codegraph', to: 'included', label: '同名 Skill, 来源分别维护' }, { from: 'closed', to: 'codegraph', label: '依赖' }] });
}

// //// 停用内容的关联记录保持相同可见范围 [@x380kkm 2026-09-07] ////
test('隐藏停用入口且保持未知状态可见', () => {
  const raw = model(), shown = visibleInventory(raw, false);
  assert.deepEqual(shown.groups.map((group) => group.id), ['code', 'official']);
  assert.equal(shown.items.has('closed'), false);
  assert.equal(shown.items.has('disabled-tool'), false);
  assert.equal(groupDisabled(raw, raw.byId.get('chrome')), true);
  assert.ok(shown.snapshot.edges.every((edge) => shown.items.has(edge.from) && shown.items.has(edge.to)));
  const restored = visibleInventory(raw, true);
  assert.equal(visibleInventory(raw, false), shown);
  assert.equal(restored.items.has('writer'), true);
  assert.equal(restored.snapshot.edges.some((edge) => edge.from === 'writer' && edge.to === 'codegraph'), true);
  const updated = inventoryModel({ ...raw.snapshot, cardManagement: { writer: { supported: true, effectiveEnabled: true } } });
  assert.equal(visibleInventory(updated, false).items.has('writer'), true);
});

// //// 同一工具的名称变体保持用途图标一致 [@x380kkm 2026-09-07] ////
test('工具图标体现关系、流程、写作与性能用途', () => {
  assert.equal(toolIconName('CodeGraph'), toolIconName('codegraph-skill'));
  assert.equal(toolIconName('Archify'), 'workflow');
  assert.equal(toolIconName('clean-writer-cn'), 'writing');
  assert.equal(toolIconName('matrix-insight'), 'performance');
  assert.notEqual(toolIconName('Archify'), toolIconName('CodeGraph'));
});
