// audience: internal
// # inventory-cards
// 卡片通过稳定身份保持选择, 工具图标与用途一致, 内容查看和使用配置通过明确按钮进入.

import { textElement } from './view-utils.mjs';
import { createToolIcon } from './tool-icon.mjs';
import { kindNames } from './inventory-model.mjs';
import { groupDisabled } from './inventory-visibility.mjs';
import { cardQuickControl, cardSubject } from './card-controls.mjs';
import { moduleActivation } from './module-controls.mjs';

// //// 生成可直接查看与配置的内容卡片 [@x380kkm 2026-09-07] ////
function card(model, group, options) {
  const item = textElement('article', '', 'inventory-card');
  item.dataset.groupId = group.id;
  item.classList.toggle('disabled-card', groupDisabled(model, group));
  const header = textElement('div', '', 'inventory-card-header');
  header.append(createToolIcon(group.name, group.category));
  const title = textElement('button', group.name, 'inventory-card-name');
  title.dataset.selectId = group.id;
  title.addEventListener('click', () => options.onSelect(group.id));
  const heading = document.createElement('div');
  const subject = cardSubject(model, group);
  const type = subject?.kind || group.category;
  item.dataset.kind = type;
  heading.append(title);
  if (options.showKinds !== false) heading.append(textElement('span', kindNames[type] || (type === 'capability' ? '工具' : '内容'), 'inventory-card-kind'));
  header.append(heading);
  const cue = textElement('span', '', 'card-chevron'); cue.setAttribute('aria-hidden', 'true'); header.append(cue);
  item.append(header);
  const footer = textElement('div', '', 'inventory-card-footer');
  const control = subject ? cardQuickControl(subject, options) : type === 'module' ? moduleActivation(model.items.get(group.primaryId), options) : null;
  if (control) { footer.append(control); item.append(footer); }
  if (subject?.management?.availability === 'source-unresolved') item.append(textElement('p', '需映射本机来源', 'card-source-warning'));
  item.addEventListener('click', (event) => {
    if (!event.target.closest('button, input, select, textarea, a, label')) options.onSelect(group.id);
  });
  return item;
}

// //// 在同一平面排列规则与 Skill 卡片 [@x380kkm 2026-09-07] ////
export function renderInventoryCards(container, model, groups, options) {
  container.replaceChildren();
  const cards = textElement('div', '', 'inventory-cards');
  for (const group of groups) cards.append(card(model, group, options));
  container.append(cards);
}
