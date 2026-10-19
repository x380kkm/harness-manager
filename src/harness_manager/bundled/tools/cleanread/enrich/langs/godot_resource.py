# audience: internal
# enrich.langs.godot_resource
from __future__ import annotations

from tree_sitter import Node

from enrich.model import Pending, _text


#### 去掉字符串字面量两端引号与 string_name 的 & 前缀 [@380kkm 2026-06-17] ####
def _unquote(s: str) -> str:
    s = s.strip()
    if s.startswith('&"') and s.endswith('"'):
        return s[2:-1]
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    return s
#### /去引号 ####


#### 取 section 头部段类型标识符(首个 identifier 直接子节点) [@380kkm 2026-06-17] ####
def _section_head(node: Node, src: bytes) -> str:
    for ch in node.named_children:
        if ch.type == "identifier":
            return _text(ch, src).strip()
    return ""
#### /取段类型 ####


#### 收集 section 的 attribute 键值(值去引号) [@380kkm 2026-06-17] ####
def _section_attrs(node: Node, src: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    for ch in node.named_children:
        if ch.type != "attribute":
            continue
        kids = ch.named_children
        if len(kids) >= 2:
            out[_text(kids[0], src).strip()] = _unquote(_text(kids[1], src))
    return out
#### /收集 attribute ####


#### 取 section 内某 property 的值(去引号),无则空串 [@380kkm 2026-06-17] ####
def _section_property(node: Node, src: bytes, key: str) -> str:
    for ch in node.named_children:
        if ch.type != "property":
            continue
        kids = ch.named_children
        if len(kids) >= 2 and _text(kids[0], src).strip() == key:
            return _unquote(_text(kids[1], src))
    return ""
#### /取 property ####


#### 取该文件 gd_resource 头声明的资源类型(供无 view_name 的 .tres 资源体命名) [@380kkm 2026-06-17] ####
def _file_resource_type(section: Node, src: bytes) -> str:
    root = section.parent
    if root is None:
        return ""
    for ch in root.named_children:
        if ch.type == "section" and _section_head(ch, src) == "gd_resource":
            return _section_attrs(ch, src).get("type", "")
    return ""
#### /取文件资源类型 ####


#### 把一个 Godot section 产出一个符号:node 的 name/type 直接可查,资源段记句柄与类型 [@380kkm 2026-06-17] ####
def _emit_section(node: Node, src: bytes, pend: Pending) -> None:
    head = _section_head(node, src)
    if not head:
        return
    attrs = _section_attrs(node, src)
    if head == "node":
        # name=节点名,kind=Godot 类型(Node3D/Marker3D/MeshInstance3D 等);parent/transform 在字节跨度内可 slice
        name = attrs.get("name") or "<node>"
        kind = attrs.get("type") or "node"
    elif head in ("ext_resource", "sub_resource"):
        # name=资源句柄 id(场景内引用名),kind=段类型;具体 type/path 在字节跨度内
        name = attrs.get("id") or attrs.get("path") or head
        kind = head
    elif head in ("gd_scene", "gd_resource"):
        # 文件头:name=uid 或资源类型
        name = attrs.get("uid") or attrs.get("type") or head
        kind = head
    elif head == "resource":
        # .tres 资源体:优先用 view_name 这类语义标签作 name,否则退回文件资源类型
        name = _section_property(node, src, "view_name") or _file_resource_type(node, src) or "resource"
        kind = "resource"
    elif head == "editable":
        name = attrs.get("path") or head
        kind = head
    elif head == "connection":
        name = attrs.get("method") or attrs.get("signal") or head
        kind = head
    else:
        name = head
        kind = head
    pend.add(name, kind, node, None)
#### /产出一个 section 符号 ####


#### 遍历 godot_resource 语法树,每个 section 产出一个符号(段内属性/字段不再下钻) [@380kkm 2026-06-17] ####
def _walk_godot_resource(node: Node, src: bytes, pend: Pending, parent_local: int | None) -> None:
    if node.type == "section":
        _emit_section(node, src, pend)
        return
    for ch in node.children:
        _walk_godot_resource(ch, src, pend, parent_local)
#### /遍历 godot_resource ####
