// audience: internal
// # content-preview
// 内容预览消费管理核心的固定读取快照, 展示完整单元与仍缺的引用.

import { catalogScopeNames, element, renderDiagnostics, request, textElement, unwrap } from './view-utils.mjs';

const purposeNames = { configuration: '使用配置', method: '方法正文', instruction: '适用说明', preference: '方法偏好', 'task-context': '配套内容', reference: '必需引用' };

// //// 创建所选发布的内容读取预览 [@x380kkm 2026-09-06] ////
export function createContentPreview({ run, setStatus, onUsage }) {
  const dialog = element('content-dialog');
  let description = null;
  let candidates = [];
  let result = null;
  let snapshot = null;
  let working = false;
  let queryContext = {};
  const units = new Map();

  // //// 清空依赖当前候选与条件的读取结果 [@x380kkm 2026-09-06] ////
  function clearRead() {
    result = null; snapshot = null; units.clear();
    element('content-unit').replaceChildren();
    element('content-body').textContent = '';
    element('content-snapshot').textContent = '';
    element('content-missing').textContent = '';
    element('content-continue').disabled = true;
    element('content-status').textContent = '选择已启用的 Skill 后读取完整使用内容.';
  }

  // //// 显示一个完整单元的正文 [@x380kkm 2026-09-06] ////
  function renderUnit() {
    const unit = units.get(element('content-unit').value);
    const descriptor = snapshot?.units.find((item) => item.ref === unit?.ref);
    const text = descriptor?.purpose === 'task-context' && typeof unit?.content?.text === 'string' ? unit.content.text : unit?.content;
    element('content-body').textContent = text == null ? '' : typeof text === 'string' ? text : JSON.stringify(text, null, 2);
  }

  // //// 累积同一读取链中的完整单元 [@x380kkm 2026-09-06] ////
  function renderResult() {
    for (const unit of result.units) units.set(unit.ref, unit);
    const select = element('content-unit'); const previous = select.value; select.replaceChildren();
    for (const unit of units.values()) {
      const descriptor = snapshot.units.find((item) => item.ref === unit.ref);
      const name = typeof unit.content?.name === 'string' ? unit.content.name : descriptor?.content?.entry || unit.ref;
      const label = `${purposeNames[descriptor?.purpose] || '内容'} / ${name}`;
      const option = textElement('option', label); option.value = unit.ref; select.append(option);
    }
    if (units.has(previous)) select.value = previous;
    else if (select.firstElementChild) select.value = select.firstElementChild.value;
    element('content-status').textContent = `${result.readiness === 'ready' ? '方法内容齐备' : '仍有必需内容缺口'} / 已取得 ${units.size} 个单元 / 需要 ${result.requiredUnits.length} 个单元`;
    element('content-missing').textContent = result.missing.join('\n');
    element('content-continue').disabled = !result.continuation;
    element('content-snapshot').textContent = JSON.stringify(snapshot, null, 2);
    renderDiagnostics(element('content-diagnostics'), result.diagnostics);
    renderUnit();
  }

  // //// 按显式条件查询当前发布的 Skill 候选 [@x380kkm 2026-09-06] ////
  async function loadCandidates() {
    let context;
    try { context = JSON.parse(element('content-context').value || '{}'); }
    catch { throw new Error('预览条件需要有效 JSON.'); }
    description = await request('usage.describe', { plugin: description.plugin, context, scope: description.scope });
    queryContext = context;
    candidates = description.effective.filter((item) => item.point === 'skill.x380kkm/deployment');
    element('content-title').textContent = description.name;
    const select = element('content-selection'); select.replaceChildren();
    for (const candidate of candidates) { const option = textElement('option', `${candidate.name} / ${candidate.ref}`); option.value = candidate.ref; select.append(option); }
    if (select.firstElementChild) select.value = select.firstElementChild.value;
    clearRead();
    element('content-read').disabled = candidates.length === 0;
    renderDiagnostics(element('content-diagnostics'), description.diagnostics);
    if (!candidates.length) element('content-status').textContent = '当前范围与条件下没有已启用的 Skill. 可在使用设置中调整选择.';
  }

  // //// 读取发布信息并打开固定范围的预览 [@x380kkm 2026-09-06] ////
  async function open(plugin, scope) {
    description = { plugin, scope };
    element('content-location').textContent = `${catalogScopeNames[scope]}${scope === 'user' ? '' : '及继承内容'} / 当前已保存声明`;
    element('content-context').value = '{}';
    element('content-resources').value = '';
    await loadCandidates();
    dialog.showModal();
    setStatus('内容预览使用当前已保存配置.');
  }

  // //// 在预览窗口中显示读取错误 [@x380kkm 2026-09-06] ////
  function execute(action) {
    return run(async () => {
      working = true; dialog.inert = true;
      try { await action(); }
      catch (error) { renderDiagnostics(element('content-diagnostics'), [{ code: error.code, message: error.message }]); setStatus(error.message); }
      finally { working = false; dialog.inert = false; }
    });
  }

  // //// 建立新的完整使用快照 [@x380kkm 2026-09-06] ////
  async function read() {
    const selected = candidates.find((item) => item.ref === element('content-selection').value);
    if (!selected) return;
    clearRead();
    const resources = element('content-resources').value.split('\n').map((value) => value.trim()).filter(Boolean);
    result = await request('content.preview', { ref: selected.ref, version: selected.version, scope: description.scope,
      context: queryContext, budget: Number(element('content-budget').value), resources });
    snapshot = await request('content.snapshot', { id: result.snapshot });
    renderResult(); setStatus('读取快照已建立.');
  }

  element('content-close').addEventListener('click', () => dialog.close());
  element('content-refresh').addEventListener('click', () => execute(loadCandidates));
  element('content-read').addEventListener('click', () => execute(read));
  element('content-authorize').addEventListener('click', () => execute(async () => {
    const result = await unwrap(window.manager.authorizeSource());
    if (result) {
      renderDiagnostics(element('content-diagnostics'));
      element('content-status').textContent = '来源目录已授权, 可以重新读取内容.';
    }
    setStatus(result ? `当前会话可读取来源目录: ${result.path}` : '保留当前读取范围.');
  }));
  element('content-unit').addEventListener('change', renderUnit);
  element('content-selection').addEventListener('change', clearRead);
  element('content-context').addEventListener('input', () => { clearRead(); element('content-read').disabled = true; });
  element('content-resources').addEventListener('input', clearRead);
  element('content-continue').addEventListener('click', () => execute(async () => {
    if (!result?.continuation) return;
    result = await request('content.continue', { continuation: result.continuation, budget: Number(element('content-budget').value) });
    renderResult(); setStatus('原快照的读取已继续.');
  }));
  element('content-usage').addEventListener('click', () => { dialog.close(); onUsage(description.plugin, description.scope); });
  dialog.addEventListener('cancel', (event) => { if (working) event.preventDefault(); });
  return { open };
}
