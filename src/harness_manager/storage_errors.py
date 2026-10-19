# audience: internal
# # storage-errors
# 存储错误保留稳定代码, 调用者可以据此区分输入拒绝, 冲突和可重试的锁占用.


# //// 表达声明存储错误 [@x380kkm 2026-09-06] ////
class StorageError(Exception):
    code = "storage-error"
    retryable = False


# //// 表达目录格式错误 [@x380kkm 2026-09-06] ////
class StorageFormatError(StorageError):
    code = "catalog-format"


# //// 表达声明与计划校验错误 [@x380kkm 2026-09-06] ////
class StorageValidationError(StorageError):
    code = "storage-validation"


# //// 表达目标对象的基线冲突 [@x380kkm 2026-09-06] ////
class StorageConflictError(StorageError):
    code = "catalog-conflict"

    def __init__(self, document_id: str) -> None:
        super().__init__(f"声明 {document_id} 已变化, 请读取当前内容后重新预览.")
        self.details = {"documentId": document_id}


# //// 表达可重试的写入锁占用 [@x380kkm 2026-09-06] ////
class StorageBusyError(StorageError):
    code = "catalog-busy"
    retryable = True


# //// 表达受管路径边界错误 [@x380kkm 2026-09-06] ////
class StorageBoundaryError(StorageError):
    code = "storage-boundary"


# //// 表达文件系统操作错误 [@x380kkm 2026-09-06] ////
class StorageIOError(StorageError):
    code = "storage-io"
