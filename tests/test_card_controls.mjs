// audience: internal
// # card-controls-tests
// 使用范围文案按页面隔离, 用户默认与项目个人覆盖保持各自语义.

import assert from 'node:assert/strict';
import test from 'node:test';
import { activationLabel } from '../desktop/renderer/card-controls.mjs';

// //// 用户页面保留自己的设置而项目页面解释覆盖来源 [@x380kkm 2026-09-07] ////
test('启用范围按用户和项目页面分别呈现', () => {
  const state = { userState: 'enabled', projectState: 'disabled', sharedState: 'disabled', privateState: 'enabled' };
  assert.equal(activationLabel({ ...state, scope: 'user' }), '用户级开启');
  assert.equal(activationLabel({ ...state, scope: 'project-local' }), '个人开启');
  assert.equal(activationLabel({ ...state, scope: 'project-local', privateState: 'inherit' }), '项目共享关闭');
  assert.equal(activationLabel({ ...state, scope: 'project-local', privateState: 'inherit', sharedState: 'inherit', projectState: 'inherit' }), '继承用户级开启');
});
