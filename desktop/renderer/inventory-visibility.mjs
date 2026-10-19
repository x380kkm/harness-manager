// audience: internal
// # inventory-visibility
// 显示筛选依据明确停用声明. 缺少启用观察的来源保留可见, 筛选结果同时供卡片和列表使用.

import { groupItems, inventoryModel } from './inventory-model.mjs';

const visibilityCache = new WeakMap();

// //// 读取内容自身及所属插件的明确停用状态 [@x380kkm 2026-09-07] ////
export function itemDisabled(item) {
  if (typeof item.management?.effectiveEnabled === 'boolean') return item.management.effectiveEnabled === false;
  const details = item.details || {};
  return details.pluginConfiguredEnabled === false || details.configuredEnabled === false || details.enabled === false || item.status === '配置禁用';
}

// //// 判断一张卡片的可用入口是否均已明确停用 [@x380kkm 2026-09-07] ////
export function groupDisabled(model, group) {
  const members = groupItems(model, group);
  if (group.category === 'module' && members.some((item) => item.details?.configuredState === 'disabled')) return true;
  const managed = members.filter((item) => item.management?.supported);
  if (managed.some((item) => item.management.effectiveEnabled === true)) return false;
  if (managed.length && managed.every((item) => item.management.effectiveEnabled === false)) return true;
  if (group.category === 'plugin') {
    const configured = members.filter((item) => item.kind === 'plugin' && item.details?.observation === '配置开关');
    if (configured.length) return configured.every((item) => item.details.enabled === false);
    const cached = members.filter((item) => item.kind === 'plugin' && item.details?.configuredEnabled !== undefined);
    if (cached.length && cached.every(itemDisabled)) return true;
  }
  if (!['skill', 'rule', 'hook', 'tool', 'plugin', 'connection'].includes(group.category)) return false;
  const entries = members.filter((item) => ['skill', 'rule', 'hook', 'mcp'].includes(item.kind));
  return entries.length > 0 && entries.every(itemDisabled);
}

// //// 在同一可见快照中移除停用卡片及隐藏成员的关系 [@x380kkm 2026-09-07] ////
export function visibleInventory(model, showDisabled) {
  if (showDisabled) return model;
  const cached = visibilityCache.get(model);
  if (cached) return cached;
  const groups = [];
  const visible = new Set();
  for (const group of model.groups) {
    if (groupDisabled(model, group)) continue;
    const itemIds = group.itemIds.filter((id) => !itemDisabled(model.items.get(id)));
    if (!itemIds.length) continue;
    const counts = {};
    for (const id of itemIds) { visible.add(id); const kind = model.items.get(id).kind; counts[kind] = (counts[kind] || 0) + 1; }
    groups.push({ ...group, primaryId: itemIds.includes(group.primaryId) ? group.primaryId : itemIds[0], itemIds, counts });
  }
  const result = inventoryModel({ ...model.snapshot, groups, items: model.snapshot.items.filter((item) => visible.has(item.id)),
    edges: model.snapshot.edges.filter((edge) => visible.has(edge.from) && visible.has(edge.to)) });
  visibilityCache.set(model, result);
  return result;
}
