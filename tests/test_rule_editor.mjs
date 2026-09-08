// audience: internal
// # rule-editor-behavior
// 规则编辑使用内存表单和可控保存响应验证片段位置, 正文预览与草稿保留.

import assert from 'node:assert/strict';
import test from 'node:test';
import { createRuleEditor } from '../desktop/renderer/rule-editor.mjs';

// //// 提供规则表单使用的元素和事件 [@x380kkm 2026-09-08] ////
class Element {
  // //// 初始化表单元素 [@x380kkm 2026-09-08] ////
  constructor(tagName) {
    this.tagName = tagName;
    this.children = [];
    this.attributes = new Map();
    this.listeners = new Map();
    this.classes = new Set();
    this.classList = { toggle: (name, value) => value ? this.classes.add(name) : this.classes.delete(name) };
    this.value = '';
    this.textContent = '';
  }
  // //// 读取表单文本 [@x380kkm 2026-09-08] ////
  get value() { return this.textValue; }
  // //// 写入表单文本并规范化文本框换行 [@x380kkm 2026-09-08] ////
  set value(value) { this.textValue = this.tagName === 'textarea' ? value.replace(/\r\n?/g, '\n') : value; }
  // //// 保存元素属性 [@x380kkm 2026-09-08] ////
  setAttribute(name, value) { this.attributes.set(name, value); }
  // //// 追加表单节点 [@x380kkm 2026-09-08] ////
  append(...nodes) { this.children.push(...nodes); }
  // //// 替换表单节点 [@x380kkm 2026-09-08] ////
  replaceChildren(...nodes) { this.children = nodes; }
  // //// 注册表单事件 [@x380kkm 2026-09-08] ////
  addEventListener(name, listener) {
    this.listeners.set(name, [...(this.listeners.get(name) || []), listener]);
  }
  // //// 调用表单事件并保留取消状态 [@x380kkm 2026-09-08] ////
  async emit(name) {
    const event = { target: this, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
    for (const listener of this.listeners.get(name) || []) await listener(event);
    return event;
  }
  // //// 查找符合条件的后代节点 [@x380kkm 2026-09-08] ////
  find(predicate) {
    for (const child of this.children) {
      if (predicate(child)) return child;
      const found = child.find(predicate);
      if (found) return found;
    }
  }
  // //// 打开表单对话框 [@x380kkm 2026-09-08] ////
  showModal() { this.open = true; }
  // //// 关闭表单并通知草稿状态 [@x380kkm 2026-09-08] ////
  close() {
    this.open = false;
    for (const listener of this.listeners.get('close') || []) listener();
  }
}

// //// 建立包含目标成员和相邻成员的规则声明 [@x380kkm 2026-09-08] ////
function ruleRecord({ text, texts, parts, mapped = true }) {
  const document = {
    id: 'plugin:personal/rules', kind: 'Plugin', release: { version: '1.0.0' }, metadata: { name: '写作规则' },
    contributions: [
      { id: 'adjacent', kind: 'rule', payload: { name: '相邻规则', text: '相邻正文' } },
      { id: 'writing', kind: 'rule', payload: { name: '写作规则', text, fragmentTexts: [...texts] } },
    ],
  };
  return { document, baseline: structuredClone(document), scope: 'user', memberId: 'writing', name: '写作规则', texts, parts, mapped };
}

// //// 将规则编辑接入可控读取和保存响应 [@x380kkm 2026-09-08] ////
async function openRule(context, record) {
  const previous = { document: globalThis.document, window: globalThis.window };
  context.after(() => {
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete globalThis[key]; else globalThis[key] = value;
    }
  });
  const body = new Element('body'), hostMessage = new Element('div');
  const state = { calls: [], dirty: false, status: '', saved: 0, confirmed: false, confirmations: [], previewError: null, previewDiagnostics: [], applyResult: {} };
  globalThis.document = { body, createElement: (tag) => new Element(tag), getElementById: () => hostMessage };
  globalThis.window = {
    confirm: (message) => { state.confirmations.push(message); return state.confirmed; },
    manager: {
      call: async (method, params) => {
        state.calls.push({ method, params: structuredClone(params) });
        if (method === 'rule.describe') return { ok: true, result: structuredClone(record) };
        if (method === 'document.preview') return state.previewError
          ? { ok: false, error: { code: 'catalog-conflict', message: state.previewError } }
          : { ok: true, result: { plan: { after: structuredClone(params.document) }, diagnostics: state.previewDiagnostics } };
        if (method === 'document.apply') {
          state.applied = structuredClone(params.plan.after);
          return { ok: true, result: state.applyResult };
        }
        throw new Error(`Unexpected management call: ${method}`);
      },
    },
  };
  const editor = createRuleEditor({
    run: (action) => action(),
    onSaved: () => { state.saved += 1; if (state.refreshError) throw new Error(state.refreshError); },
    onDirty: (value) => { state.dirty = value; },
    setStatus: (value) => { state.status = value; },
  });
  const management = { documentId: 'plugin:personal/rules@1.0.0', ref: 'plugin:personal/rules#writing' };
  await editor.open({ management, name: '卡片名称' });
  const dialog = body.children[0];
  return {
    state, dialog, hostMessage, management,
    input: (label) => dialog.find((node) => node.attributes.get('aria-label') === label),
    save: dialog.find((node) => node.className === 'primary'),
    preview: dialog.find((node) => node.className === 'rule-editor-preview'),
    feedback: dialog.find((node) => node.className === 'rule-editor-feedback'),
  };
}

