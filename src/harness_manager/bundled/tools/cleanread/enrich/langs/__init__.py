# audience: internal
# enrich.langs
from __future__ import annotations

from enrich.langs.cpp import _walk_cpp
from enrich.langs.csharp import _walk_csharp
from enrich.langs.gdscript import _walk_gdscript
from enrich.langs.glsl import _walk_glsl
from enrich.langs.godot_resource import _walk_godot_resource
from enrich.langs.java import _walk_java
from enrich.langs.javascript import _walk_javascript
from enrich.langs.python import _walk_python
from enrich.langs.rust import _walk_rust
from enrich.langs.typescript import _walk_typescript

#### 语言名称到遍历器函数的分发表 [@380kkm 2026-06-05] ####
WALKERS = {
    "cpp": _walk_cpp,
    "python": _walk_python,
    "javascript": _walk_javascript,
    "typescript": _walk_typescript,
    "tsx": _walk_typescript,
    "csharp": _walk_csharp,
    "glsl": _walk_glsl,
    "java": _walk_java,
    "rust": _walk_rust,
    "gdscript": _walk_gdscript,
    "godot_resource": _walk_godot_resource,
}
#### /语言名称到遍历器函数的分发表 ####

#### 拥有遍历器的语言名称集合，用于判断某语言是否走遍历器路径 [@380kkm 2026-06-05] ####
HAS_WALKER = frozenset(WALKERS)
