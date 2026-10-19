# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
# audience: internal
# cleanscan.lib.boundary.modulespec
"""cleanscan.lib.boundary.modulespec —— N 路模块分区的规格原语（与二进制 Zoning 并行）。

把已索引符号的路径划分到 N 个用户声明的模块 ZONE，外加一个无任何 include 命中时使用的默认归属 ZONE（默认名 ``External``）。
:class:`ModuleSpec` 是贯穿配置加载、构建和视图渲染的唯一边界类型；``module_of_path`` 是
其总分类器，复用二进制 ``zone_of_path`` 的逐前缀语义，仅以最长匹配在 N 个 include 间裁决。
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field

from .zoning import _NORM, norm_root

#### 默认归属 ZONE 的名称（无任何 include 命中时路径归入此 ZONE） [@380kkm 2026-06-05] ####
DEFAULT_FALLBACK = "External"


#### 一个声明的模块 ZONE，含名称、include 前缀集以及可选的 exclude 和 glob [@380kkm 2026-06-05] ####
@dataclass(frozen=True)
class ModuleZone:
    name: str
    # 规范化、去尾斜杠的目录前缀
    includes: tuple[str, ...]
    excludes: tuple[str, ...] = ()
    globs: tuple[str, ...] = ()


#### N 路模块分区规格，含 ZONE 元组、默认归属名和预排序的匹配器 [@380kkm 2026-06-05] ####
@dataclass(frozen=True)
class ModuleSpec:
    """``_matchers`` 是预先展平的 ``(include_prefix, zone_name)`` 对，按 ``(-len, prefix,
    decl_order)`` 全序排列以保证最长匹配和确定性平局裁决。``zones`` 保留声明序供渲染分列。

    空 include（``""``）表示整仓库归该 ZONE，与 ``zone_of_path`` 的 ``target_root==""`` 特例保持一致。
    """

    zones: tuple[ModuleZone, ...]
    fallback: str = DEFAULT_FALLBACK
    _matchers: tuple[tuple[str, str], ...] = field(default=(), compare=False)
    # 按 zone_name 查找对应的 ModuleZone，供 exclude 与 glob 谓词使用
    _by_name: dict[str, ModuleZone] = field(default_factory=dict, compare=False)


#### 把一份已校验的 doc 和内联 zone 规范化为 ModuleSpec [@380kkm 2026-06-05] ####
def make_module_spec(doc: dict | None, inline: list[tuple[str, list[str]]] | None = None,
                     fallback: str | None = None) -> ModuleSpec:
    """``doc`` 是 config.load_modules 的输出（``{version,fallback,zones}``）或 None。
    ``inline`` 是 ``--module NAME=PREFIX[,...]`` 解析出的 (name, prefixes) 列表，合并为附加 zone
    （以文件规格为基底，同名内联 zone 将新 include 追加到已有 zone）。``fallback`` 显式覆盖默认归属名。
    """
    raw_fb = (doc or {}).get("fallback") if doc else None
    fb = fallback or raw_fb or DEFAULT_FALLBACK

    #### 按声明序累积 zone，按名归并 include、exclude 和 glob [@380kkm 2026-06-05] ####
    order: list[str] = []
    acc: dict[str, dict] = {}

    def _add(name: str, includes, excludes=(), globs=()):
        if name not in acc:
            acc[name] = {"inc": [], "exc": [], "glob": []}
            order.append(name)
        acc[name]["inc"].extend(norm_root(x) for x in includes)
        acc[name]["exc"].extend(norm_root(x) for x in excludes)
        acc[name]["glob"].extend(globs)
    #### /累积 zone ####

    for z in (doc or {}).get("zones", []) if doc else []:
        _add(z["name"], z.get("include", []), z.get("exclude", []) or [], z.get("glob", []) or [])
    for name, prefixes in (inline or []):
        _add(name, prefixes)

    zones = tuple(ModuleZone(name=n, includes=tuple(acc[n]["inc"]),
                             excludes=tuple(acc[n]["exc"]), globs=tuple(acc[n]["glob"]))
                  for n in order)
    by_name = {z.name: z for z in zones}

    #### 展平 (include_prefix, zone_name)，按 (-len, prefix, decl_order) 全序 [@380kkm 2026-06-05] ####
    flat: list[tuple[int, str, int, str]] = []
    for di, z in enumerate(zones):
        for inc in z.includes:
            flat.append((-len(inc), inc, di, z.name))
    flat.sort()
    matchers = tuple((inc, name) for _nl, inc, _di, name in flat)
    #### /展平匹配器 ####

    return ModuleSpec(zones=zones, fallback=fb, _matchers=matchers, _by_name=by_name)


#### 判断规范化路径是否被某 zone 的 exclude 或 glob 排除 [@380kkm 2026-06-05] ####
def _excluded(p: str, zone: ModuleZone) -> bool:
    for ex in zone.excludes:
        if ex and (p == ex or p.startswith(ex + "/")):
            return True
    for gl in zone.globs:
        if gl and fnmatch.fnmatch(p, gl):
            return True
    return False


#### 返回规范化路径命中的 winning matcher（最长匹配且未被 exclude 排除），无命中则返回 None [@380kkm 2026-06-05] ####
def match_path(path: str | None, spec: ModuleSpec) -> tuple[str, str] | None:
    """返回 ``(include_prefix, zone_name)``：逐 ``_matchers``（最长优先）检查
    ``prefix == ""``（匹配一切）或 ``p == prefix`` 或 ``p.startswith(prefix + '/')``，命中且未被
    该 zone exclude 即返回该对。``path`` 为 None 或无任何命中时返回 None。逐前缀语义与
    ``zone_of_path`` 完全一致，是 ``module_of_path`` 取 zone 名、``winning_prefix`` 取前缀的唯一裁决点。
    """
    if path is None:
        return None
    p = _NORM(path)
    for prefix, name in spec._matchers:
        if prefix == "" or p == prefix or p.startswith(prefix + "/"):
            if not _excluded(p, spec._by_name[name]):
                return prefix, name
    return None


#### 把定义文件路径分类为某个 zone 名或默认归属名（最长匹配并经 exclude 过滤） [@380kkm 2026-06-05] ####
def module_of_path(path: str | None, spec: ModuleSpec) -> str:
    """缺失路径或无任何命中归 ``spec.fallback``，否则返回 winning matcher 的 zone 名。"""
    m = match_path(path, spec)
    return m[1] if m is not None else spec.fallback


#### 把一条 ``--module NAME=PREFIX[,PREFIX...]`` 字面量解析为 (name, prefixes) [@380kkm 2026-06-05] ####
def parse_inline_module(spec_str: str) -> tuple[str, list[str]]:
    """``"Core=Engine/Source/Core,Engine/Source/CoreUObject"`` 返回 ``("Core", [两个前缀])``；
    缺 ``=`` 或名为空时抛 ValueError。"""
    if "=" not in spec_str:
        raise ValueError(f"--module must be NAME=PREFIX[,PREFIX...], got {spec_str!r}")
    name, rest = spec_str.split("=", 1)
    name = name.strip()
    if not name:
        raise ValueError(f"--module NAME must be non-empty, got {spec_str!r}")
    prefixes = [pp.strip() for pp in rest.split(",") if pp.strip()]
    if not prefixes:
        raise ValueError(f"--module {name!r} needs at least one PREFIX")
    return name, prefixes


#### SQL ``LIKE`` 前缀转义：%、_、\ 在 ESCAPE '\' 下逐字符转义 [@380kkm 2026-06-05] ####
_LIKE_SPECIAL = re.compile(r"([%_\\])")


def like_prefix(prefix: str) -> str:
    """返回可直接拼 ``'%'`` 的 LIKE 模式体（已转义特殊字符），配合 ``ESCAPE '\\'`` 使用。"""
    return _LIKE_SPECIAL.sub(r"\\\1", prefix)
