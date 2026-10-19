# audience: internal
# enrich.langreg
from __future__ import annotations

from tree_sitter import Language, Parser, Query  # noqa: F401
from tree_sitter_language_pack import get_language

#### 语言注册表：把 cleanread 语言名映射到路由到它的文件扩展名 [@380kkm 2026-06-05] ####
LANG_FOR_EXT: dict[str, str] = {
    # cpp
    ".h": "cpp", ".hpp": "cpp", ".hh": "cpp", ".inl": "cpp", ".ipp": "cpp",
    ".c": "cpp", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hxx": "cpp",
    # HLSL 及类 shader 扩展，使用 cpp 文法
    ".hlsl": "cpp", ".cginc": "cpp", ".usf": "cpp", ".ush": "cpp",
    ".compute": "cpp", ".fx": "cpp", ".shader": "cpp",
    # python
    ".py": "python", ".pyi": "python",
    # javascript
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    # typescript 与 tsx（使用 tree-sitter-typescript 文法；tsx 走 tsx 方言）
    ".ts": "typescript", ".tsx": "tsx",
    # csharp
    ".cs": "csharp",
    # glsl shader 源（tree-sitter-glsl）。HLSL 仍走上面的 cpp 文法。
    ".glsl": "glsl", ".vert": "glsl", ".frag": "glsl", ".comp": "glsl",
    ".geom": "glsl", ".tesc": "glsl", ".tese": "glsl",
    # java（Android / JVM）
    ".java": "java",
    # rust
    ".rs": "rust",
    # gdscript（Godot）
    ".gd": "gdscript",
    # godot 场景/资源（.tscn/.tres，用 tree-sitter-godot_resource 文法）
    ".tscn": "godot_resource", ".tres": "godot_resource",
}
#### /语言注册表 ####

#### 实际支持解析的语言集合（使用 list 以便扩展时原地追加，`from-import` 的调用方可见） [@380kkm 2026-06-05] ####
SUPPORTED_LANGS: list[str] = [
    "cpp", "python", "javascript", "typescript", "tsx", "csharp", "glsl",
    "java", "rust", "gdscript", "godot_resource",
]
#### /我们实际能解析的语言集合 ####


#### cleanread 语言名 -> tree-sitter-language-pack 文法名 [@380kkm 2026-06-05] ####
_PACK_NAME: dict[str, str] = {
    "cpp": "cpp",
    "python": "python",
    "javascript": "javascript",
    "typescript": "typescript",
    "tsx": "tsx",
    "csharp": "csharp",
    "glsl": "glsl",
    "java": "java",
    "rust": "rust",
    "gdscript": "gdscript",
    "godot_resource": "godot_resource",
}
#### /语言名到文法名映射 ####


#### 注册一种新语言：写入文法名并将 lang 追加到 SUPPORTED_LANGS [@380kkm 2026-06-05] ####
def register_lang(lang: str, grammar: str) -> None:
    _PACK_NAME[lang] = grammar
    if lang not in SUPPORTED_LANGS:
        SUPPORTED_LANGS.append(lang)
#### /扩展注册一种语言 ####


#### 注册一个扩展名到语言的映射，写入 LANG_FOR_EXT [@380kkm 2026-06-05] ####
def register_ext(ext: str, lang: str) -> None:
    LANG_FOR_EXT[ext] = lang
#### /扩展注册一个 扩展名->语言 的路由 ####


#### 经 language-pack 取受支持文法对应的 tree-sitter Language [@380kkm 2026-06-05] ####
def _load_language(lang: str) -> Language:
    pack = _PACK_NAME.get(lang)
    if pack is None:
        raise ValueError(f"unsupported language: {lang}")
    return get_language(pack)
#### /经 language-pack 取受支持文法对应的 tree-sitter Language ####
