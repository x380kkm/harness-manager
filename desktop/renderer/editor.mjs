// audience: internal
// # declaration-editor
// 编辑器保留读取基线, 预览固定当前提交内容. 应用操作由管理核心核对基线.

import { catalogScopeNames, displayValue, documentName, element, renderDiagnostics, request, textElement } from './view-utils.mjs';
import { renderPlanDifference } from './document-diff.mjs';
import { createMergeDialog } from './merge-dialog.mjs';

// //// 创建声明编辑与计划应用入口 [@x380kkm 2026-09-06] ////
export function createEditor({ run, onDirty, onApplied, onRecovered, setStatus }) {
  const input = element('json-editor');
  const previewDialog = element('preview-dialog');
  let current = null;
  let registeredId = null;
  let recoveryId = null;
  let record = null;
  let catalogScope = 'user';
  let baseline = null;
  let initialText = '';
  let plan = null;
  let removing = false;
  let dirty = false;
  let mergeDirty = false;
  const mergeDialog = createMergeDialog({ run, onMerged: adoptMerge, onRemovalPreview: showRemovalPreview, onDirty: setMergeDirty, setStatus });

  // //// 记录编辑状态并使旧预览失效 [@x380kkm 2026-09-06] ////
  function updateDirty() {
    dirty = input.value !== initialText || (current !== null && baseline === null);
    element('dirty-label').hidden = !dirty;
    onDirty(dirty || mergeDirty);
    plan = null;
  }

  // //// 将合并工作文本纳入退出保护 [@x380kkm 2026-09-06] ////
  function setMergeDirty(value) {
    mergeDirty = value;
    onDirty(dirty || mergeDirty);
  }

  // //// 显示声明的实际属性 [@x380kkm 2026-09-06] ////
  function renderProperties(document, record) {
    const properties = [
      ['逻辑 ID', document?.id], ['登记 ID', record?.id], ['种类', document?.kind],
      ['版本', document?.release?.version], ['目标', document?.target?.selector],
      ['登记位置', record?.path],
      ['配置归属', catalogScopeNames[record?.scope || 'user']],
    ];
    element('document-properties').replaceChildren();
    for (const [label, value] of properties) {
      if (value == null) continue;
      element('document-properties').append(textElement('dt', label), textElement('dd', displayValue(value)));
    }
  }

  // //// 在编辑器中载入声明与读取基线 [@x380kkm 2026-09-06] ////
  function show(document, nextBaseline, nextRecord) {
    current = document;
    record = nextRecord;
    catalogScope = nextRecord?.scope || 'user';
    registeredId = record?.id || null;
    recoveryId = registeredId;
    baseline = nextBaseline;
    initialText = nextBaseline ? JSON.stringify(nextBaseline, null, 2) : document ? JSON.stringify(document, null, 2) : '';
    input.value = document ? JSON.stringify(document, null, 2) : '';
    input.readOnly = false;
    element('json-editor-label').textContent = '声明 JSON';
    element('editor-tools').hidden = false;
    element('inspector-actions').hidden = false;
    element('inspector-empty').hidden = true;
    element('inspector-content').hidden = false;
    element('document-title').textContent = document ? documentName(document) : '新建声明';
    element('document-kind').textContent = document?.kind || '完整 JSON';
    element('remove-button').hidden = baseline === null || registeredId === null;
    element('recover-button').hidden = recoveryId === null;
    renderProperties(document, record);
    updateDirty();
  }

  // //// 显示未登记图节点的只读信息 [@x380kkm 2026-09-06] ////
  function showReference(node) {
    current = null;
    registeredId = null;
    recoveryId = null;
    record = null;
    baseline = null;
    initialText = JSON.stringify(node, null, 2);
    input.value = initialText;
    input.readOnly = true;
    element('inspector-empty').hidden = true;
    element('inspector-content').hidden = false;
    element('document-title').textContent = node.label || node.id;
    element('document-kind').textContent = node.kind || '引用';
    element('json-editor-label').textContent = '节点信息';
    element('editor-tools').hidden = true;
    element('inspector-actions').hidden = true;
    element('document-properties').replaceChildren(
      textElement('dt', '节点 ID'), textElement('dd', node.id),
      textElement('dt', '登记'), textElement('dd', node.inherited ? '用户级继承声明' : '未登记引用'),
    );
    updateDirty();
  }

  // //// 清空当前声明选择 [@x380kkm 2026-09-06] ////
  function clear() {
    current = null;
    registeredId = null;
    recoveryId = null;
    record = null;
    baseline = null;
    initialText = '';
    input.value = '';
    element('inspector-empty').hidden = false;
    element('inspector-content').hidden = true;
    updateDirty();
  }

  // //// 读取用户提交的完整声明对象 [@x380kkm 2026-09-06] ////
  function readDraft() {
    let document;
    try { document = JSON.parse(input.value); }
    catch (error) { throw Object.assign(new Error(`声明 JSON 无法读取: ${error.message}`), { code: 'invalid-json' }); }
    if (!document || typeof document !== 'object' || Array.isArray(document)) throw new Error('声明需要是一个 JSON 对象.');
    return document;
  }

  // //// 显示管理核心生成的固定计划 [@x380kkm 2026-09-06] ////
  function openPreview(result) {
    plan = result.plan;
    recoveryId = plan.id;
    element('recover-button').hidden = false;
    const removing = plan.operation === 'remove';
    const creating = plan.operation === 'put' && plan.before === null;
    element('preview-title').textContent = `${removing ? '移出登记' : creating ? '新增声明' : '保存声明'}: ${documentName(plan.after || plan.before)}`;
    element('preview-description').textContent = removing
      ? '此操作移除内容目录中的登记. 来源文件保留在原位置.'
      : '请核对读取内容与提交内容, 然后保存当前计划.';
    if (plan.catalog) element('preview-description').textContent += ` 写入位置: ${plan.catalog}`;
    element('preview-effective').hidden = !Array.isArray(result.effective);
    element('preview-effective').textContent = Array.isArray(result.effective)
      ? `当前条件下可发现 ${result.effective.length} 项内容: ${result.effective.slice(0, 8).map((item) => item.name).join(', ')}${result.effective.length > 8 ? ' ...' : ''}` : '';
    element('plan-content').textContent = JSON.stringify(plan, null, 2);
    element('apply-button').textContent = removing ? '移出登记' : '保存登记';
    element('apply-button').disabled = !plan;
    renderDiagnostics(element('preview-diagnostics'), result.diagnostics);
    renderPlanDifference(plan);
    previewDialog.showModal();
  }

  // //// 显示由使用设置生成的声明与保存计划 [@x380kkm 2026-09-06] ////
  function showPlan(result, scope) {
    const next = result.plan;
    show(next.after || next.before, next.before, { id: next.before ? next.id : undefined, scope, path: next.catalog });
    removing = next.operation === 'remove';
    openPreview(result);
  }

  // //// 预览声明内容的保存差异 [@x380kkm 2026-09-06] ////
  async function preview() {
    const draft = readDraft();
    removing = false;
    try {
      const result = await request('document.preview', { document: draft, baseline, scope: catalogScope });
      openPreview(result);
      setStatus('变更预览已生成.');
    } catch (error) { await handleConflict(error); }
  }

  // //// 预览登记移除的实际范围 [@x380kkm 2026-09-06] ////
  async function previewRemoval() {
    if (!current || baseline === null || registeredId === null) return;
    removing = true;
    try {
      const result = await request('document.preview_remove', { id: registeredId, baseline, scope: catalogScope });
      openPreview(result);
      setStatus('登记移除预览已生成.');
    } catch (error) { await handleConflict(error); }
  }

  // //// 用独立比较保留发生冲突的本地编辑 [@x380kkm 2026-09-06] ////
  async function handleConflict(error) {
    if (error.code !== 'catalog-conflict') throw error;
    recoveryId = error.details?.documentId || recoveryId;
    if (!recoveryId) throw error;
    element('recover-button').hidden = false;
    if (removing) await mergeDialog.openRemoval({ id: recoveryId, baseline, localText: input.value, record, scope: catalogScope });
    else await recover();
  }

  // //// 取得最新登记并保持当前编辑基线 [@x380kkm 2026-09-06] ////
  async function recover() {
    if (recoveryId === null) return;
    await mergeDialog.open({ id: recoveryId, baseline, localText: input.value, record, scope: catalogScope });
  }

  // //// 显示最新移除计划并保留当前编辑基线 [@x380kkm 2026-09-06] ////
  function showRemovalPreview(result) {
    removing = true;
    openPreview(result);
    setStatus('最新登记的移除预览已生成. 当前编辑仍然保留.');
  }

  // //// 采用经过明确确认和核心预览的合并结果 [@x380kkm 2026-09-06] ////
  function adoptMerge(comparison) {
    const latestRecord = comparison.latest.document ? { ...comparison.record, id: comparison.id } : undefined;
    show(comparison.latest.document, comparison.latest.baseline, latestRecord || { scope: comparison.scope });
    input.value = JSON.stringify(comparison.document, null, 2);
    element('document-title').textContent = documentName(comparison.document);
    updateDirty();
    removing = false;
    onRecovered(latestRecord?.id || null);
    openPreview(comparison.preview);
    setStatus('合并结果已确认, 请核对保存预览.');
  }

  // //// 将固定计划交给管理核心保存 [@x380kkm 2026-09-06] ////
  async function apply() {
    if (!plan) return;
    const selectedPlan = plan;
    plan = null;
    element('apply-button').disabled = true;
    previewDialog.close();
    let result;
    try { result = await request('document.apply', { plan: selectedPlan }); }
    catch (error) { await handleConflict(error); return; }
    dirty = false;
    onDirty(false);
    try { await onApplied(result); }
    catch (error) {
      throw Object.assign(new Error(`登记变化已保存, 刷新目录失败: ${error.message}`), {
        code: 'refresh-after-apply', details: { id: result.id, cause: error.code, details: error.details },
      });
    }
    setStatus(result.document ? '声明已保存.' : '登记已移出, 来源文件保留.');
  }

  input.addEventListener('input', updateDirty);
  input.addEventListener('keydown', (event) => {
    if (event.key !== 'Tab' || input.readOnly) return;
    event.preventDefault();
    input.setRangeText('  ', input.selectionStart, input.selectionEnd, 'end');
    updateDirty();
  });
  element('format-button').addEventListener('click', () => run(async () => {
    input.value = JSON.stringify(readDraft(), null, 2);
    updateDirty();
    setStatus('JSON 格式已整理.');
  }));
  element('preview-button').addEventListener('click', () => run(preview));
  element('recover-button').addEventListener('click', () => run(recover));
  element('remove-button').addEventListener('click', () => run(previewRemoval));
  element('apply-button').addEventListener('click', () => run(apply));
  element('preview-close').addEventListener('click', () => previewDialog.close());
  element('schema-close').addEventListener('click', () => element('schema-dialog').close());
  element('schema-button').addEventListener('click', () => run(async () => {
    element('schema-content').textContent = JSON.stringify(await request('protocol.schema'), null, 2);
    element('schema-dialog').showModal();
    setStatus('声明 Schema 已读取.');
  }));

  return { show, showPlan, showReference, clear, isDirty: () => dirty || mergeDirty };
}
