// audience: internal
// # desktop-bridge
// 渲染器通过固定的 IPC 入口访问管理用例. 路径选择与读取授权由主进程持有.

const { contextBridge, ipcRenderer } = require('electron');

// //// 提供受限的管理界面接口 [@x380kkm 2026-09-06] ////
contextBridge.exposeInMainWorld('manager', {
  getContext: () => ipcRenderer.invoke('manager:context'),
  selectWorkspace: () => ipcRenderer.invoke('manager:select-workspace'),
  importFile: (kind, scope) => ipcRenderer.invoke('manager:import-file', kind, scope),
  authorizeSource: () => ipcRenderer.invoke('manager:authorize-source'),
  exportSnapshot: () => ipcRenderer.invoke('manager:export-snapshot'),
  call: (method, params) => ipcRenderer.invoke('manager:call', method, params),
  setDirty: (dirty) => ipcRenderer.send('manager:dirty', dirty === true),
});
