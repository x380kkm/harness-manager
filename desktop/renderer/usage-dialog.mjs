// audience: internal
// # usage-dialog
// 使用表单保持独立绑定的编辑基线, 预览结果交给共用编辑器保存.

import { catalogScopeNames, element, renderDiagnostics, request, textElement } from './view-utils.mjs';

const pointNames = { 'skill.x380kkm/deployment': 'Skill', 'tool.x380kkm/endpoint': '工具', 'preference.x380kkm/method': '方法偏好', 'context.x380kkm/instruction': '说明', 'context.x380kkm/task': '配套内容' };

// //// 创建使用设置的编辑入口 [@x380kkm 2026-09-06] ////
export function createUsageDialog({ run, onPlan, onDraft, onDirty, setStatus }) {
  const dialog = element('usage-dialog');
  let description = null;
  let baseline = null;
  let initial = '';
  let working = false;
  let conflict = null;
  const memberControls = new Map();

  // //// 保存表单当前可见的编辑值 [@x380kkm 2026-09-06] ////
  function formValues() {
    return { id: element('usage-id').value, state: element('usage-state').value,
      members: Object.fromEntries([...memberControls].map(([id, input]) => [id, input.value])),
      options: element('usage-options').value, selector: element('usage-selector').value,
      acceptance: element('usage-acceptance').value, constraint: element('usage-version').value.trim() || null };
  }

  // //// 将表单变化纳入退出保护 [@x380kkm 2026-09-06] ////
  function changed() {
    onDirty(JSON.stringify(formValues()) !== initial);
    conflict = null;
    element('usage-open-json').hidden = true;
  }

  // //// 为成员选择填充标准状态 [@x380kkm 2026-09-06] ////
  function memberState(id) {
    const selection = baseline?.selection || {};
    return ['require', 'exclude', 'include'].find((state) => selection[state]?.includes(id)) || 'inherit';
  }

  // //// 显示所选绑定的真实字段 [@x380kkm 2026-09-06] ////
  function populate(binding) {
    baseline = binding;
    conflict = null;
    element('usage-id').value = binding?.id || description.suggestedId;
    element('usage-id').readOnly = Boolean(binding);
    element('usage-state').value = binding?.enabled === true ? 'enabled' : binding?.enabled === false ? 'disabled' : 'inherit';
    element('usage-version').value = binding ? binding.plugin.constraint || '' : description.release.version;
    element('usage-release').textContent = JSON.stringify(binding?.plugin || { id: description.pluginId, constraint: description.release.version }, null, 2);
    element('usage-options').value = JSON.stringify(binding?.options || {}, null, 2);
    element('usage-selector').value = JSON.stringify(binding?.target.selector || (description.scope === 'user' ? { user: 'current' } : {}), null, 2);
    element('usage-acceptance').value = binding ? 'keep' : description.scope === 'user' ? 'current' : 'inherit';
    element('usage-inherit').hidden = !binding || description.scope === 'user';
    element('usage-open-json').hidden = true;
    element('usage-defaults').textContent = JSON.stringify(description.defaults, null, 2);
    element('usage-schema').textContent = description.optionsSchema ? JSON.stringify(description.optionsSchema, null, 2) : '来源没有声明选项 Schema.';
    element('usage-members').replaceChildren();
    memberControls.clear();
    for (const member of description.members) {
      const row = textElement('label', '', 'usage-member');
      const label = textElement('span', member.name);
      const available = description.effective.some((candidate) => candidate.ref === member.ref);
      label.append(textElement('small', `${pointNames[member.point] || member.point} / ${member.id} / ${available ? '当前可发现' : '当前未提供'}`));
      const select = document.createElement('select');
      select.setAttribute('aria-label', `${member.name} 的选择方式`);
      for (const [value, name] of [['inherit', '继承'], ['include', '选入'], ['exclude', '排除'], ['require', '选入并设为必需']]) {
        const option = textElement('option', name);
        option.value = value;
        option.disabled = value === 'exclude' && member.required;
        select.append(option);
      }
      select.value = memberState(member.id);
      select.addEventListener('change', changed);
      memberControls.set(member.id, select);
      row.append(label, select);
      element('usage-members').append(row);
    }
    renderDiagnostics(element('usage-diagnostics'), description.diagnostics);
    initial = JSON.stringify(formValues());
    onDirty(false);
  }

  // //// 读取当前范围中的来源和绑定 [@x380kkm 2026-09-06] ////
  async function load(plugin, scope) {
    description = await request('usage.describe', { plugin, scope });
    element('usage-title').textContent = `${description.name} / ${description.release.version}`;
    element('usage-location').textContent = `${catalogScopeNames[scope]} / ${description.catalog}`;
    const select = element('usage-binding');
    select.replaceChildren();
    const create = textElement('option', '新增使用设置'); create.value = ''; select.append(create);
    for (const binding of description.bindings) {
      const option = textElement('option', `${binding.id} / ${JSON.stringify(binding.target.selector)}`);
      option.value = binding.id; select.append(option);
    }
    select.value = description.preferred || '';
    populate(description.bindings.find((item) => item.id === select.value) || null);
  }

  // //// 打开与原始发布分离的使用设置 [@x380kkm 2026-09-06] ////
  async function open(plugin, scope) {
    await load(plugin, scope);
    dialog.showModal();
    setStatus('使用设置保存为独立绑定. 来源发布保留原样.');
  }

  // //// 执行表单动作并保留失败时的当前编辑 [@x380kkm 2026-09-06] ////
  function execute(action) {
    return run(async () => {
      working = true; dialog.inert = true;
      try { await action(); }
      catch (error) {
        renderDiagnostics(element('usage-diagnostics'), [{ code: error.code, message: error.message }]);
        if (error.code === 'catalog-conflict' && error.details?.document) {
          conflict = error.details.document;
          element('usage-open-json').hidden = false;
        }
        setStatus(error.message);
      } finally { working = false; dialog.inert = false; }
    });
  }

  // //// 预览明确的字段变化并转入共用保存窗口 [@x380kkm 2026-09-06] ////
  async function preview() {
    const settings = formValues();
    try {
      settings.options = JSON.parse(settings.options || '{}');
      settings.selector = JSON.parse(settings.selector || '{}');
    } catch { throw new Error('使用选项和接收范围需要有效 JSON.'); }
    const result = await request('usage.preview', { plugin: description.plugin, scope: description.scope, baseline, settings });
    dialog.close();
    onPlan(result, description.scope);
  }

  // //// 确认是否放弃当前表单编辑 [@x380kkm 2026-09-06] ////
  function canDiscard() {
    return JSON.stringify(formValues()) === initial || window.confirm('当前使用设置尚未保存. 放弃这些编辑?');
  }

  element('usage-binding').addEventListener('change', (event) => {
    if (!canDiscard()) { event.target.value = baseline?.id || ''; return; }
    populate(description.bindings.find((item) => item.id === event.target.value) || null);
  });
  for (const id of ['usage-id', 'usage-options', 'usage-selector', 'usage-version']) element(id).addEventListener('input', changed);
  for (const id of ['usage-state', 'usage-acceptance']) element(id).addEventListener('change', changed);
  element('usage-close').addEventListener('click', () => { if (canDiscard()) dialog.close(); });
  element('usage-reload').addEventListener('click', () => { if (canDiscard()) return execute(() => load(description.plugin, description.scope)); });
  element('usage-preview').addEventListener('click', () => execute(preview));
  element('usage-inherit').addEventListener('click', () => execute(async () => {
    if (!baseline || !canDiscard()) return;
    const result = await request('document.preview_remove', { id: baseline.id, baseline, scope: description.scope });
    dialog.close(); onPlan(result, description.scope);
  }));
  element('usage-open-json').addEventListener('click', () => {
    if (!conflict) return;
    const draft = conflict; dialog.close();
    onDraft(draft, baseline, { scope: description.scope, path: description.catalog, id: baseline?.id });
  });
  dialog.addEventListener('cancel', (event) => { if (working || !canDiscard()) event.preventDefault(); });
  dialog.addEventListener('close', () => onDirty(false));
  return { open };
}
