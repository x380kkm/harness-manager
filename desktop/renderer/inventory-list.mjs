// audience: internal
// # inventory-group-list
// 折叠列表使用内容组作为主行, 展开状态和原始成员选择分别保存.

import { textElement } from './view-utils.mjs';
import { groupItems, groupStatus, groupSummary, kindNames, memberLabel } from './inventory-model.mjs';
import { createToolIcon } from './tool-icon.mjs';

// //// 显示一个内容组及按需展开的组成项 [@x380kkm 2026-09-07] ////
function groupRow(model, group, options) {
  const row = textElement('div', '', 'inventory-group');
  row.dataset.groupId = group.id;
  const heading = textElement('div', '', 'inventory-group-heading');
  const expanded = options.expanded.has(group.id);
  const toggle = textElement('button', expanded ? '⌄' : '›', 'inventory-expand');
  toggle.disabled = group.itemIds.length < 2;
  toggle.setAttribute('aria-label', `${expanded ? '收起' : '展开'} ${group.name}`);
  toggle.setAttribute('aria-expanded', String(expanded));
  toggle.dataset.toggleId = group.id;
  toggle.addEventListener('click', () => options.onToggle(group.id));
  const title = textElement('button', group.name, 'inventory-group-name');
  title.prepend(createToolIcon(group.name, group.category, 28));
  title.dataset.selectId = group.id;
  title.addEventListener('click', () => options.onSelect(group.id));
  const summary = textElement('span', groupSummary(model, group), 'inventory-group-summary');
  const status = textElement('span', groupStatus(model, group), 'inventory-group-status');
  heading.append(toggle, title, summary, status);
  row.append(heading);
  if (expanded) {
    const members = textElement('div', '', 'inventory-group-members');
    const choices = groupItems(model, group);
    const query = options.query.toLocaleLowerCase().trim();
    for (const item of choices) {
      const button = textElement('button', '', 'inventory-member');
      button.dataset.selectId = item.id;
      button.title = item.path;
      const name = memberLabel(model, group, item);
      button.append(textElement('span', kindNames[item.kind] || item.kind, 'inventory-member-kind'), textElement('span', name));
      if (query && `${item.name} ${item.path} ${item.summary}`.toLocaleLowerCase().includes(query)) button.classList.add('matched');
      button.addEventListener('click', () => options.onSelect(item.id));
      members.append(button);
    }
    row.append(members);
  }
  return row;
}

// //// 按内容用途排列个人内容组 [@x380kkm 2026-09-07] ////
export function renderGroupList(container, model, groups, options) {
  container.replaceChildren();
  const list = textElement('section', '', 'inventory-category');
  for (const group of groups) list.append(groupRow(model, group, options));
  container.append(list);
}

// //// 同步选择而保留列表滚动位置 [@x380kkm 2026-09-07] ////
export function selectGroupRow(container, model, selected) {
  const selectedGroup = model.byId.has(selected) ? selected : model.itemGroups.get(selected);
  for (const node of container.querySelectorAll('[data-select-id]')) {
    node.setAttribute('aria-pressed', String(node.dataset.selectId === selected));
  }
  for (const node of container.querySelectorAll('[data-group-id]')) {
    node.classList.toggle('selected', node.dataset.groupId === selectedGroup);
  }
}