// //// 输入正文并触发表单更新 [@x380kkm 2026-09-08] ////
async function enter(input, value) { input.value = value; await input.emit('input'); }

// //// 按正文位置保存片段并保留重复替换值和 Unicode 文本 [@x380kkm 2026-09-08] ////
test('片段按来源顺序编辑, 正文按位置组装并同步保存名称', async (context) => {
  const record = ruleRecord({
    text: '# 手册🙂\n\n后🙂\n\n---\n\n前🙂\n\n尾声🧭\n', texts: ['前🙂\n', '后🙂\n'],
    parts: [{ text: '# 手册🙂\n\n' }, { fragment: 1 }, { text: '\n---\n\n' }, { fragment: 0 }, { text: '\n尾声🧭\n' }],
  });
  const form = await openRule(context, record);
  assert.deepEqual(form.state.calls[0], { method: 'rule.describe', params: { id: form.management.documentId, ref: form.management.ref, scope: 'user' } });
  assert.equal(form.input('规则名称').value, '写作规则');
  assert.equal(form.input('规则片段 1').value, '前🙂\n');
  assert.equal(form.input('规则片段 2').value, '后🙂\n');
  assert.equal(form.state.dirty, false);
  assert.equal(form.save.disabled, true);
  await enter(form.input('规则片段 1'), '后🙂\n');
  await enter(form.input('规则片段 2'), '新结尾🧭\n\n下一行\n');
  await enter(form.input('规则名称'), '完整规则');
  const preview = form.preview.children[0].innerHTML;
  assert.match(preview, /<h1>手册🙂<\/h1>/);
  assert.match(preview, /下一行/);
  assert.ok(preview.indexOf('新结尾🧭') < preview.indexOf('后🙂'));
  assert.match(preview, /尾声🧭/);
  await form.save.emit('click');
  const payload = form.state.applied.contributions[1].payload;
  assert.equal(payload.text, '# 手册🙂\n\n新结尾🧭\n\n下一行\n\n---\n\n后🙂\n\n尾声🧭\n');
  assert.deepEqual(payload.fragmentTexts, ['后🙂\n', '新结尾🧭\n\n下一行\n']);
  assert.equal(payload.name, '完整规则');
  assert.equal(form.state.applied.metadata.name, '完整规则');
  assert.deepEqual(form.state.applied.contributions[0], record.document.contributions[0]);
  const previewCall = form.state.calls.find((call) => call.method === 'document.preview');
  assert.equal(previewCall.params.scope, 'user');
  assert.deepEqual(previewCall.params.baseline, record.baseline);
  assert.equal(form.dialog.open, false);
  assert.equal(form.state.dirty, false);
  assert.equal(form.state.saved, 1);
});

