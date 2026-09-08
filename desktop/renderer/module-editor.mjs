// audience: internal
// # module-editor
// 模块表单维护成员引用与职责, 独立来源和组合声明通过同一预览保存.

import { refreshAfterSave, renderDiagnostics, request, textElement } from './view-utils.mjs';

// //// 创建带名称的表单项 [@x380kkm 2026-09-07] ////
function field(name, input) {
  const label = textElement('label', '', 'module-field'); label.append(textElement('span', name), input); return label;
}

// //// 编辑由独立内容组成的模块 [@x380kkm 2026-09-07] ////
export function createModuleEditor({ run, onSaved, onDirty, setStatus }) {
  const dialog = textElement('dialog', '', 'module-editor-dialog'); dialog.setAttribute('aria-label', '模块组合'); document.body.append(dialog);
  let record, members, initial, name, description, candidates, selected, feedback, previewButton, save, plan;
  let query = '', showOfficial = false, kind = '';
  const settings = () => ({ name: name.value, description: description.value, members });
  const dirty = () => JSON.stringify(settings()) !== initial;
  const canLeave = () => !dirty() || window.confirm('模块修改尚未保存. 放弃修改?');

  // //// 保存输入状态并使旧计划失效 [@x380kkm 2026-09-07] ////
  function changed() { plan = null; save.disabled = true; previewButton.disabled = !record.editable || !name.value.trim() || !members.length; onDirty(dirty()); }

  // //// 显示引用成员的职责与顺序 [@x380kkm 2026-09-07] ////
  function renderSelected() {
    selected.replaceChildren();
    selected.append(textElement('h3', `模块成员 / ${members.length}`, 'editor-column-heading'));
    selected.append(textElement('p', '使用说明随模块进入指令和按需内容. 适用场景用自然语言说明, Hook 触发仍由事件配置决定.', 'card-hint'));
    if (!members.length) selected.append(textElement('p', '从左侧选择此模块需要的内容.', 'card-hint'));
    members.forEach((member, index) => {
      const candidate = record.candidates.find((value) => value.itemId === member.itemId);
      const row = textElement('article', '', 'module-selected');
      row.append(textElement('h3', candidate?.name || member.itemId));
      const role = document.createElement('textarea'); role.rows = 2; role.value = member.role || ''; role.disabled = !record.editable;
      role.placeholder = '给 Agent 的使用说明, 例如: 仅在人类化写作或技术报告中参考.'; role.setAttribute('aria-label', `${candidate?.name || member.itemId} 的使用说明`);
      role.addEventListener('input', () => { member.role = role.value; changed(); }); row.append(role);
      const actions = textElement('div', '', 'button-group');
      for (const [label, offset] of [['上移', -1], ['下移', 1]]) {
        const button = textElement('button', label); button.disabled = !record.editable || index + offset < 0 || index + offset >= members.length;
        button.addEventListener('click', () => { members.splice(index, 1); members.splice(index + offset, 0, member); changed(); renderSelected(); }); actions.append(button);
      }
      const remove = textElement('button', '移出模块'); remove.disabled = !record.editable;
      remove.addEventListener('click', () => { members.splice(index, 1); changed(); renderSelected(); renderCandidates(); }); actions.append(remove);
      row.append(actions); selected.append(row);
    });
  }

  // //// 按独立内容类别选择模块成员 [@x380kkm 2026-09-07] ////
  function renderCandidates() {
    candidates.replaceChildren();
    candidates.append(textElement('h3', '可选内容', 'editor-column-heading'));
    const values = record.candidates.filter((candidate) => (!kind || candidate.kind === kind) && (showOfficial || candidate.origin !== 'official')
      && `${candidate.name} ${candidate.path || ''}`.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
    for (const candidate of values) {
      const row = textElement('label', '', 'module-candidate');
      const check = document.createElement('input'); check.type = 'checkbox'; check.checked = members.some((member) => member.itemId === candidate.itemId);
      check.disabled = !record.editable || !candidate.supported; check.setAttribute('aria-label', `选择 ${candidate.name}`);
      check.addEventListener('change', () => {
        if (check.checked) members.push({ itemId: candidate.itemId, role: '' }); else members = members.filter((member) => member.itemId !== candidate.itemId);
        changed(); renderSelected();
      });
      const info = textElement('div'); info.append(textElement('strong', candidate.name));
      info.title = candidate.path || candidate.ref || '';
      const repeated = record.candidates.some((value) => value.itemId !== candidate.itemId && value.name === candidate.name);
      if (candidate.reason || repeated) info.append(textElement('small', candidate.reason || candidate.path || candidate.ref, 'card-hint'));
      row.append(check, textElement('span', { skill: 'Skill', rule: '规则', tool: 'Tool', hook: 'Hook' }[candidate.kind] || candidate.kind, 'kind-badge'), info);
      candidates.append(row);
    }
  }

  // //// 保留失败草稿并显示保存结果 [@x380kkm 2026-09-07] ////
  function execute(action) {
    run(async () => {
      dialog.inert = true;
      try { await action(); }
      catch (error) { feedback.textContent = error.message; }
      finally { dialog.inert = false; }
    });
  }

  // //// 从当前配置层打开模块或建立新组合 [@x380kkm 2026-09-07] ////
  async function open(item, scope) {
    record = await request('module.describe', { id: item?.id, scope }); members = structuredClone(record.settings.members);
    query = ''; kind = ''; showOfficial = false; plan = null; dialog.replaceChildren();
    const heading = textElement('div', '', 'dialog-heading'), close = textElement('button', '取消');
    close.addEventListener('click', () => { if (canLeave()) dialog.close(); }); heading.append(textElement('h2', item ? '编辑模块' : '新建模块'), close);
    name = document.createElement('input'); name.value = record.settings.name; name.disabled = !record.editable; name.setAttribute('aria-label', '模块名称'); name.addEventListener('input', changed);
    description = document.createElement('textarea'); description.rows = 2; description.value = record.settings.description; description.disabled = !record.editable;
    description.setAttribute('aria-label', '模块补充说明'); description.addEventListener('input', changed);
    const note = textElement('details', '', 'module-optional-note');
    note.append(textElement('summary', '补充说明 (可选)'), description);
    const metadata = textElement('div', '', 'module-metadata'); metadata.append(field('模块名称', name), note);
    const toolbar = textElement('div', '', 'module-toolbar');
    const filter = document.createElement('select'); filter.setAttribute('aria-label', '成员类型');
    for (const [value, label] of [['', '全部内容'], ['rule', '规则'], ['skill', 'Skills'], ['hook', 'Hooks'], ['tool', 'Tools']]) { const option = textElement('option', label); option.value = value; filter.append(option); }
    filter.addEventListener('change', () => { kind = filter.value; renderCandidates(); });
    const search = document.createElement('input'); search.type = 'search'; search.placeholder = '搜索可用内容'; search.setAttribute('aria-label', '搜索模块成员');
    search.addEventListener('input', () => { query = search.value; renderCandidates(); });
    const official = document.createElement('input'); official.type = 'checkbox'; official.addEventListener('change', () => { showOfficial = official.checked; renderCandidates(); });
    const officialLabel = textElement('label', '', 'inventory-filter'); officialLabel.append(official, textElement('span', '显示官方内容')); toolbar.append(filter, search, officialLabel);
    const body = textElement('div', '', 'module-editor-body'); candidates = textElement('div', '', 'module-candidates'); selected = textElement('div', '', 'module-selected-list'); body.append(candidates, selected);
    feedback = textElement('div', record.reason || '', 'settings-feedback'); feedback.setAttribute('aria-live', 'polite');
    const actions = textElement('div', '', 'dialog-actions'); actions.append(textElement('span', scope === 'user' ? '保存到用户模块库' : '保存到当前项目的个人模块库'));
    previewButton = textElement('button', '预览组合'); save = textElement('button', '保存模块', 'primary');
    previewButton.addEventListener('click', () => execute(async () => {
      const result = await request('module.preview', { ...settings(), id: record.id, baseline: record.baseline, scope }); plan = result.plan;
      const diagnostics = textElement('div'); renderDiagnostics(diagnostics, result.diagnostics);
      feedback.replaceChildren(textElement('p', `保存 ${members.length} 项独立内容的固定版本引用. 启用范围另行设置.`), diagnostics);
      save.disabled = false;
    }));
    save.addEventListener('click', () => execute(async () => {
      if (!plan) return;
      await request('module.apply', { plan }); initial = JSON.stringify(settings()); onDirty(false); dialog.close();
      await refreshAfterSave(onSaved, setStatus, '模块组合已保存.');
    }));
    const buttons = textElement('div', '', 'button-group'); buttons.append(previewButton, save); actions.append(buttons);
    initial = JSON.stringify(settings());
    dialog.append(heading, metadata, toolbar, body, feedback, actions); changed(); renderCandidates(); renderSelected(); dialog.showModal();
  }
  dialog.addEventListener('cancel', (event) => { if (!canLeave()) event.preventDefault(); }); dialog.addEventListener('close', () => onDirty(false));
  return { open };
}
