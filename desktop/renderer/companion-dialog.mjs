// audience: internal
// # companion-dialog
// 正文编辑和使用设置共享一个读取基线. 冲突比较保留本地表单, 用户确认后采用新的磁盘基线.

import { planDifferences } from './document-diff.mjs';
import { catalogScopeNames, element, refreshAfterSave, renderDiagnostics, request, textElement } from './view-utils.mjs';

// //// 维护配套说明的正文、范围与联合保存 [@x380kkm 2026-09-06] ////
export function createCompanionDialog({ run, setStatus, onDirty, onSaved }) {
  const dialog = element('companion-dialog');
  let description = null;
  let baseline = null;
  let selectedId = '';
  let initial = '';
  let plan = null;
  let latest = null;
  let working = false;
  let requiresRead = false;

  // //// 取得尚未解析的表单值以检测未保存内容 [@x380kkm 2026-09-06] ////
  function fields() {
    return { name: element('companion-name').value, skill: element('companion-skill').value,
      text: element('companion-text').value, enabled: element('companion-enabled').checked,
      selector: element('companion-selector').value };
  }

  // //// 表单改变时撤销旧计划并更新退出保护 [@x380kkm 2026-09-06] ////
  function changed() {
    plan = null;
    onDirty(JSON.stringify(fields()) !== initial);
  }

  // //// 显示独立编辑或固定差异视图 [@x380kkm 2026-09-06] ////
  function comparison(visible) {
    element('companion-editing').hidden = visible;
    element('companion-comparison').hidden = !visible;
    element('companion-back').hidden = !visible;
    element('companion-apply').hidden = !visible;
    element('companion-preview').hidden = visible;
    element('companion-remove').hidden = visible || !baseline;
  }

  // //// 根据选定正文恢复完整表单和原始基线 [@x380kkm 2026-09-06] ////
  function populate(item) {
    selectedId = item?.id || '';
    baseline = item?.baseline || null;
    latest = null;
    const settings = item?.settings || { name: `${description.name} 配套说明`, skill: description.skills[0]?.ref || '',
      text: '', enabled: true, selector: description.scope === 'user' ? { user: 'current' } : {} };
    for (const key of ['name', 'text', 'skill']) element(`companion-${key}`).value = settings[key];
    element('companion-selector').value = JSON.stringify(settings.selector, null, 2);
    element('companion-enabled').checked = settings.enabled;
    const readOnly = item ? !item.editable : description.skills.length === 0;
    element('companion-fields').disabled = readOnly;
    element('companion-preview').disabled = readOnly;
    element('companion-remove').disabled = readOnly;
    element('companion-origin').textContent = item && !item.editable
      ? item.scope !== description.scope ? `继承${catalogScopeNames[item.scope]}的内容. 原文在对应配置层中维护.` : '此内容包含外部来源或组合设置. 完整声明在内容库中维护.'
      : '独立正文 / Skill 专属配套内容';
    initial = JSON.stringify(fields());
    renderDiagnostics(element('companion-diagnostics'));
    element('companion-conflict').hidden = true;
    comparison(false);
    changed();
  }

  // //// 重新读取所选发布的全部配套说明 [@x380kkm 2026-09-06] ////
  async function load(plugin, scope, preferred = selectedId) {
    description = await request('context.describe', { plugin, scope });
    requiresRead = false;
    element('companion-title').textContent = description.name;
    element('companion-location').textContent = `${catalogScopeNames[scope]} / ${description.catalog}`;
    const skills = element('companion-skill'); skills.replaceChildren();
    const refs = new Set();
    for (const skill of description.skills) {
      for (const ref of skill.references) {
        if (refs.has(ref)) continue;
        refs.add(ref);
        const option = textElement('option', ref === skill.ref ? skill.name : `${skill.name} / 原始来源`);
        option.value = ref; skills.append(option);
      }
    }
    const selection = element('companion-selection'); selection.replaceChildren(); selection.disabled = false;
    const create = textElement('option', '新增配套说明'); create.value = ''; selection.append(create);
    for (const item of description.contexts) {
      const option = textElement('option', `${item.name}${item.scope !== scope ? ` / 继承${catalogScopeNames[item.scope]}` : ''}`);
      option.value = item.id; selection.append(option);
    }
    const selected = description.contexts.find((item) => item.id === preferred);
    selection.value = selected?.id || '';
    populate(selected);
    if (!description.skills.length) renderDiagnostics(element('companion-diagnostics'), [{ message: '此发布没有可关联的 Skill. 配套内容从 Skill 发布进入.' }]);
  }

  // //// 打开配套说明编辑并固定配置归属 [@x380kkm 2026-09-06] ////
  async function open(plugin, scope) {
    selectedId = '';
    await load(plugin, scope);
    dialog.showModal();
    setStatus('配套正文与上游 Skill 分别保存.');
  }

  // //// 对每份实际写入声明显示字段差异 [@x380kkm 2026-09-06] ////
  function showPlan(result) {
    plan = result.plan;
    const removing = plan.entries[0].operation === 'remove';
    element('companion-plan-title').textContent = `${removing ? '移出' : '保存'}: ${result.name}`;
    element('companion-apply').textContent = removing ? '确认移出正文与设置' : '保存正文与设置';
    const container = element('companion-diffs'); container.replaceChildren();
    for (const entry of plan.entries) {
      container.append(textElement('h3', entry.after?.kind === 'PluginBinding' || entry.before?.kind === 'PluginBinding' ? '使用设置' : '配套正文'));
      const columns = textElement('div', '', 'diff-columns');
      const rows = planDifferences(entry);
      for (const [key, label, className] of [['before', '当前登记', 'removed'], ['after', '提交内容', 'added']]) {
        const section = document.createElement('section');
        const content = document.createElement('pre');
        content.append(...rows[key].map((row) => textElement('span', row.text || ' ', `diff-line${row.changed ? ` ${className}` : ''}`)));
        section.append(textElement('h3', label), content); columns.append(section);
      }
      container.append(columns);
    }
    element('companion-plan').textContent = JSON.stringify(plan, null, 2);
    comparison(true);
    onDirty(true);
  }

  // //// 保留表单并读取发生冲突后的内容基线 [@x380kkm 2026-09-06] ////
  async function showConflict() {
    const current = await request('context.describe', { plugin: description.plugin, scope: description.scope });
    const id = selectedId || plan?.entries[0].id;
    latest = current.contexts.find((item) => item.id === id) || null;
    element('companion-latest').textContent = JSON.stringify(latest?.baseline || { state: '内容已移出或结构发生变化' }, null, 2);
    element('companion-conflict').hidden = false;
    element('companion-rebase').disabled = !latest?.editable;
    comparison(false);
    plan = null;
  }

  // //// 在对话框内保留失败原因和全部表单内容 [@x380kkm 2026-09-06] ////
  function execute(action) {
    return run(async () => {
      working = true; dialog.inert = true;
      try { await action(); }
      catch (error) {
        renderDiagnostics(element('companion-diagnostics'), [{ code: error.code, message: error.message }]);
        setStatus(error.message);
        if (error.code === 'catalog-conflict') await showConflict();
      } finally { working = false; dialog.inert = false; }
    });
  }

  // //// 确认放弃表单变化时保留用户选择 [@x380kkm 2026-09-06] ////
  function canDiscard() {
    return (!plan && JSON.stringify(fields()) === initial) || window.confirm('配套说明尚未保存. 放弃这些编辑?');
  }

  for (const id of ['companion-name', 'companion-text', 'companion-selector']) element(id).addEventListener('input', changed);
  for (const id of ['companion-skill', 'companion-enabled']) element(id).addEventListener('change', changed);
  element('companion-selection').addEventListener('change', (event) => {
    if (!canDiscard()) { event.target.value = selectedId; return; }
    populate(description.contexts.find((item) => item.id === event.target.value));
  });
  element('companion-close').addEventListener('click', () => { if (canDiscard()) dialog.close(); });
  element('companion-reload').addEventListener('click', () => { if (canDiscard()) return execute(() => load(description.plugin, description.scope)); });
  element('companion-back').addEventListener('click', () => { comparison(false); changed(); });
  element('companion-preview').addEventListener('click', () => execute(async () => {
    if (requiresRead) return;
    const settings = fields();
    try { settings.selector = JSON.parse(settings.selector || '{}'); }
    catch { throw new Error('接收条件需要有效 JSON.'); }
    showPlan(await request('context.preview', { plugin: description.plugin, scope: description.scope, settings, baseline }));
  }));
  element('companion-remove').addEventListener('click', () => execute(async () => {
    if (requiresRead || !baseline || !canDiscard()) return;
    showPlan(await request('context.preview_remove', { plugin: description.plugin, scope: description.scope, baseline }));
  }));
  element('companion-rebase').addEventListener('click', () => {
    if (!latest?.editable) return;
    const values = latest.settings;
    initial = JSON.stringify({ name: values.name, skill: values.skill, text: values.text, enabled: values.enabled,
      selector: JSON.stringify(values.selector, null, 2) });
    baseline = latest.baseline; selectedId = latest.id; latest = null;
    element('companion-conflict').hidden = true;
    renderDiagnostics(element('companion-diagnostics'));
    changed();
    setStatus('当前表单保留. 再次预览将比较最新磁盘内容.');
  });
  element('companion-apply').addEventListener('click', () => execute(async () => {
    if (!plan) return;
    const result = await request('context.apply', { plan });
    plan = null; latest = null; requiresRead = true; selectedId = result.removed ? '' : result.id;
    initial = JSON.stringify(fields()); onDirty(false); comparison(false);
    for (const id of ['companion-fields', 'companion-selection', 'companion-preview', 'companion-remove']) element(id).disabled = true;
    await refreshAfterSave(async () => { await load(description.plugin, description.scope); await onSaved(); },
      (message) => { renderDiagnostics(element('companion-diagnostics'), [{ message }]); setStatus(message); },
      result.removed ? '配套正文与本层绑定已移出. 上游 Skill 保持原位.' : '配套正文与使用设置已保存.');
  }));
  dialog.addEventListener('cancel', (event) => { if (working || !canDiscard()) event.preventDefault(); });
  dialog.addEventListener('close', () => onDirty(false));
  return { open };
}
