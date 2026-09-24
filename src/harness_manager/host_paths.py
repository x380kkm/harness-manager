# audience: internal
# # host-paths
"""用户配置保存所有 Hook 的个人开关, 项目宿主事务同时绑定该用户目录."""
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from .host_storage import HostStorage


# //// 将声明范围映射到固定宿主目录 [@x380kkm 2026-09-10] ////
def host_storage(catalogs, codex_root: Path, scope: str) -> HostStorage:
    catalogs.select(scope)
    if scope == "user":
        return HostStorage(catalogs.user.workspace, codex_root, "codex-user")
    root = catalogs.project.workspace
    return HostStorage(catalogs.user.workspace, root, "codex-" + uuid5(NAMESPACE_URL, root.as_uri()).hex,
                       config_subdir=".codex", hook_state_root=codex_root)
