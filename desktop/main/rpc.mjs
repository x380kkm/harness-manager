// audience: internal
// # manager-rpc
// 管理进程通过标准输入输出交换逐行 JSON. 进程的读取范围由调用方提供.

import { createInterface } from 'node:readline';

// //// 生成可跨进程传递的错误 [@x380kkm 2026-09-06] ////
export function failure(code, message, details) {
  return Object.assign(new Error(message), { code, details });
}

// //// 管理本地 RPC 进程 [@x380kkm 2026-09-06] ////
export class ManagerProcess {
  // //// 保存后端进程依赖 [@x380kkm 2026-09-06] ////
  constructor({ projectDirectory, spawnProcess }) {
    this.projectDirectory = projectDirectory;
    this.spawnProcess = spawnProcess;
    this.child = null;
    this.pending = new Map();
    this.nextId = 0;
    this.stderr = '';
  }

  // //// 启动指定读取范围的管理进程 [@x380kkm 2026-09-06] ////
  start(workspace, readRoots, userRoot) {
    const args = ['run', '--project', this.projectDirectory, 'harness-manager'];
    if (workspace) args.push('--workspace', workspace);
    if (userRoot) args.push('--user-root', userRoot);
    for (const root of readRoots) args.push('--read-root', root);
    args.push('rpc');
    const child = this.spawnProcess('uv', args, {
      cwd: this.projectDirectory,
      windowsHide: true,
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    this.child = child;
    this.stderr = '';
    const lines = createInterface({ input: child.stdout });
    lines.on('line', (line) => this.receive(line));
    child.stderr.setEncoding('utf8');
    child.stderr.on('data', (chunk) => { this.stderr = (this.stderr + chunk).slice(-6000); });
    child.on('error', (error) => {
      if (this.child === child) this.child = null;
      lines.close();
      this.rejectPending(failure('process-start', error.message));
    });
    child.on('exit', (code) => {
      lines.close();
      if (this.child === child) this.child = null;
      this.rejectPending(failure('process-exit', '管理进程已退出. 请刷新目录重新连接.', {
        exitCode: code, output: this.stderr.trim(),
      }));
    });
  }

  // //// 将回执交给对应请求 [@x380kkm 2026-09-06] ////
  receive(line) {
    let response;
    try {
      response = JSON.parse(line);
    } catch {
      this.rejectPending(failure('invalid-response', '管理进程返回了无法读取的响应.', { output: line.slice(0, 2000) }));
      return;
    }
    const request = this.pending.get(response.id);
    if (!request) return;
    clearTimeout(request.timer);
    this.pending.delete(response.id);
    if (response.error) {
      request.reject(failure(response.error.code, response.error.message, response.error.details));
    } else {
      request.resolve(response.result);
    }
  }

  // //// 发出有独立标识的管理请求 [@x380kkm 2026-09-06] ////
  request(method, params) {
    const child = this.child;
    if (!child || child.stdin.destroyed) {
      return Promise.reject(failure('process-unavailable', '管理进程尚未连接. 请刷新目录.'));
    }
    const id = ++this.nextId;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(failure('response-timeout', '管理请求尚未返回结果. 请重新读取目录确认实际内容.'));
      }, 60000);
      this.pending.set(id, { resolve, reject, timer });
      child.stdin.write(`${JSON.stringify({ id, method, params })}\n`, (error) => {
        if (!error || !this.pending.has(id)) return;
        clearTimeout(timer);
        this.pending.delete(id);
        reject(failure('request-write', error.message));
      });
    });
  }

  // //// 结束仍在等待的请求 [@x380kkm 2026-09-06] ////
  rejectPending(error) {
    for (const request of this.pending.values()) {
      clearTimeout(request.timer);
      request.reject(error);
    }
    this.pending.clear();
  }

  // //// 关闭输入并等待管理进程退出 [@x380kkm 2026-09-06] ////
  close() {
    const child = this.child;
    if (!child || !child.pid || child.exitCode !== null) return Promise.resolve();
    return new Promise((resolve) => {
      child.once('exit', resolve);
      child.stdin.end();
    });
  }
}
