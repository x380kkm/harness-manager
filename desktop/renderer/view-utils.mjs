// audience: internal
// # view-utils
// 界面以文本节点呈现声明内容. 管理错误保留核心返回的代码和详情.

// //// 命名各声明目录的配置归属 [@x380kkm 2026-09-08] ////
export const catalogScopeNames = { user: '用户级默认', project: '项目共享设置', 'project-local': '项目个人设置' };

// //// 取得指定界面元素 [@x380kkm 2026-09-06] ////
export function element(id) {
  return document.getElementById(id);
}

// //// 提取桌面接口返回的结果 [@x380kkm 2026-09-06] ////
export async function unwrap(promise) {
  const response = await promise;
  if (!response.ok) {
    throw Object.assign(new Error(response.error.message), response.error);
  }
  return response.result;
}

// //// 通过受限接口发出管理请求 [@x380kkm 2026-09-06] ////
export async function request(method, params = {}) {
  const result = await unwrap(window.manager.call(method, params));
  const output = document.getElementById('host-sync-message');
  if (result?.hostSync && output) {
    const sync = result.hostSync;
    output.hidden = false;
    output.classList.toggle('sync-blocked', sync.status === 'blocked');
    const message = textElement('span', [sync.message, ...(sync.diagnostics || []).map(diagnosticMessage)].join(' '));
    const close = textElement('button', '关闭', 'text-button'); close.setAttribute('aria-label', '关闭操作提示');
    close.addEventListener('click', () => { output.hidden = true; });
    output.replaceChildren(message, close);
  }
  return result;
}

// //// 刷新已保存内容并显示独立的读取结果 [@x380kkm 2026-09-08] ////
export async function refreshAfterSave(refresh, setStatus, savedMessage) {
  try { await refresh(); }
  catch (error) { setStatus(`${savedMessage} 刷新目录失败: ${error.message || String(error)}`); return; }
  setStatus(savedMessage);
}

// //// 创建带纯文本内容的元素 [@x380kkm 2026-09-06] ////
export function textElement(tag, value, className) {
  const node = document.createElement(tag);
  node.textContent = value == null ? '' : String(value);
  if (className) node.className = className;
  return node;
}

// //// 提取声明的用户可读名称 [@x380kkm 2026-09-06] ////
export function documentName(value) {
  return value?.metadata?.name || value?.name || value?.id || '声明';
}

// //// 将结构化值显示为文本 [@x380kkm 2026-09-06] ////
export function displayValue(value) {
  if (value == null || value === '') return '-';
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
}

// //// 在诊断正文中标明配置范围与引用位置 [@x380kkm 2026-09-08] ////
export function diagnosticMessage(diagnostic) {
  const message = diagnostic.message || displayValue(diagnostic);
  const detail = diagnostic.origin ? `${message} (引用: ${diagnostic.origin})` : message;
  return diagnostic.scope ? `${catalogScopeNames[diagnostic.scope] || diagnostic.scope}: ${detail}` : detail;
}

// //// 显示管理核心的诊断信息 [@x380kkm 2026-09-06] ////
export function renderDiagnostics(container, diagnostics = []) {
  container.replaceChildren();
  container.hidden = diagnostics.length === 0;
  for (const diagnostic of diagnostics) {
    const item = textElement('div', diagnosticMessage(diagnostic), 'diagnostic');
    if (diagnostic.code) item.prepend(textElement('code', diagnostic.code, 'diagnostic-code'));
    container.append(item);
  }
}