// //// 聚合正文相同的片段调整保留保存与退出保护 [@x380kkm 2026-09-08] ////
test('片段重新分配但完整正文相同时仍可保存并保留草稿', async (context) => {
  const record = ruleRecord({ text: 'Left.Right.', texts: ['Left.', 'Right.'], parts: [{ fragment: 0 }, { fragment: 1 }] });
  delete record.document.contributions[1].payload.fragmentTexts;
  delete record.baseline.contributions[1].payload.fragmentTexts;
  const form = await openRule(context, record);
  const originalPreview = form.preview.children[0].innerHTML;
  assert.equal(form.state.dirty, false);
  await enter(form.input('规则片段 1'), 'Left.Right.');
  await enter(form.input('规则片段 2'), '');
  assert.equal(form.preview.children[0].innerHTML, originalPreview);
  assert.equal(form.state.dirty, true);
  assert.equal(form.save.disabled, false);
  assert.equal((await form.dialog.emit('cancel')).defaultPrevented, true);
  await enter(form.input('规则片段 1'), 'Left.');
  await enter(form.input('规则片段 2'), 'Right.');
  assert.equal(form.state.dirty, false);
  assert.equal(form.save.disabled, true);
  await enter(form.input('规则片段 1'), 'Left.Right.');
  await enter(form.input('规则片段 2'), '');
  await form.save.emit('click');
  assert.equal(form.state.applied.contributions[1].payload.text, record.document.contributions[1].payload.text);
  assert.deepEqual(form.state.applied.contributions[1].payload.fragmentTexts, ['Left.Right.', '']);
  assert.equal(form.state.dirty, false);
  assert.equal(form.dialog.open, false);
});

// //// 可选元数据缺失时保存名称与正文并沿用原始基线 [@x380kkm 2026-09-08] ////
test('可选元数据缺失的合法规则仍可通过表单保存', async (context) => {
  const record = ruleRecord({ text: 'Original policy.', texts: ['Original policy.'], parts: [{ fragment: 0 }], mapped: false });
  delete record.document.metadata;
  delete record.baseline.metadata;
  const form = await openRule(context, record);
  await enter(form.input('规则名称'), '更新的规则名称');
  await enter(form.input('规则正文'), 'Updated policy.');
  await form.save.emit('click');
  assert.deepEqual(form.state.applied.metadata, { name: '更新的规则名称' });
  assert.equal(form.state.applied.contributions[1].payload.text, 'Updated policy.');
  const previewCall = form.state.calls.find((call) => call.method === 'document.preview');
  assert.deepEqual(previewCall.params.baseline, record.baseline);
  assert.equal(Object.hasOwn(previewCall.params.baseline, 'metadata'), false);
  assert.equal(form.dialog.open, false);
  assert.equal(form.state.saved, 1);
});

// //// 保存单个空片段与全部空片段 [@x380kkm 2026-09-08] ////
test('片段分别清空和全部清空均可保存', async (context) => {
  for (const values of [['', '乙'], ['', '']]) {
    await context.test(values[1] ? '清空一个片段' : '清空全部片段', async (nested) => {
      const form = await openRule(nested, ruleRecord({ text: '甲乙', texts: ['甲', '乙'], parts: [{ fragment: 0 }, { fragment: 1 }] }));
      await enter(form.input('规则片段 1'), values[0]);
      await enter(form.input('规则片段 2'), values[1]);
      assert.equal(form.save.disabled, false);
      await form.save.emit('click');
      assert.deepEqual(form.state.applied.contributions[1].payload.fragmentTexts, values);
      assert.equal(form.state.applied.contributions[1].payload.text, values[1]);
    });
  }
});

