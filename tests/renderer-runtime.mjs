// audience: internal
// # renderer-runtime

// //// 为界面状态提供最小 DOM 行为 [@x380kkm 2026-09-06] ////
export class Element {
  // //// 初始化元素的状态与事件容器 [@x380kkm 2026-09-06] ////
  constructor(tagName = 'div') {
    this.tagName = tagName;
    this.children = [];
    this.attributes = new Map();
    this.listeners = new Map();
    this.dataset = {};
    this.value = '';
    this.textContent = '';
    this.hidden = false;
    this.clientWidth = 800;
    this.clientHeight = 600;
    this.classes = new Set();
    this.classList = {
      add: (name) => this.classes.add(name),
      remove: (name) => this.classes.delete(name),
      toggle: (name, value) => { if (value) this.classes.add(name); else this.classes.delete(name); },
    };
  }
  get childNodes() { return this.children; }
  // //// 记录元素属性 [@x380kkm 2026-09-06] ////
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  // //// 读取元素属性 [@x380kkm 2026-09-06] ////
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  // //// 追加子元素 [@x380kkm 2026-09-06] ////
  append(...nodes) { for (const node of nodes) node.parentNode = this; this.children.push(...nodes); }
  // //// 替换子元素 [@x380kkm 2026-09-06] ////
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  // //// 将节点插入内容开头 [@x380kkm 2026-09-06] ////
  prepend(node) { this.children.unshift(node); }
  // //// 记录事件处理函数 [@x380kkm 2026-09-06] ////
  addEventListener(name, listener) {
    const listeners = this.listeners.get(name) || [];
    listeners.push(listener);
    this.listeners.set(name, listeners);
  }
  // //// 调用当前事件的处理函数 [@x380kkm 2026-09-06] ////
  async emit(name) {
    for (const listener of this.listeners.get(name) || []) await listener({ target: this, preventDefault() {} });
  }
  // //// 查找图中的可选择节点 [@x380kkm 2026-09-06] ////
  querySelectorAll(selector = '[data-node-id]') {
    return this.children.flatMap((node) => [...(selector.split(',').some((part) => node.matches(part.trim())) ? [node] : []), ...node.querySelectorAll(selector)]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  matches(selector) {
    const parts = selector.split(/\s+>\s+|\s+/);
    if (parts.length > 1) {
      if (!this.matches(parts.pop())) return false;
      for (let parent = this.parentNode; parent; parent = parent.parentNode) if (parent.matches(parts.join(' '))) return true;
      return false;
    }
    if (selector.startsWith('.')) return this.classes.has(selector.slice(1)) || (this.className || '').split(' ').includes(selector.slice(1));
    const attribute = /^\[([^=\]]+)(?:="([^"]*)")?\]$/.exec(selector);
    if (attribute) {
      const value = this.getAttribute(attribute[1]) ?? this.dataset[attribute[1].replace(/^data-/, '').replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())];
      return value !== null && value !== undefined && (attribute[2] === undefined || value === attribute[2]);
    }
    return this.tagName === selector;
  }
  focus() {}
  scrollIntoView() {}
  // //// 打开对话框 [@x380kkm 2026-09-06] ////
  showModal() { this.open = true; }
  // //// 关闭对话框并通知处理函数 [@x380kkm 2026-09-06] ////
  close() {
    this.open = false;
    for (const listener of this.listeners.get('close') || []) listener();
  }
}

// //// 安装可控响应并保持测试之间的 DOM 隔离 [@x380kkm 2026-09-07] ////
export function rendererRuntime(context, call) {
  const elements = new Map();
  const get = (id) => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  const originals = Object.fromEntries(['document', 'window', 'localStorage', 'CSS'].map((name) => [name, globalThis[name]]));
  context.after(() => { for (const [name, value] of Object.entries(originals)) { if (value === undefined) delete globalThis[name]; else globalThis[name] = value; } });
  globalThis.document = { getElementById: get, createElement: (tag) => new Element(tag), createElementNS: (_, tag) => new Element(tag), body: new Element(), documentElement: new Element() };
  globalThis.localStorage = { getItem: () => null, setItem() {} };
  globalThis.CSS = { escape: (value) => value };
  globalThis.window = { confirm: () => false, manager: { setDirty() {}, getContext: async () => ({ ok: true, result: { workspace: null } }),
    call: async (method, params) => ({ ok: true, result: await call(method, params) }) } };
  get('inventory-workspace').append(get('inventory-kinds'), get('inventory-list'), get('inventory-official-list'), get('inventory-inspector'));
  return get;
}
