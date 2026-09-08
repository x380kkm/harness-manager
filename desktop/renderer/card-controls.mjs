// audience: internal
// # card-controls
// 卡片动作按明确的配置层开启或关闭内容, 规则与 Skill 共用同一组使用入口.

import { textElement } from './view-utils.mjs';

// //// 找出一张卡片对应的可配置内容 [@x380kkm 2026-09-07] ////
export function cardSubject(model, group) {
  const subjects = group.itemIds.map((id) => model.items.get(id)).filter((item) => item && (['skill', 'rule'].includes(item.kind) || item.kind === 'hook' && item.management?.supported));
  return subjects.length === 1 ? subjects[0] : null;
}

// //// 从当前声明取得可读的启用范围 [@x380kkm 2026-09-07] ////
export function activationLabel(management) {
  if (!management) return '未设置';
  if (management.moduleUses?.length && (management.scope === 'user' ? management.userState : management.privateState) !== 'enabled') return '模块正在使用';
  if (management.scope === 'user') {
    return management.userState === 'enabled' ? '用户级开启' : management.userState === 'disabled' ? '用户级关闭' : '未设置';
  }
  if (management.scope === 'project-local') {
    if (management.privateState === 'enabled') return '个人开启';
    if (management.privateState === 'disabled') return '个人关闭';
    if (management.sharedState === 'enabled') return '项目共享开启';
    if (management.sharedState === 'disabled') return '项目共享关闭';
  }
  if (management.projectState === 'enabled') return '项目开启';
  if (management.projectState === 'disabled') return '项目关闭';
  if (management.userState === 'enabled') return '继承用户级开启';
  if (management.userState === 'disabled') return '继承用户级关闭';
  return '未设置';
}

// //// 创建用户与项目范围的快捷启停选择 [@x380kkm 2026-09-07] ////
export function activationControl(subject, actions) {
  const select = document.createElement('select');
  select.className = 'card-activation';
  select.setAttribute('aria-label', `${subject.name} 的启用范围`);
  select.title = '选择 Manager 提供此内容的范围';
  const current = textElement('option', activationLabel(subject.management));
  current.value = ''; current.disabled = true; select.append(current);
  const project = actions.scope !== 'user';
  const choices = [['enabled', project ? '在此项目开启' : '用户级开启'], ['disabled', project ? '在此项目关闭' : '用户级关闭'], ['inherit', project ? '恢复继承' : '恢复默认']];
  for (const [value, label] of choices) { const option = textElement('option', label); option.value = value; select.append(option); }
  select.value = '';
  select.disabled = actions.readOnly || subject.management?.supported === false;
  select.addEventListener('change', () => { const action = select.value; select.value = ''; actions.onConfigure(subject.id, action); });
  return select;
}

// //// 在总览卡片中呈现快捷启停与明确状态 [@x380kkm 2026-09-07] ////
export function cardQuickControl(subject, actions) {
  if (!actions.readOnly) return activationControl(subject, actions);
  const label = activationLabel(subject.management);
  return label === '未设置' ? null : textElement('span', label, 'card-state-label');
}

// //// 提供启用范围和关系配置的共同操作面 [@x380kkm 2026-09-07] ////
export function cardControls(subject, actions) {
  const controls = textElement('div', '', 'card-controls');
  if (actions.readOnly) {
    controls.classList.add('card-controls-readonly');
    controls.append(textElement('span', activationLabel(subject.management), 'card-state-label'));
    if (actions.scope === 'project-local' && subject.management?.shared) controls.append(textElement('span', '项目共享', 'card-shared'));
    return controls;
  }
  controls.append(activationControl(subject, actions));
  if (subject.management?.moduleUses?.length) {
    const modules = textElement('span', `模块: ${subject.management.moduleUses.map((module) => module.name).join(', ')}`, 'card-module-uses');
    controls.append(modules);
  }
  const relations = textElement('button', '工具与依赖', 'card-relations-button');
  relations.disabled = actions.readOnly || subject.management?.supported === false;
  relations.addEventListener('click', () => actions.onRelations(subject.id));
  if (subject.kind !== 'hook') controls.append(relations);
  if (actions.scope === 'project-local') {
    const label = textElement('label', '', 'card-shared');
    const shared = document.createElement('input'); shared.type = 'checkbox'; shared.checked = subject.management?.shared === true;
    shared.disabled = actions.readOnly; shared.setAttribute('aria-label', `${subject.name} 随项目 Git 共享`);
    shared.addEventListener('change', () => actions.onShare(subject.id, shared.checked));
    label.append(shared, textElement('span', '随项目 Git 共享')); controls.append(label);
    if (subject.management?.shared && subject.management.privateState && subject.management.privateState !== 'inherit') {
      const publish = textElement('button', '更新共享设置'); publish.disabled = actions.readOnly;
      publish.addEventListener('click', () => actions.onShare(subject.id, true)); controls.append(publish);
    }
  }
  return controls;
}
