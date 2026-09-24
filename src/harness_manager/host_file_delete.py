# audience: internal
# # host-file-delete
"""宿主删除绑定已打开的文件或父目录, 并沿用预览中的原文字节."""
from pathlib import Path
import os
import stat

from .source_files import opened_file_path
from .storage_errors import StorageBoundaryError, StorageConflictError


# //// 核对删除句柄的实际位置和预览正文 [@x380kkm 2026-09-10] ////
def check_delete_file(stream, path: Path, expected: bytes) -> None:
    if opened_file_path(stream.fileno()) != path or not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
        raise StorageBoundaryError("宿主删除对象的位置或类型已经变化.")
    if stream.read(len(expected) + 1) != expected:
        raise StorageConflictError(path.name)


# //// 将已验证的 Windows 文件句柄标记为关闭时删除 [@x380kkm 2026-09-10] ////
def mark_windows_deleted(handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    remove = kernel.SetFileInformationByHandle
    remove.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    remove.restype = wintypes.BOOL
    disposition = wintypes.BOOL(True)
    file_disposition_info = 4
    if not remove(handle, file_disposition_info, ctypes.byref(disposition), ctypes.sizeof(disposition)):
        raise ctypes.WinError(ctypes.get_last_error())


# //// 打开带删除权限的文件并保持身份直到关闭 [@x380kkm 2026-09-10] ////
def remove_windows_file(path: Path, expected: bytes) -> None:
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    read_and_delete, share_read, open_existing, open_reparse = 0x80010000, 1, 3, 0x00200000
    handle = create(str(path), read_and_delete, share_read, None, open_existing, open_reparse, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except Exception:
        close = kernel.CloseHandle
        close.argtypes, close.restype = [wintypes.HANDLE], wintypes.BOOL
        close(handle)
        raise
    with os.fdopen(descriptor, "rb") as stream:
        check_delete_file(stream, path, expected)
        mark_windows_deleted(handle)


# //// 在固定父目录句柄内删除当前文件名 [@x380kkm 2026-09-10] ////
def remove_posix_file(path: Path, expected: bytes) -> None:
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if opened_file_path(parent) != path.parent:
            raise StorageBoundaryError("宿主删除目录的位置已经变化.")
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        with os.fdopen(descriptor, "rb") as stream:
            check_delete_file(stream, path, expected)
            if not os.path.samestat(os.fstat(descriptor), os.stat(path.name, dir_fd=parent, follow_symlinks=False)):
                raise StorageConflictError(path.name)
            os.unlink(path.name, dir_fd=parent)
    finally:
        os.close(parent)


# //// 按操作系统删除预览选定的宿主文件 [@x380kkm 2026-09-10] ////
def remove_host_file(path: Path, expected: bytes) -> None:
    if os.name == "nt":
        remove_windows_file(path, expected)
    else:
        remove_posix_file(path, expected)
