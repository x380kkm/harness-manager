// audience: internal
// # desktop-main
// 桌面主进程持有用户选择的项目和来源根. 声明写入统一交给 Python 管理核心.

import { app, BrowserWindow, dialog, ipcMain, net, protocol, session } from 'electron';
import { spawn } from 'node:child_process';
import { homedir } from 'node:os';
import { realpath, stat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { failure, ManagerProcess } from './rpc.mjs';
import { exportSnapshot } from './snapshot-export.mjs';

const mainDirectory = path.dirname(fileURLToPath(import.meta.url));
const desktopDirectory = path.dirname(mainDirectory);
const projectDirectory = path.dirname(desktopDirectory);
const rendererDirectory = path.join(desktopDirectory, 'renderer');
const pageUrl = 'harness://app/index.html';
const assets = new Set(['index.html', 'styles.css', 'inventory.css', 'app.js']);
const methods = new Set(['codex.snapshot', 'codex.import', 'catalog.snapshot', 'document.read', 'document.preview', 'document.preview_remove', 'document.apply', 'protocol.schema', 'usage.describe', 'usage.preview', 'content.open', 'content.continue', 'content.snapshot']);
for (const method of ['context.describe', 'context.preview', 'context.preview_remove', 'context.apply']) methods.add(method);
for (const method of ['card.inventory', 'card.describe', 'card.configure', 'card.relations', 'card.set_relation', 'card.remove_relation', 'statistics.reads', 'content.preview']) methods.add(method);
methods.add('card.set_shared');
methods.add('rule.describe');
for (const method of ['project.relocate_preview', 'project.relocate_apply']) methods.add(method);
for (const method of ['host.initialize', 'host.status', 'host.inspect', 'host.set_enabled', 'host.preview', 'host.preview_restore', 'host.apply', 'module.describe', 'module.preview', 'module.apply']) methods.add(method);
const importKinds = new Set(['skill', 'instruction', 'preference', 'tool', 'model']);
const manager = new ManagerProcess({ projectDirectory, spawnProcess: spawn });
let window;
let workspace = null;
let userRoot = homedir();
let readRoots = new Set();
let dirty = false;
let queue = Promise.resolve();
let shutdown = null;
let managerClosed = false;

protocol.registerSchemesAsPrivileged([{ scheme: 'harness', privileges: { standard: true, secure: true, supportFetchAPI: true } }]);

// //// 核对 IPC 的主页面来源 [@x380kkm 2026-09-06] ////
function verifySender(event) {
  if (event.sender !== window?.webContents || event.senderFrame !== event.sender.mainFrame || event.senderFrame.url !== pageUrl) {
    throw failure('invalid-sender', '当前页面缺少管理接口访问权限.');
  }
}

// //// 顺序执行桌面管理动作并保留结构化错误 [@x380kkm 2026-09-08] ////
async function transact(event, action) {
  try {
    verifySender(event);
    const result = queue.then(action);
    queue = result.then(() => undefined, () => undefined);
    return { ok: true, result: await result };
  } catch (error) {
    return { ok: false, error: { code: error.code || 'desktop-error', message: error.message, details: error.details } };
  }
}

// //// 解析用户指定的项目目录 [@x380kkm 2026-09-06] ////
async function resolveWorkspace(directory) {
  const resolved = await realpath(path.resolve(directory));
  if (!(await stat(resolved)).isDirectory()) throw failure('invalid-workspace', '项目路径需要是一个目录.');
  return resolved;
}

// //// 取得可用的管理进程 [@x380kkm 2026-09-06] ////
function connectedManager() {
  if (!manager.child) manager.start(workspace, readRoots, userRoot);
  return manager;
}

// //// 确认尚未保存的编辑如何处理 [@x380kkm 2026-09-08] ////
function confirmDiscard() {
  return dialog.showMessageBoxSync(window, {
    type: 'question', message: '当前编辑尚未保存.', detail: '放弃修改后继续操作?',
    buttons: ['继续编辑', '放弃修改'], defaultId: 0, cancelId: 0,
  }) === 1;
}

// //// 选择项目并切换管理进程 [@x380kkm 2026-09-08] ////
async function selectWorkspace() {
  const selection = await dialog.showOpenDialog(window, { title: '选择项目目录', properties: ['openDirectory'] });
  if (selection.canceled) return null;
  const selected = await resolveWorkspace(selection.filePaths[0]);
  if (dirty && !confirmDiscard()) return null;
  await manager.close();
  workspace = selected;
  dirty = false;
  return { workspace };
}

// //// 从原生文件选择授予导入读取范围 [@x380kkm 2026-09-06] ////
async function importFile(kind, scope) {
  connectedManager();
  if (kind != null && kind !== '' && !importKinds.has(kind)) throw failure('invalid-kind', '导入类型无法识别.');
  const selection = await dialog.showOpenDialog(window, {
    title: '导入声明或本地内容', properties: ['openFile'],
    filters: [{ name: 'JSON 或 Markdown', extensions: ['json', 'md'] }],
  });
  if (selection.canceled) return null;
  const selected = await realpath(selection.filePaths[0]);
  const root = path.dirname(selected);
  if (!readRoots.has(root)) {
    await manager.close();
    readRoots.add(root);
  }
  const params = { path: selected, scope };
  if (kind) params.kind = kind;
  return connectedManager().request('document.import', params);
}

// //// 通过原生选择增加当前会话的来源读取范围 [@x380kkm 2026-09-06] ////
async function authorizeSource() {
  const selection = await dialog.showOpenDialog(window, { title: '允许读取来源目录', properties: ['openDirectory'] });
  if (selection.canceled) return null;
  const root = await resolveWorkspace(selection.filePaths[0]);
  if (!readRoots.has(root)) {
    await manager.close();
    readRoots.add(root);
  }
  return { path: root };
}

// //// 登记渲染器可调用的管理动作 [@x380kkm 2026-09-06] ////
function registerBridge() {
  ipcMain.handle('manager:context', (event) => transact(event, () => ({ workspace, userRoot })));
  ipcMain.handle('manager:select-workspace', (event) => transact(event, selectWorkspace));
  ipcMain.handle('manager:import-file', (event, kind, scope) => transact(event, () => importFile(kind, scope)));
  ipcMain.handle('manager:authorize-source', (event) => transact(event, authorizeSource));
  ipcMain.handle('manager:export-snapshot', (event) => transact(event, () => exportSnapshot(connectedManager(), rendererDirectory)));
  ipcMain.handle('manager:call', (event, method, params) => transact(event, () => {
    if (!methods.has(method)) throw failure('unknown-method', '该管理动作未开放给界面.');
    if (!params || typeof params !== 'object' || Array.isArray(params)) throw failure('invalid-params', '管理参数需要是一个对象.');
    return connectedManager().request(method, params);
  }));
  ipcMain.on('manager:dirty', (event, value) => {
    try { verifySender(event); dirty = value === true; } catch { return; }
  });
}

// //// 只提供打包的界面文件 [@x380kkm 2026-09-06] ////
function registerAssets() {
  protocol.handle('harness', (request) => {
    const url = new URL(request.url);
    const name = url.pathname.slice(1);
    if (url.host === 'app' && name === 'local-inventory.js' && request.method === 'GET') return new Response('', { headers: { 'Content-Type': 'text/javascript' } });
    if (url.host !== 'app' || !assets.has(name) || request.method !== 'GET') return new Response(null, { status: 403 });
    return net.fetch(pathToFileURL(path.join(rendererDirectory, name)).href);
  });
}

// //// 打开隔离的本地管理窗口 [@x380kkm 2026-09-08] ////
function createWindow() {
  window = new BrowserWindow({
    width: 1440, height: 920, minWidth: 960, minHeight: 640,
    title: 'Harness Manager', backgroundColor: '#f5f6f8',
    webPreferences: { preload: path.join(mainDirectory, 'preload.cjs'), contextIsolation: true, sandbox: true },
  });
  window.setMenuBarVisibility(false);
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', (event) => event.preventDefault());
  window.webContents.on('will-attach-webview', (event) => event.preventDefault());
  window.webContents.on('will-prevent-unload', (event) => {
    dirty = true;
    if (confirmDiscard()) { dirty = false; event.preventDefault(); }
  });
  window.on('closed', () => { window = null; });
  return window.loadURL(pageUrl);
}

// //// 启动本地桌面管理入口 [@x380kkm 2026-09-06] ////
app.whenReady().then(async () => {
  const userPosition = process.argv.indexOf('--user-root');
  if (userPosition !== -1) {
    if (!process.argv[userPosition + 1]) throw failure('user-root-required', '--user-root 需要目录参数.');
    userRoot = await resolveWorkspace(process.argv[userPosition + 1]);
  }
  const position = process.argv.indexOf('--workspace');
  if (position !== -1) {
    if (!process.argv[position + 1]) throw failure('workspace-required', '--workspace 需要目录参数.');
    workspace = await resolveWorkspace(process.argv[position + 1]);
  }
  session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  session.defaultSession.setPermissionCheckHandler(() => false);
  registerAssets();
  registerBridge();
  await createWindow();
}).catch((error) => { dialog.showErrorBox('Harness Manager', error.message); app.quit(); });

app.on('window-all-closed', () => app.quit());

// //// 在管理进程关闭后完成应用退出 [@x380kkm 2026-09-08] ////
function finishQuit() {
  managerClosed = true;
  app.quit();
}

// //// 在所有窗口卸载后完成已接收的管理动作 [@x380kkm 2026-09-08] ////
app.on('will-quit', (event) => {
  if (managerClosed) return;
  event.preventDefault();
  shutdown ??= queue.then(() => manager.close()).then(finishQuit, (error) => {
    dialog.showErrorBox('Harness Manager', error.message);
    finishQuit();
  });
});
