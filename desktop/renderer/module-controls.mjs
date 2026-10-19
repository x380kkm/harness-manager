// audience: internal
// # module-controls
// 模块控制整体使用绑定, 成员组合通过独立编辑入口维护.

import { textElement } from './view-utils.mjs';

// //// 为模块生成当前使用范围的启停控件 [@x380kkm 2026-09-07] ////
export function moduleActivation(item, actions) {
  if (actions.readOnly) {
    return textElement('span', item.details.configuredState === 'enabled' ? '本层开启' : item.details.configuredState === 'disabled' ? '本层关闭' : actions.scope === 'user' ? '尚未开启' : '按继承设置', 'card-state-label');
  }
  const select = document.createElement('select'); select.setAttribute('aria-label', `${item.name} 的模块状态`); select.disabled = actions.readOnly;
  const current = textElement('option', item.details.configuredState === 'enabled' ? '本层开启' : item.details.configuredState === 'disabled' ? '本层关闭' : actions.scope === 'user' ? '尚未开启' : '按继承设置'); current.value = ''; current.disabled = true; select.append(current);
  for (const [value, label] of [['enabled', actions.scope === 'user' ? '用户级开启' : '此项目开启'], ['disabled', actions.scope === 'user' ? '用户级关闭' : '此项目关闭'], ['inherit', '恢复继承']]) {
    const option = textElement('option', label); option.value = value; select.append(option);
  }
  select.value = ''; select.addEventListener('change', () => { const value = select.value; select.value = ''; actions.onConfigureModule(item, value); });
  return select;
}

// //// 显示模块整体启停与组合入口 [@x380kkm 2026-09-07] ////
export function moduleControls(item, actions) {
  const controls = textElement('div', '', 'card-controls');
  controls.append(moduleActivation(item, actions));
  if (actions.readOnly) return controls;
  const edit = textElement('button', '编辑组合'); edit.disabled = actions.readOnly;
  edit.addEventListener('click', () => actions.onModule(item)); controls.append(edit); return controls;
}
