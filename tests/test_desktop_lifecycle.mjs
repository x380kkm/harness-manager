// audience: internal
// # desktop-lifecycle-tests
// 事件替身遵循 Electron 的卸载与退出顺序. 管理入口和渲染器使用实际源码.

import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { registerHooks } from 'node:module';
import test from 'node:test';
import { rendererRuntime } from './renderer-runtime.mjs';

let runtimeId = 0;

// //// 派发可取消的原生事件 [@x380kkm 2026-09-08] ////
function dispatch(emitter, name) {
  const event = { defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
  emitter.emit(name, event);
  return event;
}

// //// 隔离主进程的窗口与后台连接 [@x380kkm 2026-09-08] ////
async function mainRuntime(context) {
  const runtime = { confirmations: 0, answer: 0, selection: { canceled: true }, events: [] };
  const windows = new Set(), handlers = new Map(), app = new EventEmitter(), ipcMain = new EventEmitter();
  app.whenReady = () => Promise.resolve();
  app.quit = () => {
    if (dispatch(app, 'before-quit').defaultPrevented) return;
    app.closingWindows = true;
    for (const window of windows) {
      if (!window.close()) { app.closingWindows = false; return; }
    }
    app.closingWindows = false;
    if (!dispatch(app, 'will-quit').defaultPrevented) app.exited = true;
  };
  ipcMain.handle = (name, handler) => handlers.set(name, handler);

  // //// 按关闭和刷新事件执行页面卸载 [@x380kkm 2026-09-08] ////
  class BrowserWindow extends EventEmitter {
    constructor() {
      super();
      this.webContents = new EventEmitter();
      this.webContents.mainFrame = { url: 'harness://app/index.html' };
      this.webContents.setWindowOpenHandler = () => {};
      windows.add(this); runtime.window = this;
    }
    setMenuBarVisibility(value) { this.menuVisible = value; }
    loadURL() { return Promise.resolve(); }
    unload() {
      if (this.hasDraft && !dispatch(this.webContents, 'will-prevent-unload').defaultPrevented) return false;
      this.hasDraft = false;
      return true;
    }
    reload() { if (!this.unload()) return false; this.reloaded = true; return true; }
    close() {
      if (dispatch(this, 'close').defaultPrevented || !this.unload()) return false;
      windows.delete(this); this.emit('closed');
      if (!windows.size && !app.closingWindows) app.emit('window-all-closed');
      return true;
    }
  }

  // //// 记录已接收请求和后端关闭的实际顺序 [@x380kkm 2026-09-08] ////
  class ManagerProcess {
    constructor() { this.closed = 0; runtime.manager = this; }
    start() { this.child = {}; }
    request(method, params) { runtime.events.push(method); return runtime.response?.(method, params) ?? Promise.resolve({ method }); }
    close() { this.closed += 1; this.child = null; runtime.events.push('backend-close'); return Promise.resolve(); }
  }

  runtime.electron = { app, BrowserWindow, ipcMain, net: {},
    dialog: { showMessageBoxSync() { runtime.confirmations += 1; return runtime.answer; },
      showOpenDialog: async () => runtime.selection, showErrorBox: (_, message) => { throw new Error(message); } },
    protocol: { registerSchemesAsPrivileged() {}, handle() {} },
    session: { defaultSession: { setPermissionRequestHandler() {}, setPermissionCheckHandler() {} } } };
  runtime.ManagerProcess = ManagerProcess;
  const identity = `desktopLifecycle${++runtimeId}`;
  const mainUrl = new URL(`../desktop/main/main.mjs?${identity}`, import.meta.url).href;
  globalThis[identity] = runtime;
  const hooks = registerHooks({
    resolve(specifier, environment, next) {
      if (environment.parentURL === mainUrl && ['electron', './rpc.mjs'].includes(specifier)) {
        return { url: `fixture:${identity}/${specifier}`, shortCircuit: true };
      }
      return next(specifier, environment);
    },
    load(url, environment, next) {
      if (url === `fixture:${identity}/electron`) return { format: 'module', shortCircuit: true,
        source: `export const { app, BrowserWindow, dialog, ipcMain, net, protocol, session } = globalThis.${identity}.electron;` };
      if (url === `fixture:${identity}/./rpc.mjs`) return { format: 'module', shortCircuit: true,
        source: `export const { ManagerProcess } = globalThis.${identity}; export const failure = (code, message, details) => Object.assign(new Error(message), { code, details });` };
      return next(url, environment);
    },
  });
  try { await import(mainUrl); await new Promise(setImmediate); }
  finally { hooks.deregister(); delete globalThis[identity]; }
  runtime.app = app;
  runtime.sender = () => ({ sender: runtime.window.webContents, senderFrame: runtime.window.webContents.mainFrame });
  runtime.sendDirty = (value) => ipcMain.emit('manager:dirty', runtime.sender(), value);
  runtime.call = (method, params = {}) => handlers.get('manager:call')(runtime.sender(), method, params);
  runtime.chooseProject = () => handlers.get('manager:select-workspace')(runtime.sender());
  context.after(() => { windows.clear(); });
  return runtime;
}

// //// 页面刷新使用渲染器的卸载阻止并保留取消状态 [@x380kkm 2026-09-08] ////
test('刷新在 dirty 通知尚未到达时仍确认, 取消后继续使用后台', async (context) => {
  const runtime = await mainRuntime(context);
  runtime.window.hasDraft = true;
  assert.equal(runtime.window.reload(), false);
  assert.equal(runtime.window.hasDraft, true);
  assert.equal((await runtime.call('document.read')).ok, true);
  assert.equal(runtime.manager.closed, 0);
  runtime.answer = 1;
  assert.equal(runtime.window.reload(), true);
  assert.equal(runtime.confirmations, 2);
  assert.equal(runtime.manager.closed, 0);
  runtime.selection = { canceled: false, filePaths: [process.cwd()] };
  assert.equal((await runtime.chooseProject()).ok, true);
  assert.equal(runtime.confirmations, 2);
});

// //// 窗口关闭和菜单退出共享一次草稿确认 [@x380kkm 2026-09-08] ////
for (const entry of ['window', 'application']) test(`${entry} 退出取消后可重试, 接受时仅确认一次`, async (context) => {
  const runtime = await mainRuntime(context);
  const leave = () => entry === 'window' ? runtime.window.close() : runtime.app.quit();
  runtime.window.hasDraft = true; runtime.sendDirty(true);
  leave();
  assert.equal(runtime.window.hasDraft, true);
  assert.equal(runtime.app.exited, undefined);
  assert.equal(runtime.manager.closed, 0);
  assert.equal((await runtime.call('document.read')).ok, true);
  runtime.answer = 1;
  leave(); await new Promise(setImmediate);
  assert.equal(runtime.confirmations, 2);
  assert.equal(runtime.manager.closed, 1);
  assert.equal(runtime.app.exited, true);
});

// //// 已接收的动作在窗口关闭后按顺序完成 [@x380kkm 2026-09-08] ////
test('退出等待当前请求和已入队请求后只关闭一次后端', async (context) => {
  const runtime = await mainRuntime(context);
  const pending = Promise.withResolvers();
  runtime.response = (method) => method === 'document.read' ? pending.promise : Promise.resolve({ method });
  const first = runtime.call('document.read'), second = runtime.call('catalog.snapshot');
  await new Promise(setImmediate);
  runtime.window.hasDraft = true; runtime.answer = 1;
  runtime.window.close(); runtime.app.quit();
  assert.deepEqual(runtime.events, ['document.read']);
  assert.equal(runtime.manager.closed, 0);
  pending.resolve({ value: 'saved' });
  assert.equal((await first).ok, true);
  assert.equal((await second).ok, true);
  await new Promise(setImmediate);
  assert.deepEqual(runtime.events, ['document.read', 'catalog.snapshot', 'backend-close']);
  assert.equal(runtime.confirmations, 1);
  assert.equal(runtime.app.exited, true);
});

// //// 取消目录选择或放弃确认时保留项目与连接 [@x380kkm 2026-09-08] ////
test('项目选择取消保留 dirty, 接受目录后只确认一次', async (context) => {
  const runtime = await mainRuntime(context);
  runtime.sendDirty(true);
  assert.equal((await runtime.chooseProject()).result, null);
  assert.equal(runtime.confirmations, 0);
  runtime.selection = { canceled: false, filePaths: [process.cwd()] };
  assert.equal((await runtime.chooseProject()).result, null);
  assert.equal(runtime.confirmations, 1);
  assert.equal(runtime.manager.closed, 0);
  runtime.answer = 1;
  assert.ok((await runtime.chooseProject()).result.workspace);
  assert.equal(runtime.confirmations, 2);
  assert.equal(runtime.manager.closed, 1);
});

// //// 渲染器阻止草稿卸载并在新页面初始化干净状态 [@x380kkm 2026-09-08] ////
test('渲染器保护草稿, 取消项目选择保持正文且仅主进程确认', async (context) => {
  const get = rendererRuntime(context, async (method) => {
    if (method === 'host.initialize') return {};
    if (method === 'card.inventory') return { items: [], groups: [], edges: [], diagnostics: [] };
    if (method === 'catalog.snapshot') return { catalog: '/catalog', scope: 'user', documents: [], graph: { nodes: [], edges: [] }, diagnostics: [] };
    throw new Error(method);
  });
  const dirty = [];
  let localConfirmations = 0, projectSelections = 0;
  window.manager.setDirty = (value) => dirty.push(value);
  window.manager.selectWorkspace = async () => { projectSelections += 1; return { ok: true, result: null }; };
  window.confirm = () => { localConfirmations += 1; return false; };
  const { start } = await import('../desktop/renderer/ui.mjs');
  start(); await new Promise(setImmediate);
  assert.equal(dirty[0], false);
  const clean = { preventDefault() { this.prevented = true; } };
  window.onbeforeunload(clean);
  assert.equal(clean.prevented, undefined);
  await get('inventory-kinds').children.find((node) => node.textContent === '配置源码').emit('click');
  await get('new-button').emit('click');
  get('json-editor').value = '{"draft":"保留正文"}'; await get('json-editor').emit('input');
  await get('workspace-button').emit('click'); await get('project-page-button').emit('click');
  const unloading = { preventDefault() { this.prevented = true; } };
  window.onbeforeunload(unloading);
  assert.equal(unloading.prevented, true);
  assert.equal(unloading.returnValue, false);
  assert.equal(get('json-editor').value, '{"draft":"保留正文"}');
  assert.equal(dirty.at(-1), true);
  assert.equal(localConfirmations, 0);
  assert.equal(projectSelections, 2);
});