// //// 使用单正文表单保存映射片段与普通规则 [@x380kkm 2026-09-08] ////
test('单片段保持普通正文输入, 普通规则移除片段替换值', async (context) => {
  for (const mapped of [true, false]) {
    await context.test(mapped ? '单片段规则' : '普通规则', async (nested) => {
      const form = await openRule(nested, ruleRecord({ text: '正文', texts: ['正文'], parts: [{ fragment: 0 }], mapped }));
      assert.equal(form.input('规则片段 1'), undefined);
      await enter(form.input('规则正文'), '编辑后的正文🙂\n');
      form.state.applyResult = { hostSync: { status: 'blocked', message: '宿主文件存在冲突.', diagnostics: [{ message: '请核对来源内容.' }] } };
      await form.save.emit('click');
      const payload = form.state.applied.contributions[1].payload;
      assert.equal(payload.text, '编辑后的正文🙂\n');
      if (mapped) assert.deepEqual(payload.fragmentTexts, ['编辑后的正文🙂\n']);
      else assert.equal(Object.hasOwn(payload, 'fragmentTexts'), false);
      assert.equal(form.hostMessage.hidden, false);
      assert.equal(form.hostMessage.classes.has('sync-blocked'), true);
      assert.equal(form.hostMessage.children[0].textContent, '宿主文件存在冲突. 请核对来源内容.');
    });
  }
});

// //// 保留已有聚合正文并允许确认按片段恢复 [@x380kkm 2026-09-08] ////
test('正文与片段不一致时显示可复制原文并直接确认恢复', async (context) => {
  const record = ruleRecord({ text: '单独编辑的原文🙂\n\n末尾\n', texts: ['甲', '乙'], parts: [{ fragment: 0 }, { text: '\n\n' }, { fragment: 1 }] });
  record.unmappedText = record.document.contributions[1].payload.text;
  const form = await openRule(context, record);
  const original = form.input('已保存正文');
  assert.equal(original.readOnly, true);
  assert.equal(original.value, record.unmappedText);
  assert.equal(form.state.dirty, true);
  assert.equal(form.save.disabled, false);
  assert.equal(form.save.textContent, '确认保存片段正文');
  assert.ok(form.dialog.find((node) => node.textContent.includes('保存将采用右侧预览中的正文.')));
  await form.save.emit('click');
  assert.equal(form.state.applied.contributions[1].payload.text, '甲\n\n乙');
  assert.deepEqual(form.state.applied.contributions[1].payload.fragmentTexts, ['甲', '乙']);
  assert.equal(original.value, record.unmappedText);
});

// //// 在保存冲突与取消关闭时保留片段草稿 [@x380kkm 2026-09-08] ////
test('保存冲突保留片段草稿, 取消关闭保持未保存状态', async (context) => {
  const record = ruleRecord({ text: '甲乙', texts: ['甲', '乙'], parts: [{ fragment: 0 }, { fragment: 1 }] });
  const form = await openRule(context, record);
  await enter(form.input('规则片段 1'), '草稿🙂\n');
  await enter(form.input('规则片段 2'), '');
  form.state.previewError = '来源声明已变化.';
  await form.save.emit('click');
  assert.equal(form.dialog.open, true);
  assert.equal(form.dialog.inert, false);
  assert.equal(form.state.dirty, true);
  assert.equal(form.save.disabled, false);
  assert.equal(form.input('规则片段 1').value, '草稿🙂\n');
  assert.equal(form.input('规则片段 2').value, '');
  assert.match(form.preview.children[0].innerHTML, /草稿🙂/);
  assert.equal(form.feedback.textContent, '来源声明已变化. 草稿仍保留.');
  assert.equal(form.state.calls.some((call) => call.method === 'document.apply'), false);
  assert.equal((await form.dialog.emit('cancel')).defaultPrevented, true);
  assert.equal(form.state.dirty, true);
  await form.save.emit('click');
  const previews = form.state.calls.filter((call) => call.method === 'document.preview');
  assert.deepEqual(previews[1].params.baseline, record.baseline);
  assert.deepEqual(previews[1].params.document.contributions[1].payload.fragmentTexts, ['草稿🙂\n', '']);
});

