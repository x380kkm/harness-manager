// audience: internal
// # card-controls-tests
// 使用范围文案按页面隔离, 用户默认与项目个人覆盖保持各自语义.

import assert from 'node:assert/strict';
import test from 'node:test';
import { activationControl, activationLabel } from '../desktop/renderer/card-controls.mjs';
import { Element } from './renderer-runtime.mjs';

// //// 用户页面保留自己的设置而项目页面解释覆盖来源 [@x380kkm 2026-09-07] ////
test('启用范围按用户和项目页面分别呈现', () => {
  const state = { userState: 'enabled', projectState: 'disabled', sharedState: 'disabled', privateState: 'enabled' };
  assert.equal(activationLabel({ ...state, scope: 'user' }), '用户级开启');
  assert.equal(activationLabel({ ...state, scope: 'project-local' }), '个人开启');
  assert.equal(activationLabel({ ...state, scope: 'project-local', privateState: 'inherit' }), '项目共享关闭');
  assert.equal(activationLabel({ ...state, scope: 'project-local', privateState: 'inherit', sharedState: 'inherit', projectState: 'inherit' }), '继承用户级开启');
});

// //// 原生关闭和混合选择优先呈现实际处理器状态 [@x380kkm 2026-09-10] ////
test('Hook 卡片跟随原生开关并区分待应用选择', () => {
  const management = { kind: 'hook', scope: 'user', userState: 'enabled', nativeState: 'disabled' };
  assert.equal(activationLabel(management), '原生已关闭');
  assert.equal(activationLabel({ ...management, nativeState: 'mixed' }), '部分处理器开启');
  assert.equal(activationLabel({ ...management, pendingNativeState: true }), '待应用开启');
  assert.equal(activationLabel({ ...management, nativeState: 'enabled', pendingNativeState: false }), '待应用关闭');
});

// //// 用户来源的项目 Hook 保留撤去本层绑定的入口 [@x380kkm 2026-09-10] ////
test('项目继承 Hook 允许恢复继承并限制全局启停', (context) => {
  const previous = globalThis.document;
  globalThis.document = { createElement: (tag) => new Element(tag) };
  context.after(() => { globalThis.document = previous; });
  const control = activationControl({ id: 'hook', name: 'Hook', kind: 'hook', management: { kind: 'hook', nativeScope: 'user', nativeState: 'disabled' } },
    { scope: 'project-local', readOnly: false, onConfigure() {} });
  assert.equal(control.disabled, false);
  const choices = new Map(control.children.map((option) => [option.value, option]));
  assert.equal(choices.get('enabled').disabled, true);
  assert.equal(choices.get('disabled').disabled, true);
  assert.equal(choices.get('inherit').disabled, false);
});
