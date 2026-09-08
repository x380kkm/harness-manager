// audience: internal
// # rule-editor
// 规则片段按来源声明的顺序编辑, 完整正文按来源位置组装并使用读取基线保存.

import { diagnosticMessage, refreshAfterSave, request, textElement } from './view-utils.mjs';
import { markdownView } from './markdown-view.mjs';

// //// 追加带名称的正文输入框 [@x380kkm 2026-09-08] ////
function appendTextArea(form, title, value) {
  const input = document.createElement('textarea'); input.rows = 14; input.value = value; input.setAttribute('aria-label', title);
  const field = textElement('label', '', 'rule-editor-field'); field.append(textElement('span', `${title} / Markdown`), input);
  form.append(field);
  return input;
}

// //// 编辑用户拥有的规则副本并预览正文 [@x380kkm 2026-09-08] ////
export function createRuleEditor({ run, onSaved, onDirty, setStatus }) {
  const dialog = textElement('dialog', '', 'rule-dialog');
  dialog.setAttribute('aria-label', '编辑规则正文'); document.body.append(dialog);
  let initial = '', fields = [], parts = [], name;
  const fragmentTexts = () => fields.map(({ input, initialValue, text }) => input.value === initialValue ? text : input.value);
  const bodyText = () => {
    const texts = fragmentTexts();
    return parts.map((part) => 'text' in part ? part.text : texts[part.fragment]).join('');
  };
  const snapshot = () => JSON.stringify([name?.value, bodyText(), fragmentTexts()]);
  const dirty = () => snapshot() !== initial;
  const canLeave = () => !dirty() || window.confirm('规则正文尚未保存. 放弃修改?');

  // //// 从受管来源声明打开可编辑片段和完整预览 [@x380kkm 2026-09-08] ////
  async function open(item) {
    const record = await request('rule.describe', { id: item.management.documentId, ref: item.management.ref, scope: 'user' });
    const draft = structuredClone(record.document), member = draft.contributions.find((value) => value.id === record.memberId);
    if (typeof member?.payload?.text !== 'string') throw new Error('此来源的正文由外部文件维护.');
    dialog.replaceChildren();
    const heading = textElement('div', '', 'dialog-heading');
    const close = textElement('button', '取消'); close.addEventListener('click', () => { if (canLeave()) dialog.close(); });
    heading.append(textElement('h2', '编辑规则正文'), close);
    const body = textElement('div', '', 'rule-editor-body');
    const form = textElement('div', '', 'rule-editor-fields');
    name = document.createElement('input'); name.value = record.name; name.setAttribute('aria-label', '规则名称');
    initial = JSON.stringify([name.value, member.payload.text, record.texts]);
    const label = textElement('label', '', 'rule-editor-field'); label.append(textElement('span', '规则名称'), name);
    form.append(label);
    if (record.unmappedText !== undefined) {
      form.append(textElement('p', '已保存正文与片段正文不同. 下方原文可供对照和复制. 保存将采用右侧预览中的正文.', 'card-hint'));
      const original = appendTextArea(form, '已保存正文', record.unmappedText); original.readOnly = true; original.rows = 8;
    }
    parts = record.parts;
    fields = record.texts.map((text, index) => {
      const input = appendTextArea(form, record.texts.length > 1 ? `规则片段 ${index + 1}` : '规则正文', text);
      if (record.texts.length > 1) input.rows = 8;
      return { input, initialValue: input.value, text };
    });
    form.append(textElement('p', '保存到 Manager 用户规则副本. 原始文件保留原位.', 'card-hint'));
    const preview = textElement('div', '', 'rule-editor-preview');
    const feedback = textElement('p', '', 'rule-editor-feedback'); feedback.setAttribute('aria-live', 'polite');
    const actions = textElement('div', '', 'dialog-actions');
    const save = textElement('button', record.unmappedText !== undefined ? '确认保存片段正文' : '保存正文', 'primary'); save.disabled = true;
    const update = () => { preview.replaceChildren(markdownView(bodyText())); onDirty(dirty()); save.disabled = !dirty() || !name.value.trim() || (!record.mapped && !bodyText().trim()); };
    for (const { input } of fields) input.addEventListener('input', update);
    name.addEventListener('input', update);
    save.addEventListener('click', () => run(async () => {
      dialog.inert = true;
      try {
        draft.metadata ??= {};
        draft.metadata.name = name.value; member.payload.name = name.value; member.payload.text = bodyText();
        if (record.mapped) member.payload.fragmentTexts = fragmentTexts();
        else delete member.payload.fragmentTexts;
        const result = await request('document.preview', { document: draft, baseline: record.baseline, scope: 'user' });
        if (result.diagnostics?.length) {
          feedback.textContent = result.diagnostics.map(diagnosticMessage).join('\n');
          if (!window.confirm(`保存规则会影响以下配置:\n${feedback.textContent}\n\n继续保存当前规则?`)) return;
        }
        await request('document.apply', { plan: result.plan });
        initial = snapshot(); onDirty(false); dialog.close();
        await refreshAfterSave(onSaved, setStatus, 'Manager 规则正文已保存.');
      } catch (error) { feedback.textContent = `${error.message} 草稿仍保留.`; }
      finally { dialog.inert = false; }
    }));
    actions.append(textElement('span', '保存前可核对右侧正文'), save); body.append(form, preview);
    dialog.append(heading, body, feedback, actions); update(); dialog.showModal();
  }
  dialog.addEventListener('cancel', (event) => { if (!canLeave()) event.preventDefault(); });
  dialog.addEventListener('close', () => onDirty(false));
  return { open };
}
