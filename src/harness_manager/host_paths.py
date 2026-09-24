# audience: internal
# # host-paths
"""用户配置保存所有 Hook 的个人开关, 项目宿主事务同时绑定该用户目录.

声明范围到宿主目录的映射只在本模块给出; 按项目位置取得存档入口的调用方使用 `project_host_storage`, 避免同一映射出现第二处实现.
"""
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from .host_profiles import CODEX, HostProfile
from .host_storage import HostStorage


# //// 按项目位置生成宿主存档入口 [@x380kkm 2026-09-24] ////
def project_host_storage(user_root: Path, project: Path, host_root: Path,
                         profile: HostProfile = CODEX) -> HostStorage:
    return HostStorage(user_root, project, profile.scope_id(uuid5(NAMESPACE_URL, project.as_uri()).hex),
                       config_subdir=profile.config_subdir, hook_state_root=host_root, profile=profile)


# //// 将声明范围映射到固定宿主目录 [@x380kkm 2026-09-10] ////
def host_storage(catalogs, host_root: Path, scope: str, profile: HostProfile = CODEX) -> HostStorage:
    catalogs.select(scope)
    if scope == "user":
        return HostStorage(catalogs.user.workspace, host_root, profile.scope_id("user"), profile=profile)
    return project_host_storage(catalogs.user.workspace, catalogs.project.workspace, host_root, profile)
