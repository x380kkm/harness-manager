// audience: internal
// # inventory-inspector
// 详情以选定内容组和原始成员为入口, 读取正文与技术字段通过显式展开呈现.

import { textElement } from './view-utils.mjs';
import { groupItems, groupStatus, groupSummary, kindNames, memberLabel } from './inventory-model.mjs';
import { createToolIcon } from './tool-icon.mjs';
import { cardControls, cardSubject } from './card-controls.mjs';
import { markdownView } from './markdown-view.mjs';
import { moduleControls } from './module-controls.mjs';

const scopes = { user: '用户级', directory: '用户目录', codex: 'Codex 用户目录', plugin: '插件' };
const carriers = { 'context.x380kkm/instruction': '宿主规则文件', 'preference.x380kkm/method': '宿主规则文件',
  'context.x380kkm/task': '按来源条件附加的说明', 'skill.x380kkm/deployment': '独立 Skill 文件', 'hook.x380kkm/lifecycle': '宿主生命周期事件', 'tool.x380kkm/endpoint': '工具接口配置' };
const fields = {
  model: '模型', model_reasoning_effort: '思考强度', approval_policy: '审批', sandbox_mode: '文件访问',
  transport: '连接方式', endpointScope: '端点位置', configuredEnabled: '配置开关', enabled: '配置开关',
  version: '版本', owner: '维护方', handlerCount: '处理项',
};

// //// 创建内容与来源的键值列表 [@x380kkm 2026-09-07] ////
function properties(entries) {
  const list = textElement('dl', '', 'inventory-evidence');
  for (const [label, value] of entries) {
    if (value === undefined || value === null || value === '') continue;
    const rendered = typeof value === 'boolean' ? value ? '启用' : '停用' : typeof value === 'object' ? JSON.stringify(value) : String(value);
    list.append(textElement('dt', label), textElement('dd', rendered));
  }
  return list;
}

// //// 在用户展开时创建较长的原文或技术信息 [@x380kkm 2026-09-07] ////
function disclosure(label, content) {
  const details = textElement('details', '', 'inventory-disclosure');
  details.append(textElement('summary', label));
  let rendered = false;
  details.addEventListener('toggle', () => {
    if (details.open && !rendered) { details.append(content()); rendered = true; }
  });
  return details;
}

// //// 展示组内的维护入口和各自身份 [@x380kkm 2026-09-07] ////
function membersSection(model, group, actions) {
  const section = textElement('section', '', 'inventory-section');
  section.append(textElement('h3', `组成 / ${group.itemIds.length}`));
  const list = textElement('div', '', 'inventory-member-links');
  for (const member of groupItems(model, group)) {
    const button = textElement('button', '', 'inventory-member-link');
    button.append(textElement('span', kindNames[member.kind] || member.kind, 'inventory-member-kind'), textElement('span', memberLabel(model, group, member)));
    button.title = member.path;
    button.addEventListener('click', () => actions.onSelect(member.id));
    list.append(button);
  }
  section.append(list);
  return section;
}

// //// 用聚合目标展示跨组关系 [@x380kkm 2026-09-07] ////
function relationsSection(model, group, actions) {
  const related = new Map();
  for (const edge of model.snapshot.edges) {
    if (['包含章节', '包含条目', '标记覆盖', '包含标记区块'].includes(edge.label)) continue;
    const from = model.itemGroups.get(edge.from), to = model.itemGroups.get(edge.to);
    if (from === to || (from !== group.id && to !== group.id)) continue;
    const id = from === group.id ? to : from;
    if (model.byId.has(id)) related.set(id, model.byId.get(id));
  }
  if (!related.size) return null;
  return disclosure(`关联内容 / ${related.size}`, () => {
    const list = textElement('div', '', 'inventory-member-links');
    for (const entry of related.values()) {
      const button = textElement('button', entry.name);
      button.addEventListener('click', () => actions.onSelect(entry.id));
      list.append(button);
    }
    return list;
  });
}

