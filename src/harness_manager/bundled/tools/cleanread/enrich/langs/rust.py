# audience: internal
# enrich.langs.rust
from __future__ import annotations

from tree_sitter import Node

from enrich.model import Pending, _named_child_text, _text

# //// Rust 声明节点类型映射为符号 kind. [@380kkm 2026-08-04] ////
_RUST_DEFS = {
    "mod_item": "module",
    "struct_item": "struct",
    "enum_item": "enum",
    "trait_item": "trait",
    "impl_item": "impl",
    "const_item": "const",
    "type_item": "type_alias",
}
# //// /Rust 声明节点类型映射为符号 kind. ////

# //// Rust 可调用声明节点类型映射为符号 kind. [@380kkm 2026-08-04] ////
_RUST_CALLABLE_DEFS = {
    "function_item": "function",
    "function_signature_item": "function",
}
# //// /Rust 可调用声明节点类型映射为符号 kind. ////

# //// 本集合列出 Rust 中可直接包含方法的符号 kind. [@380kkm 2026-08-04] ////
_RUST_METHOD_HOST_KINDS = frozenset(("trait", "impl"))
# //// /本集合列出 Rust 中可直接包含方法的符号 kind. ////


# //// 本函数返回 impl 声明的稳定展示名称. [@380kkm 2026-08-04] ////
def _rust_impl_name(node: Node, src: bytes) -> str:
    target = _text(node.child_by_field_name("type"), src).strip()
    trait = _text(node.child_by_field_name("trait"), src).strip()
    if trait and target:
        return f"{trait} for {target}"
    return target or "<anonymous>"
# //// /本函数返回 impl 声明的稳定展示名称. ////


# //// 本函数遍历 Rust 语法树并收集声明, 包含和 trait 实现边. [@380kkm 2026-08-04] ////
def _walk_rust(node: Node, src: bytes, pend: Pending, parent_local: int | None) -> None:
    cur_parent = parent_local
    node_type = node.type

    if node_type in _RUST_DEFS:
        if node_type == "impl_item":
            name = _rust_impl_name(node, src)
        else:
            name = _named_child_text(node, "name", src) or "<anonymous>"
        idx = pend.add(name, _RUST_DEFS[node_type], node, parent_local)
        if node_type == "impl_item":
            trait = _text(node.child_by_field_name("trait"), src).strip()
            if trait:
                pend.inherit.append((idx, trait, "implements"))
        cur_parent = idx

    elif node_type in _RUST_CALLABLE_DEFS:
        name = _named_child_text(node, "name", src) or "<anonymous>"
        kind = "function"
        if (parent_local is not None
                and pend.rows[parent_local].kind in _RUST_METHOD_HOST_KINDS):
            kind = "method"
        cur_parent = pend.add(name, kind, node, parent_local)

    for child in node.children:
        _walk_rust(child, src, pend, cur_parent)
# //// /本函数遍历 Rust 语法树并收集声明, 包含和 trait 实现边. ////
