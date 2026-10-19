// audience: internal
// # inventory-model-tests
// 聚合查询保留隐藏来源, 原始成员与配置状态分别表达.

import assert from 'node:assert/strict';
import test from 'node:test';
import { groupStatus, inventoryModel, matchesGroup, memberLabel } from '../desktop/renderer/inventory-model.mjs';
import { cardSubject } from '../desktop/renderer/card-controls.mjs';

// //// 规则与 Skill 共享浏览范围且聚合入口保留明确选择 [@x380kkm 2026-09-07] ////
test('完整规则与 Skill 位于同一默认范围, 多 Skill 组逐项选择', () => {
  const items = [{ id: 'rule', kind: 'rule', name: 'PowerShell 使用规范', summary: '转义与编码', path: '/rules' },
    { id: 'skill', kind: 'skill', name: 'analysis', path: '/analysis' }];
  const groups = items.map((item) => ({ id: `g:${item.id}`, primaryId: item.id, itemIds: [item.id], category: item.kind, name: item.name, counts: { [item.kind]: 1 } }));
  const model = inventoryModel({ items, groups, edges: [] });
  assert.ok(groups.every((group) => matchesGroup(model, group, 'library', '')));
  assert.equal(cardSubject(model, groups[0]).id, 'rule');
  assert.equal(cardSubject(model, { itemIds: ['rule', 'skill'] }), null);
});

// //// 查询折叠组内的名称并保留来源归属 [@x380kkm 2026-09-07] ////
test('折叠来源参与搜索并保留自身配置状态', () => {
  const items = [
    { id: 'local-skill', name: 'documents', kind: 'skill', path: 'user/skills/documents/SKILL.md', summary: 'Personal method.', details: {} },
    { id: 'local-component', name: 'Tool', kind: 'component', path: 'components.json', details: {} },
    { id: 'package', name: 'documents', kind: 'plugin', path: 'cache/plugin.json', details: { enabled: false } },
    { id: 'cached-skill', name: 'memo-template', kind: 'skill', path: 'cache/skills/memo/SKILL.md', summary: 'Official template.', details: {} },
  ];
  const groups = [
    { id: 'mine', name: 'documents', origin: 'user', category: 'skill', itemIds: ['local-skill', 'local-component'], primaryId: 'local-component', counts: { skill: 1, component: 1 } },
    { id: 'official', name: 'documents', origin: 'official', category: 'plugin', itemIds: ['package', 'cached-skill'], primaryId: 'package', counts: { plugin: 1, skill: 1 } },
  ];
  const model = inventoryModel({ items, groups, edges: [
    { from: 'local-component', to: 'local-skill', label: 'contains' },
    { from: 'local-skill', to: 'cached-skill', label: 'same-name' },
  ] });
  assert.equal(matchesGroup(model, groups[1], '', 'memo-template'), true);
  assert.equal(matchesGroup(model, groups[1], 'methods', 'memo-template'), true);
  assert.equal(matchesGroup(model, groups[0], '', 'memo-template'), false);
  assert.equal(groupStatus(model, groups[1]), '配置停用');
});

// //// 同名维护来源在组内保留不同位置 [@x380kkm 2026-09-07] ////
test('同名维护来源显示不同路径片段', () => {
  const items = [
    { id: 'tool', name: 'codegraph', kind: 'source', path: 'W:/toolupdate/codegraph', details: {} },
    { id: 'skill', name: 'codegraph', kind: 'source', path: 'W:/toolupdate/manager/skills/codegraph', details: {} },
  ];
  const group = { id: 'codegraph', origin: 'user', name: 'CodeGraph', category: 'capability', itemIds: ['tool', 'skill'], primaryId: 'tool', counts: { source: 2 } };
  const model = inventoryModel({ items, groups: [group], edges: [] });
  assert.equal(memberLabel(model, group, items[0]), 'toolupdate/codegraph');
  assert.equal(memberLabel(model, group, items[1]), 'skills/codegraph');
});

// //// 同名缓存 Skill 从所属插件取得版本位置 [@x380kkm 2026-09-07] ////
test('同名 Skill 在多份缓存中保留可辨别的版本标签', () => {
  const items = ['2026.9', 'latest'].flatMap((version) => [
    { id: `plugin-${version}`, kind: 'plugin', name: 'chrome', path: `cache/chrome/${version}/plugin.json`, details: { versionDirectory: version } },
    { id: `skill-${version}`, kind: 'skill', name: 'control-chrome', path: `cache/chrome/${version}/skills/control-chrome/SKILL.md`, details: { pluginId: `plugin-${version}` } },
  ]);
  const group = { id: 'chrome', origin: 'official', name: 'chrome', category: 'plugin', itemIds: items.map((item) => item.id), primaryId: items[0].id, counts: { plugin: 2, skill: 2 } };
  const model = inventoryModel({ items, groups: [group], edges: [] });
  assert.equal(memberLabel(model, group, items[1]), 'control-chrome / 2026.9');
  assert.equal(memberLabel(model, group, items[3]), 'control-chrome / latest');
});

// //// 搜索沿用快照索引并在来源变化时重新取词 [@x380kkm 2026-09-07] ////
test('连续查询复用正文索引, 新快照刷新关键词与分类计数', () => {
  let reads = 0;
  const item = { id: 'rule', kind: 'rule', name: '编码规则', get content() { reads += 1; return 'PowerShell UTF8'; } };
  const group = { id: 'g:rule', name: item.name, itemIds: [item.id], category: 'rule', origin: 'user', counts: { rule: 1 } };
  const model = inventoryModel({ items: [item], groups: [group], edges: [] });
  const indexed = reads;
  assert.ok(matchesGroup(model, group, 'library', 'powershell utf8'));
  assert.ok(matchesGroup(model, group, 'rules', 'utf8'));
  assert.equal(reads, indexed);
  assert.equal(model.categoryCounts.get('rules'), 1);
  const updated = inventoryModel({ items: [{ ...item, content: 'UTF16' }], groups: [group], edges: [] });
  assert.equal(matchesGroup(updated, group, 'rules', 'utf8'), false);
  assert.equal(matchesGroup(updated, group, 'rules', 'utf16'), true);
});