// //// 呈现组或原始成员的可操作详情 [@x380kkm 2026-09-07] ////
export function renderInventoryInspector(container, model, selected, actions) {
  container.replaceChildren();
  const group = model.byId.get(selected) || model.byId.get(model.itemGroups.get(selected));
  if (!group) { container.hidden = true; return; }
  container.hidden = false;
  const member = model.items.get(selected);
  const subject = member && ['skill', 'rule', 'hook'].includes(member.kind) && member.management?.supported !== false ? member : cardSubject(model, group);
  const item = member || subject || model.items.get(group.primaryId);
  const heading = textElement('div', '', 'inspector-heading');
  const close = textElement('button', '返回卡片', 'text-button');
  close.setAttribute('aria-label', '返回卡片');
  close.addEventListener('click', actions.onClose);
  heading.append(close);
  const identity = textElement('div', '', 'inspector-identity');
  identity.append(createToolIcon(member?.name || group.name, member?.kind || group.category));
  const labels = textElement('div', '', 'inspector-labels');
  if (member && member.name !== group.name) {
    const parent = textElement('button', group.name, 'text-button inventory-breadcrumb');
    parent.addEventListener('click', () => actions.onSelect(group.id));
    labels.append(parent);
  } else labels.append(textElement('span', group.origin === 'official' ? '官方与内置' : groupSummary(model, group), 'eyebrow'));
  labels.append(textElement('h2', member ? member.name : group.name));
  identity.append(labels); heading.append(identity);
  container.append(heading);
  const overview = textElement('section', '', 'inventory-section');
  if (item?.kind === 'module') {
    overview.append(moduleControls(item, actions));
    const composition = textElement('section', '', 'inventory-section'); composition.append(textElement('h3', '成员与载体'));
    for (const member of item.details.members || []) {
      const row = textElement('article', '', 'module-member-row');
      row.append(textElement('span', kindNames[member.kind] || member.kind, 'kind-badge'), textElement('h3', member.name));
      if (member.role) row.append(textElement('p', member.role));
      row.append(properties([['载体', member.carrier || carriers[member.point] || '需要载体适配'], ['版本', member.version]]));
      row.append(disclosure('来源引用', () => textElement('pre', member.originalRef || member.reference || '')));
      if (member.cardId && model.items.has(member.cardId)) {
        const open = textElement('button', '查看独立内容'); open.addEventListener('click', () => actions.onSelect(member.cardId)); row.append(open);
      }
      if (!member.resolved) row.append(textElement('p', '此成员来源需要确认.', 'diagnostic'));
      composition.append(row);
    }
    container.append(overview, composition);
    return;
  }
  if (item) {
    const entries = Object.entries(fields).filter(([key]) => item.details?.[key] !== undefined).map(([key, label]) => [label, item.details[key]]);
    if (member && item.kind !== 'rule') entries.unshift(['类型', kindNames[item.kind]], ['范围', scopes[item.scope] || item.scope], ['位置', `${item.path}${item.line ? `:${item.line}` : ''}`]);
    else if (item.kind !== 'rule' && groupStatus(model, group)) entries.unshift(['状态', groupStatus(model, group)]);
    if (entries.length) overview.append(properties(entries));
  }
  if (subject) {
    overview.append(textElement('h3', '使用范围'), cardControls(subject, actions));
    const configured = subject.management?.effectiveEnabled;
    overview.append(textElement('p', configured === undefined || configured === null
      ? '此项使用设置尚未由管理器覆盖. 宿主继续读取原配置.'
      : `已保存${configured ? '开启' : '关闭'}设置. 实际文件状态见系统设置中的应用结果.`, 'card-hint'));
    if (item.kind === 'hook') overview.append(textElement('p', '事件处理项由宿主执行. 新增或修改后, 按宿主要求审阅并信任.', 'card-hint'));
  }
  if (item?.kind === 'hook' && !subject) overview.append(textElement('p', '这是宿主已有的 Hooks 文件. 可在 Hooks 页面新建独立事件配置, 或由 Agent 协助整理后加入模块.', 'card-hint'));
  if (overview.childNodes.length) container.append(overview);
  if (!member && group.itemIds.length > 1 && !subject) container.append(membersSection(model, group, actions));
  if (subject?.management?.contents?.length > 1) {
    const composition = textElement('section', '', 'inventory-section');
    composition.append(textElement('h3', '包含内容'));
    for (const content of subject.management.contents) composition.append(properties([['名称', content.name], ['内容类型', content.point || '引用内容'], ['引用', content.ref]]));
    container.append(composition);
  }
  if (item) {
    const source = textElement('section', '', 'inventory-section');
    if (!member && group.itemIds.length === 1 && item.kind !== 'rule') source.append(properties([['范围', scopes[item.scope] || item.scope], ['位置', item.path]]));
    if (item.kind === 'rule') {
      source.append(textElement('h3', '规则正文'), markdownView(item.content));
      if (!actions.readOnly && actions.scope === 'user' && item.details?.catalogScope === 'user') {
        const edit = textElement('button', '编辑规则正文');
        edit.addEventListener('click', () => actions.onEditRule(item)); source.append(edit);
      }
      source.append(disclosure('原始文本', () => textElement('pre', item.content || '')));
    }
    else if (item.kind === 'hook' && typeof item.content === 'string') {
      source.append(textElement('h3', '生命周期配置'), properties([['事件', item.details?.event], ['匹配条件', item.details?.matcher]]), textElement('pre', item.content));
      if (!actions.readOnly && item.details?.catalogScope === actions.scope && item.management?.supported) {
        const edit = textElement('button', '编辑 Hook'); edit.addEventListener('click', () => actions.onEditHook(item)); source.append(edit);
      }
    } else if (item.kind === 'skill' && typeof item.content === 'string') {
      source.append(markdownView(item.content.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n/, '')));
      source.append(disclosure('原始文本', () => textElement('pre', item.content)));
    } else if (typeof item.content === 'string') source.append(disclosure('原文', () => textElement('pre', item.content)));
    const provenance = disclosure('来源与定位', () => {
      const details = document.createElement('div');
      if (item.summary) details.append(textElement('p', item.summary));
      details.append(properties([['观察', item.status], ['范围', scopes[item.scope] || item.scope], ['位置', `${item.path}:${item.line || 1}${item.details?.endLine ? `-${item.details.endLine}` : ''}`], ['管理引用', item.managedIds?.join(', ')]]));
      details.append(textElement('pre', JSON.stringify(item.details || {}, null, 2)));
      if (!member && group.itemIds.length > 1 && subject) details.append(membersSection(model, group, actions));
      return details;
    });
    const relations = relationsSection(model, group, actions);
    if (relations) source.append(relations);
    container.append(source);
    const details = textElement('section', '', 'inventory-section'); details.append(provenance); container.append(details);
  }
}
