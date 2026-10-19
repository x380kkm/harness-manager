// audience: internal
// # desktop-rpc-integration
// 验证使用桌面进程边界的真实导入与登记. 测试资料位于独立的系统临时目录.

import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdir, mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { registeredDocumentId } from '../desktop/renderer/graph.mjs';
import { ManagerProcess } from '../desktop/main/rpc.mjs';

const projectDirectory = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

// //// 清理经过路径核对的测试临时目录 [@x380kkm 2026-09-06] ////
async function removeTemporaryDirectory(temporaryRoot, createdDirectory) {
  const resolved = await realpath(createdDirectory);
  const relative = path.relative(temporaryRoot, resolved);
  assert.ok(relative && !path.isAbsolute(relative));
  assert.equal(path.dirname(resolved), temporaryRoot);
  assert.equal(resolved, createdDirectory);
  await rm(resolved, { recursive: true });
}

// //// 经桌面 RPC 导入中文 Skill 并读取登记关系 [@x380kkm 2026-09-06] ////
test('中文 Skill 经真实 RPC 保存并映射到登记声明', { timeout: 90000 }, async (context) => {
  const temporaryRoot = await realpath(tmpdir());
  const createdDirectory = await mkdtemp(path.join(temporaryRoot, 'harness-desktop-rpc-'));
  const workspace = path.join(createdDirectory, '项目目录');
  const userRoot = path.join(createdDirectory, '用户目录');
  const sourceDirectory = path.join(createdDirectory, '来源资料');
  const entry = path.join(sourceDirectory, 'SKILL.md');
  const skillText = '---\nname: 项目结构分析\ndescription: 整理符号与项目引用关系.\n---\n# 项目结构分析\n\n读取项目输入, 保留内容来源.\n';
  const manager = new ManagerProcess({ projectDirectory, spawnProcess: spawn });
  context.after(async () => {
    await manager.close();
    await removeTemporaryDirectory(temporaryRoot, createdDirectory);
  });
  await mkdir(workspace);
  await mkdir(userRoot);
  await mkdir(sourceDirectory);
  await writeFile(entry, skillText, 'utf8');
  manager.start(null, new Set([sourceDirectory]), userRoot);

  const imported = await manager.request('document.import', { path: entry });
  assert.equal(imported.document.metadata.name, '项目结构分析');
  assert.equal(imported.document.metadata.description, '整理符号与项目引用关系.');
  assert.equal(imported.baseline, null);
  const preview = await manager.request('document.preview', { document: imported.document, baseline: imported.baseline });
  const applied = await manager.request('document.apply', { plan: preview.plan });
  const storedId = `${imported.document.id}@${imported.document.release.version}`;
  assert.equal(applied.id, storedId);

  const catalog = await manager.request('catalog.snapshot', {});
  assert.equal(catalog.workspace, null);
  assert.equal(catalog.scope, 'user');
  assert.equal(catalog.catalog, path.join(userRoot, '.harness', 'catalog.json'));
  assert.equal(catalog.documents.length, 1);
  assert.equal(catalog.documents[0].id, storedId);
  assert.equal(catalog.documents[0].name, '项目结构分析');
  assert.equal(catalog.documents[0].summary, '整理符号与项目引用关系.');
  const graph = catalog.graph;
  const childId = `${storedId}#${imported.document.contributions[0].id}`;
  const child = graph.nodes.find((node) => node.id === childId);
  assert.equal(child.documentId, storedId);
  assert.equal(child.label, '项目结构分析');
  assert.ok(graph.edges.some((edge) => edge.from === storedId && edge.to === childId));
  const read = await manager.request('document.read', { id: registeredDocumentId(child) });
  assert.deepEqual(read.document, imported.document);
  assert.equal(read.document.id, imported.document.id);
  assert.equal(await readFile(entry, 'utf8'), skillText);
  const saved = JSON.parse(await readFile(catalog.catalog, 'utf8'));
  assert.deepEqual(saved.documents, [imported.document]);
  await manager.close();
  manager.start(workspace, new Set([sourceDirectory]), userRoot);
  const binding = { apiVersion: 'manager.x380kkm/v1', kind: 'PluginBinding', id: 'binding:project',
    plugin: { id: imported.document.id, constraint: 'local' },
    target: { contract: { id: 'manager.scope', range: '^1.0.0' }, selector: {} } };
  const projectPreview = await manager.request('document.preview', { document: binding, scope: 'project' });
  await manager.request('document.apply', { plan: projectPreview.plan });
  const projectCatalog = await manager.request('catalog.snapshot', { scope: 'project' });
  assert.equal(projectCatalog.documents[0].scope, 'project');
  assert.equal(projectCatalog.catalog, path.join(workspace, '.harness', 'catalog.json'));
  const discovery = await manager.request('catalog.discover', {});
  assert.equal(discovery.candidates[0].ref, `${imported.document.id}#${imported.document.contributions[0].id}`);
  assert.deepEqual(JSON.parse(await readFile(catalog.catalog, 'utf8')).documents, [imported.document]);
});
