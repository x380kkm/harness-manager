# audience: internal
# # host-sources
# 编译观察保留来源声明与读取授权. 宿主提交沿同一解析器核对正文修订.

from copy import deepcopy
import os
from pathlib import Path

from .sources import SourceReader, text_revision
from .storage_errors import StorageConflictError


# //// 保存宿主编译使用的完整来源观察 [@x380kkm 2026-09-08] ////
class HostSourceReader(SourceReader):
    def __init__(self, workspace: Path, roots: list[Path]) -> None:
        super().__init__(workspace, roots)
        self.observations = []

    # //// 读取声明入口并保存其正文修订及授权位置 [@x380kkm 2026-09-08] ////
    def read(self, source: dict, entry: str) -> dict:
        unit = super().read(source, entry)
        self.observations.append({"source": deepcopy(source), "entry": entry,
                                  **{key: deepcopy(unit[key]) for key in ("path", "revision", "accessPaths")}})
        return unit


# //// 沿原来源解析器核对编译正文与当前读取权限 [@x380kkm 2026-09-08] ////
def verify_source_reads(reader: SourceReader, observations: list[dict], written: dict[Path, bytes | None]) -> None:
    outputs = {os.path.normcase(str(path)): content for path, content in written.items()}
    for observation in observations:
        reader.authorize_snapshot(observation["accessPaths"])
        unit = reader.read(observation["source"], observation["entry"])
        expected = observation["revision"]
        path = os.path.normcase(observation["path"])
        if observation["source"]["resolver"]["id"] == "manager.source/path" and path in outputs:
            content = outputs[path]
            if content is None:
                raise StorageConflictError("host-content-source")
            expected = "local:" + text_revision(observation["path"], content.decode("utf-8-sig"))
        if unit["revision"] != expected:
            raise StorageConflictError("host-content-source")
