# audience: internal
# # observation-store
"""读取快照与回执以独立文件保存在用户观察目录. 随机身份对应一次完整发布, 目录边界在读取和写入时核对."""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat
from uuid import uuid4

from .json_codec import decode_json, encode_json
from .storage_errors import StorageBoundaryError, StorageFormatError

MAX_OBSERVATION_BYTES = 16 * 1024 * 1024


# //// 生成单次观察的独立身份 [@x380kkm 2026-09-06] ////
def observation_id(kind: str) -> str:
    return f"{kind}:{uuid4().hex}"


# //// 保存完整的不可覆盖观察记录 [@x380kkm 2026-09-06] ////
class ObservationStore:
    def __init__(self, user_root: Path) -> None:
        self.root = user_root.resolve(strict=True)
        self.directory = self.root / ".harness" / "observations"

    # //// 核对每层目录仍属于选定位置 [@x380kkm 2026-09-06] ////
    def check_location(self) -> None:
        if self.root.resolve() != self.root or not self.root.is_dir():
            raise StorageBoundaryError("用户观察目录的根位置已经变化.")
        for path in (self.root / ".harness", self.directory):
            self.check_path(path, stat.S_ISDIR)

    # //// 核对普通目录或文件的直接归属 [@x380kkm 2026-09-06] ////
    def check_path(self, path: Path, expected) -> None:
        try:
            info = path.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise StorageBoundaryError("观察载体需要直接位于受管目录中.")
        if not expected(info.st_mode):
            raise StorageBoundaryError("观察载体的位置类型与声明不同.")

    # //// 将已验证观察身份映射到固定文件名 [@x380kkm 2026-09-06] ////
    def path_for(self, identity: str) -> Path:
        if not isinstance(identity, str) or re.fullmatch(r"(?:content-snapshot|content-read):[0-9a-f]{32}", identity) is None:
            raise StorageFormatError("读取观察需要有效的快照或回执身份.")
        return self.directory / (identity.replace(":", "-") + ".json")

    # //// 完整写入一个新观察文件 [@x380kkm 2026-09-06] ////
    def save(self, identity: str, document: dict) -> None:
        data = encode_json(document).encode("utf-8")
        if len(data) > MAX_OBSERVATION_BYTES:
            raise StorageFormatError("完整读取快照超过 16 MiB, 需要选择独立的较小内容入口.")
        target = self.path_for(identity)
        self.check_location()
        self.directory.parent.mkdir(exist_ok=True)
        self.check_location()
        self.directory.mkdir(exist_ok=True)
        self.check_location()
        self.check_path(target, stat.S_ISREG)
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            self.check_location()
            target.unlink(missing_ok=True)
            raise

    # //// 有界读取观察并核对请求身份 [@x380kkm 2026-09-06] ////
    def read(self, identity: str) -> dict:
        target = self.path_for(identity)
        self.check_location()
        self.check_path(target, stat.S_ISREG)
        with target.open("rb") as stream:
            data = stream.read(MAX_OBSERVATION_BYTES + 1)
        if len(data) > MAX_OBSERVATION_BYTES:
            raise StorageFormatError("读取观察超过存储上限.")
        document = decode_json(data.decode("utf-8"))
        if not isinstance(document, dict) or document.get("id") != identity:
            raise StorageFormatError("观察身份与请求不同.")
        return document
