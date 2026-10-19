<!-- audience: internal -->
# Archify tool contract

将 `harness-manager toolkit list` 返回的发行根记为 `D`, 独立 Archify 包或仓库根记为 `A`, 当前工作根记为 `R`. Skill 位于 `D/skills/archify`.

## 解析独立安装

`D/tools/archify/invoke.ps1` 按以下来源解析 Archify:

1. 显式 `-ArchifyRoot A`.
2. `ARCHIFY_ROOT` 指向的包根.
3. `D/managed/clean-vibing.json` 中登记的 Archify 来源.
4. 外部 Manager 状态中登记的 Archify 来源.
5. PATH 中的 `archify` 命令.
6. Codex 和 Agent 的常用 user-level 或 project-level skill 目录.

包根可以直接包含 `bin/archify.mjs`, 也可以是包含 `archify/bin/archify.mjs` 的上游仓库根. runner 原样转发参数和退出码.

```powershell
& "$D/tools/archify/invoke.ps1" doctor
& "$D/tools/archify/invoke.ps1" -ArchifyRoot $A doctor
```

首次接入, 安装位置变化或调用失败时用 `doctor` 确认当前解析到的安装可以运行. 普通任务直接复用已经健康的安装.

## 操作路由

`author` 是 Agent 编写 typed JSON IR 的操作. 从当前安装读取对应 `schemas/<type>.schema.json`, `schemas/common.schema.json` 和一个同类型 example. schema 与 example 保持在 Archify 包中.

类型或表达方式不明确时调用 `guide`:

```powershell
& "$D/tools/archify/invoke.ps1" guide "<需要回答的问题>" --json --lang zh
```

已经写入 JSON 的 IR 调用 `validate`:

```powershell
& "$D/tools/archify/invoke.ps1" validate <type> <input.json> --json
```

需要交付独立 HTML 时调用 `deliver`:

```powershell
& "$D/tools/archify/invoke.ps1" deliver <type> <input.json> <output.html> --json
```

两个架构基线需要作者化差异视图时调用 `compare`:

```powershell
& "$D/tools/archify/invoke.ps1" compare architecture <base.json> <head.json> <output.html> --json
```

交付完成后需要浏览器行为证据时调用 `visual-check`:

```powershell
& "$D/tools/archify/invoke.ps1" visual-check <output.html> --json
```

命令选项, schema 和质量门以当前安装的 `help` 与 `SKILL.md` 为准. runner 负责定位和透传, Archify 负责命令语义和产物验证.

## 事实边界

`guide` 提供结构建议. author 决定事实内容. `validate` 证明 IR 和产物满足 Archify 契约. `deliver` 证明交付文件与已验证输入绑定. `compare` 证明两份已作者化 architecture IR 的结构差异.

源码事实来自 clean-tools 或 CodeGraph. Git 事实来自 Git. 运行事实来自实际执行和观察工具. Archify 保持这些来源之间的关系清晰.