// //// 修改名称时保留正文的原始换行 [@x380kkm 2026-09-08] ////
test('文本框规范化换行后保持初始未修改状态, 名称修改保留原文', async (context) => {
  const record = ruleRecord({ text: '\r\n# 标题\r\n\r\n甲🙂\r\n', texts: ['甲🙂\r\n'], parts: [{ text: '\r\n# 标题\r\n\r\n' }, { fragment: 0 }] });
  const form = await openRule(context, record);
  assert.equal(form.input('规则正文').value, '甲🙂\n');
  assert.equal(form.state.dirty, false);
  assert.equal(form.save.disabled, true);
  await enter(form.input('规则名称'), '新名称');
  await form.save.emit('click');
  assert.equal(form.state.applied.contributions[1].payload.text, record.document.contributions[1].payload.text);
  assert.deepEqual(form.state.applied.contributions[1].payload.fragmentTexts, ['甲🙂\r\n']);
});

// //// 规则写入完成后保持保存状态并显示刷新失败 [@x380kkm 2026-09-08] ////
test('规则保存成功后刷新失败仍显示已保存状态', async (context) => {
  const form = await openRule(context, ruleRecord({ text: '原文', texts: ['原文'], parts: [{ fragment: 0 }] }));
  form.state.refreshError = '目录读取中断.';
  await enter(form.input('规则正文'), '保存正文');
  await form.save.emit('click');
  assert.equal(form.state.applied.contributions[1].payload.text, '保存正文');
  assert.equal(form.state.dirty, false);
  assert.equal(form.dialog.open, false);
  assert.match(form.state.status, /已保存.*刷新目录失败: 目录读取中断/);
  assert.equal(form.state.calls.filter((call) => call.method === 'document.apply').length, 1);
});

// //// 规则保存前保留项目影响的确认选择 [@x380kkm 2026-09-08] ////
test('共享发布的项目冲突先显示并确认, 取消保留规则草稿', async (context) => {
  const form = await openRule(context, ruleRecord({ text: '原文', texts: ['原文'], parts: [{ fragment: 0 }] }));
  const note = { severity: 'error', code: 'catalog_identity_conflict', scope: 'project', origin: 'binding:working', message: '同一发布包含不同正文.' };
  form.state.previewDiagnostics = [note];
  form.state.applyResult = { hostSync: { status: 'blocked', message: '用户配置已保存, 项目应用需要处理.', diagnostics: [note] } };
  await enter(form.input('规则正文'), '修改正文');
  await form.save.emit('click');
  assert.match(form.state.confirmations[0], /项目共享设置: 同一发布包含不同正文/);
  assert.equal(form.feedback.textContent, '项目共享设置: 同一发布包含不同正文. (引用: binding:working)');
  assert.equal(form.state.calls.some((call) => call.method === 'document.apply'), false);
  assert.equal(form.state.dirty, true);
  assert.equal(form.dialog.open, true);
  assert.equal(form.dialog.inert, false);
  form.state.confirmed = true;
  await form.save.emit('click');
  assert.equal(form.state.applied.contributions[1].payload.text, '修改正文');
  assert.equal(form.state.dirty, false);
  assert.equal(form.hostMessage.hidden, false);
  assert.equal(form.hostMessage.classes.has('sync-blocked'), true);
  assert.match(form.hostMessage.children[0].textContent, /项目共享设置: 同一发布包含不同正文/);
});
