// audience: internal
// # document-merge
// 比较读取基线, 本地编辑和最新登记. 用户确认的合并结果通过核心预览后交给编辑器.

import { documentName, element, renderDiagnostics, request } from './view-utils.mjs';

// //// 读取当前登记或确认该身份已无登记 [@x380kkm 2026-09-06] ////
async function readLatest(id, scope) {
  try { return await request('document.read', { id, scope }); }
  catch (error) {
    if (error.code === 'not_found') return { document: null, baseline: null };
    throw error;
  }
}

// //// 保留本地编辑并收集明确的合并结果 [@x380kkm 2026-09-06] ////
export function createMergeDialog({ run, onMerged, onRemovalPreview, onDirty, setStatus }) {
  const dialog = element('merge-dialog');
  const resultInput = element('merge-result');
  let comparison = null;
  let working = false;
  const mergeReview = {
    editable: true, allowsMissing: true, confirm: confirmMerge, label: '确认合并并预览',
    present: '选择内容作为起点, 核对并编辑合并结果. 确认后才更新编辑基线并生成预览.',
    missing: '该身份当前没有登记. 本地编辑保留在下方, 确认合并结果后将预览新增登记.',
  };
  const removalReview = {
    editable: false, allowsMissing: false, confirm: confirmRemoval, label: '确认移出并预览',
    present: '核对最新登记后重新预览移除范围. 当前编辑正文与基线保持原样.',
    missing: '该身份已经移出登记. 当前本地编辑保留, 可以返回编辑后刷新目录.',
  };

  // //// 显示最新登记及其比较条件 [@x380kkm 2026-09-06] ////
  function displayLatest() {
    element('merge-latest').textContent = comparison.latest.document
      ? JSON.stringify(comparison.latest.document, null, 2) : '该身份当前没有登记.';
    element('merge-use-latest').disabled = comparison.latest.document === null;
    element('merge-editing').hidden = !comparison.review.editable;
    element('merge-confirm').textContent = comparison.review.label;
    element('merge-confirm').disabled = comparison.latest.document === null && !comparison.review.allowsMissing;
    element('merge-description').textContent = comparison.latest.document ? comparison.review.present : comparison.review.missing;
  }

  // //// 打开独立于当前编辑基线的比较 [@x380kkm 2026-09-06] ////
  async function openComparison({ id, baseline, localText, record, scope }, review) {
    const latest = await readLatest(id, scope);
    comparison = { id, baseline, localText, record, scope, latest, review };
    element('merge-title').textContent = `比较最新登记: ${latest.document || baseline ? documentName(latest.document || baseline) : id}`;
    element('merge-original').textContent = baseline === null ? '读取时没有登记.' : JSON.stringify(baseline, null, 2);
    element('merge-local').textContent = localText;
    resultInput.value = '';
    onDirty(false);
    renderDiagnostics(element('merge-error'));
    displayLatest();
    dialog.showModal();
    setStatus('本地编辑已保留. 请核对当前登记.');
  }

  // //// 比较可明确确认的声明合并内容 [@x380kkm 2026-09-06] ////
  function open(input) {
    return openComparison(input, mergeReview);
  }

  // //// 比较移除操作对应的最新登记 [@x380kkm 2026-09-06] ////
  function openRemoval(input) {
    return openComparison(input, removalReview);
  }

  // //// 执行比较操作并在当前对话框中呈现错误 [@x380kkm 2026-09-06] ////
  function execute(action) {
    return run(async () => {
      working = true;
      dialog.inert = true;
      renderDiagnostics(element('merge-error'));
      try { await action(); }
      catch (error) {
        renderDiagnostics(element('merge-error'), [{ code: error.code, message: error.message }]);
        setStatus(error.message);
      } finally {
        working = false;
        dialog.inert = false;
      }
    });
  }

  // //// 预览用户明确提交的合并结果 [@x380kkm 2026-09-06] ////
  async function confirmMerge() {
    if (!comparison) return;
    let document;
    try { document = JSON.parse(resultInput.value); }
    catch { throw new Error('请先选择并编辑有效的合并 JSON.'); }
    if (!document || typeof document !== 'object' || Array.isArray(document)) throw new Error('合并结果需要是完整的声明对象.');
    const selected = comparison;
    const preview = await request('document.preview', { document, baseline: selected.latest.baseline, scope: selected.scope });
    dialog.close();
    onMerged({ ...selected, document, preview });
  }

  // //// 用最新登记预览用户确认的移除操作 [@x380kkm 2026-09-06] ////
  async function confirmRemoval() {
    if (!comparison?.latest.document) return;
    const preview = await request('document.preview_remove', { id: comparison.id, baseline: comparison.latest.baseline, scope: comparison.scope });
    dialog.close();
    onRemovalPreview(preview);
  }

  // //// 记录用户选择的合并工作文本 [@x380kkm 2026-09-06] ////
  function chooseResult(value) {
    resultInput.value = value;
    onDirty(Boolean(value.trim()));
  }

  element('merge-close').addEventListener('click', () => dialog.close());
  element('merge-use-latest').addEventListener('click', () => chooseResult(JSON.stringify(comparison.latest.document, null, 2)));
  element('merge-use-local').addEventListener('click', () => chooseResult(comparison.localText));
  resultInput.addEventListener('input', () => onDirty(Boolean(resultInput.value.trim())));
  element('merge-confirm').addEventListener('click', () => { if (comparison) return execute(comparison.review.confirm); });
  element('merge-reload').addEventListener('click', () => execute(async () => {
    comparison.latest = await readLatest(comparison.id, comparison.scope);
    displayLatest();
    setStatus('磁盘内容已重新读取. 请再次核对当前登记.');
  }));
  dialog.addEventListener('cancel', (event) => { if (working) event.preventDefault(); });
  dialog.addEventListener('close', () => { comparison = null; onDirty(false); });
  return { open, openRemoval };
}
