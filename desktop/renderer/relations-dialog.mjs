// audience: internal
// # relations-dialog
// 双向列表读取同一份有向关系, 文本编辑保留该关系的读取基线与配置归属.

import { element, refreshAfterSave, renderDiagnostics, request, textElement } from './view-utils.mjs';
import { createToolIcon } from './tool-icon.mjs';

// //// 提供工具选择与关联适配文本的共同编辑入口 [@x380kkm 2026-09-07] ////
export function createRelationsDialog({ run, onSaved, onDirty, setStatus }) {
  const dialog = element('relations-dialog');
  let description = null, direction = 'outgoing', selected = null, baseline = null;
  let query = '', initialText = '', working = false, candidateFilter = 'skill';
  let requiresRead = false;

  // //// 判断文本是否存在尚未保存的编辑 [@x380kkm 2026-09-07] ////
  function isDirty() { return element('relation-text').value !== initialText; }

  // //// 在切换编辑对象时保留尚未保存的文本 [@x380kkm 2026-09-07] ////
  function canLeave() { return !isDirty() || window.confirm('Adapter 文本尚未保存. 放弃修改?'); }

  // //// 将另一张卡片映射到唯一有向关系 [@x380kkm 2026-09-07] ////
  function relationFor(candidate) {
    return description[direction].find((edge) => (direction === 'outgoing' ? edge.to : edge.from) === candidate.id);
  }

  // //// 在关系两侧保持相同的默认适配提示 [@x380kkm 2026-09-07] ////
  function pair(candidate) {
    return direction === 'outgoing' ? { from: description.subject, to: candidate } : { from: candidate, to: description.subject };
  }

  // //// 显示选中关系的正文与来源方向 [@x380kkm 2026-09-07] ////
  function select(candidate) {
    if (requiresRead) return;
    selected = candidate;
    const edge = relationFor(candidate), sides = pair(candidate);
    baseline = edge?.baseline || null;
    initialText = edge?.text || `使用本 skill 时可以参考使用 **${sides.to.name}** skill`;
    element('relation-text').value = initialText;
    element('relation-title').textContent = `${sides.from.name} → ${sides.to.name}`;
    const scopes = { user: '用户级', project: '项目共享', 'project-local': '仅自己使用' };
    element('relation-origin').textContent = edge ? `${scopes[edge.scope]}${edge.inherited ? ' / 继承' : ''}`
      + (edge.sourceScope && edge.sourceScope !== edge.scope ? ` / 正文: ${scopes[edge.sourceScope]} (${edge.sourceVersion})` : '') : '新关系';
    element('relation-editor').hidden = false;
    element('relation-save').disabled = false;
    element('relation-inherit').hidden = description.scope === 'user' || !edge || edge.inherited;
    onDirty(false);
  }

  // //// 从后台重新取得同一张卡片的双向关系列表 [@x380kkm 2026-09-07] ////
  async function load() {
    description = await request('card.relations', { id: description.subject.id, scope: description.scope });
    requiresRead = false;
    element('relations-title').textContent = description.subject.name;
    if (selected) {
      const candidate = description.candidates.find((item) => item.id === selected.id);
      if (candidate) select(candidate); else { selected = null; element('relation-editor').hidden = true; }
    }
    render();
  }

  // //// 在对话框内执行操作并保留失败草稿 [@x380kkm 2026-09-07] ////
  function execute(action) {
    return run(async () => {
      working = true; dialog.inert = true;
      renderDiagnostics(element('relations-error'));
      try { await action(); }
      catch (error) { renderDiagnostics(element('relations-error'), [{ code: error.code, message: error.message }]); setStatus(error.message); }
      finally { working = false; dialog.inert = false; }
    });
  }

  // //// 保存关系后取得新基线并分开显示读取反馈 [@x380kkm 2026-09-08] ////
  async function refreshSaved(message) {
    requiresRead = true; baseline = null; initialText = ''; onDirty(false);
    element('relation-text').value = ''; element('relation-editor').hidden = true;
    element('relation-save').disabled = true; render();
    await refreshAfterSave(async () => { await load(); await onSaved(); },
      (message) => { renderDiagnostics(element('relations-error'), [{ message }]); setStatus(message); }, message);
  }

  // //// 从任一侧保存相同方向的参考关系 [@x380kkm 2026-09-07] ////
  async function setEnabled(candidate, enabled) {
    if (requiresRead) return;
    if (!canLeave()) { render(); return; }
    const edge = relationFor(candidate), sides = pair(candidate);
    try {
      await request('card.set_relation', { from_id: sides.from.id, to_id: sides.to.id, text: edge?.text,
        scope: description.scope, baseline: edge?.baseline || null, enabled });
    } catch (error) { render(); throw error; }
    selected = candidate;
    await refreshSaved('关系设置已保存.');
  }

  // //// 渲染可搜索的引用与被引用列表 [@x380kkm 2026-09-07] ////
  function render() {
    const list = element('relations-candidates'); list.replaceChildren();
    for (const value of ['outgoing', 'incoming']) {
      const count = description[value].filter((edge) => edge.enabled).length;
      element(`relations-${value}`).textContent = `${value === 'outgoing' ? '参考的工具' : '被哪些工具参考'} ${count}`;
      element(`relations-${value}`).setAttribute('aria-pressed', String(direction === value));
    }
    const candidates = description.candidates.filter((item) => (!candidateFilter || item.kind === candidateFilter) && item.name.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
    for (const candidate of candidates) {
      const edge = relationFor(candidate);
      const row = textElement('div', '', 'relation-choice');
      const check = document.createElement('input'); check.type = 'checkbox'; check.checked = edge?.enabled === true; check.disabled = requiresRead;
      check.setAttribute('aria-label', `${direction === 'outgoing' ? '参考' : '由其参考'} ${candidate.name}`);
      check.addEventListener('change', () => execute(() => setEnabled(candidate, check.checked)));
      const title = textElement('button', candidate.name, 'relation-choice-name');
      title.disabled = requiresRead;
      title.addEventListener('click', () => { if (canLeave()) select(candidate); });
      row.append(check, createToolIcon(candidate.name, candidate.kind, 28), title);
      if (edge?.scope === 'project') row.append(textElement('span', '共享', 'kind-badge'));
      if (edge?.inherited) row.append(textElement('span', '继承', 'kind-badge'));
      list.append(row);
    }
    if (!candidates.length) list.append(textElement('p', '没有匹配内容.', 'scope-location'));
  }

  // //// 打开指定作用域的工具关系编辑 [@x380kkm 2026-09-07] ////
  async function open(id, scope) {
    description = { subject: { id }, scope }; selected = null; query = ''; direction = 'outgoing'; candidateFilter = 'skill';
    element('relations-search').value = ''; element('relations-kind').value = 'skill'; element('relation-editor').hidden = true;
    element('relation-text').value = ''; initialText = ''; onDirty(false);
    await load(); dialog.showModal();
    element('relations-scope').textContent = scope === 'user' ? '用户级关联' : '项目关联 / 两端都共享时, 关系与 Adapter 文本随项目共享';
  }

  element('relations-search').addEventListener('input', (event) => { query = event.target.value; render(); });
  element('relations-kind').addEventListener('change', (event) => { candidateFilter = event.target.value; render(); });
  for (const value of ['outgoing', 'incoming']) element(`relations-${value}`).addEventListener('click', () => {
    if (!canLeave()) return;
    direction = value; selected = null; initialText = ''; element('relation-text').value = ''; element('relation-editor').hidden = true; onDirty(false); render();
  });
  element('relation-text').addEventListener('input', () => onDirty(isDirty()));
  element('relations-close').addEventListener('click', () => { if (canLeave()) dialog.close(); });
  element('relations-reload').addEventListener('click', () => { if (canLeave()) execute(load); });
  element('relation-save').addEventListener('click', () => execute(async () => {
    if (requiresRead || !selected) return;
    const sides = pair(selected), text = element('relation-text').value;
    await request('card.set_relation', { from_id: sides.from.id, to_id: sides.to.id, text, scope: description.scope, baseline, enabled: true });
    await refreshSaved('Adapter 文本已保存.');
  }));
  element('relation-inherit').addEventListener('click', () => execute(async () => {
    if (requiresRead || !selected || !canLeave()) return;
    const sides = pair(selected);
    await request('card.remove_relation', { from_id: sides.from.id, to_id: sides.to.id, scope: description.scope, baseline });
    await refreshSaved('本层关系已移出, 继承设置已恢复.');
  }));
  dialog.addEventListener('cancel', (event) => { if (working || !canLeave()) event.preventDefault(); });
  dialog.addEventListener('close', () => onDirty(false));
  return { open };
}
