// audience: internal
// # inventory-presentation
// 展示组来自盘点核心. 搜索, 状态摘要与折叠关系均保留原始成员身份.


export const kindNames = { module: '模块', skill: 'Skill', hook: 'Hooks', tool: 'Tool', instruction: '说明文件', rule: '规则', mcp: 'MCP 工具', plugin: '插件来源', config: '宿主配置', component: '工具组件', source: '维护来源' };
export const categories = [
  { id: 'modules', name: '模块', members: ['module'], section: 'modules' },
  { id: 'library', name: '基础内容', shortName: '全部', members: ['rule', 'skill', 'hook', 'tool'], section: 'content' },
  { id: 'methods', name: 'Skills', members: ['skill'], section: 'content' },
  { id: 'rules', name: '规则', members: ['rule'], section: 'content' },
  { id: 'hooks', name: 'Hooks', members: ['hook'], section: 'content' },
  { id: 'tools', name: 'Tools', members: ['tool'], section: 'content' },
  { id: 'sources', name: '来源文件', members: ['plugin', 'source', 'instruction', 'config'], section: 'system' },
];

// //// 为同一盘点快照建立成员与展示组索引 [@x380kkm 2026-09-07] ////
export function inventoryModel(snapshot) {
  if (snapshot.items.length && !snapshot.groups) throw new Error('此快照需要更新聚合信息. 请在桌面应用中重新导出.');
  const normalized = snapshot.items.map((item) => snapshot.cardManagement?.[item.id] ? { ...item, management: snapshot.cardManagement[item.id] } : item);
  const items = new Map(normalized.map((item) => [item.id, item]));
  const groups = snapshot.groups || [];
  const byId = new Map(groups.map((group) => [group.id, group]));
  const itemGroups = new Map(groups.flatMap((group) => group.itemIds.map((id) => [id, group.id])));
  const searchText = new Map(groups.map((group) => [group.id, [group.name,
    ...group.itemIds.flatMap((id) => { const item = items.get(id); return item ? [item.name, item.path, item.summary, item.kind === 'rule' ? item.content : ''] : []; }),
  ].join(' ').toLocaleLowerCase()]));
  const categoryGroups = new Map(categories.map((category) => [category.id, groups.filter((group) => category.members.includes(group.category)
    || ['methods', 'library'].includes(category.id) && group.counts.skill > 0
    || ['tools', 'library'].includes(category.id) && group.counts.mcp > 0)]));
  const categoryIds = new Map([...categoryGroups].map(([id, entries]) => [id, new Set(entries.map((group) => group.id))]));
  const categoryCounts = new Map([...categoryGroups].map(([id, entries]) => [id, entries.filter((group) => group.origin !== 'official').length]));
  return { snapshot: { ...snapshot, items: normalized }, items, groups, byId, itemGroups, searchText, categoryGroups, categoryIds, categoryCounts };
}

// //// 取得展示组的原始成员 [@x380kkm 2026-09-07] ////
export function groupItems(model, group) {
  return group.itemIds.map((id) => model.items.get(id)).filter(Boolean);
}

// //// 匹配组名称和其全部来源内容 [@x380kkm 2026-09-07] ////
export function matchesGroup(model, group, category, query) {
  if (model.categoryIds.has(category) && !model.categoryIds.get(category).has(group.id)) return false;
  const terms = Array.isArray(query) ? query : query.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
  if (!terms.length) return true;
  const text = model.searchText.get(group.id) || '';
  return terms.every((term) => text.includes(term));
}

// //// 按用途概括组内内容而保留各版本记录 [@x380kkm 2026-09-07] ////
export function groupSummary(model, group) {
  const counts = group.counts;
  if (group.category === 'module') {
    const members = model.items.get(group.primaryId)?.details?.memberCounts || {};
    return Object.entries(members).map(([kind, count]) => `${count} ${kindNames[kind] || kind}`).join(' / ');
  }
  if (group.category === 'rule') {
    return '规则';
  }
  if (group.category === 'tool') {
    const item = model.items.get(group.primaryId);
    return [item?.details?.transport, item?.details?.endpointScope].filter(Boolean).join(' / ') || 'MCP';
  }
  if (group.category === 'plugin') {
    const versions = groupItems(model, group).filter((item) => item.details?.observation === '缓存版本');
    return [counts.skill ? `${counts.skill} Skill` : '', versions.length ? `${versions.length} 个缓存版本` : '插件'].filter(Boolean).join(' · ');
  }
  if (['config', 'hook'].includes(group.category)) {
    const details = model.items.get(group.primaryId)?.details || {};
    if (details.model) return [details.model, details.model_reasoning_effort].filter(Boolean).join(' · ');
    if (details.handlerCount !== undefined) return `${details.handlerCount} 个处理项`;
    return '宿主设置';
  }
  if (group.itemIds.length === 1) return kindNames[model.items.get(group.primaryId)?.kind] || '';
  const labels = { skill: 'Skill', component: '组件', instruction: '文件', rule: '规则', source: '来源', config: '配置' };
  return Object.entries(labels).filter(([kind]) => counts[kind]).map(([kind, label]) => `${counts[kind]} ${label}`).join(' · ');
}

// //// 展示配置开关和登记事实而保留运行状态在详情中 [@x380kkm 2026-09-07] ////
export function groupStatus(model, group) {
  const members = groupItems(model, group);
  if (group.category === 'connection') return model.items.get(group.primaryId)?.status?.split(';')[0] || '';
  const flags = members.flatMap((item) => [item.details?.enabled, item.details?.configuredEnabled]).filter((value) => typeof value === 'boolean');
  const active = new Set(flags);
  if (active.size > 1) return '开关不同';
  if (active.has(false)) return '配置停用';
  if (active.has(true)) return '配置启用';
  if (members.some((item) => item.managedIds?.length)) return '已收录';
  if (group.category === 'plugin') return members.some((item) => item.details?.observation === '安装记录') ? '有安装记录' : '缓存';
  return '';
}

// //// 区分组内同名来源与缓存位置 [@x380kkm 2026-09-07] ////
export function memberLabel(model, group, item) {
  const duplicates = groupItems(model, group).filter((candidate) => candidate.kind === item.kind && candidate.name === item.name);
  const path = item.path.replaceAll('\\', '/').split('/').filter(Boolean);
  const plugin = model.items.get(item.details?.pluginId);
  let suffix = '';
  if (duplicates.length > 1) {
    suffix = item.details?.versionDirectory || plugin?.details?.versionDirectory || '';
    if (!suffix) {
      for (let length = 2; length <= path.length; length += 1) {
        suffix = path.slice(-length).join('/');
        if (duplicates.every((candidate) => candidate.id === item.id || candidate.path.replaceAll('\\', '/').split('/').filter(Boolean).slice(-length).join('/') !== suffix)) break;
      }
    }
  }
  if (item.kind === 'source') return suffix || item.name;
  return [item.name, item.kind === 'plugin' ? item.details?.observation : '', suffix].filter(Boolean).join(' / ');
}
