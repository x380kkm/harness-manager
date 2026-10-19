# audience: internal
# # source-files
"""文件句柄的实际路径用于读取授权, 打开后的文件身份与正文读取期间保持一致."""
from pathlib import Path
import os
import sys


# //// 从 Windows 文件句柄取得规范化来源路径 [@x380kkm 2026-09-10] ////
def windows_file_path(descriptor: int) -> Path:
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    resolve = kernel.GetFinalPathNameByHandleW
    resolve.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    resolve.restype = wintypes.DWORD
    handle = msvcrt.get_osfhandle(descriptor)
    size = resolve(handle, None, 0, 0)
    if not size:
        raise ctypes.WinError(ctypes.get_last_error())
    buffer = ctypes.create_unicode_buffer(size + 1)
    actual = resolve(handle, buffer, len(buffer), 0)
    if not actual:
        raise ctypes.WinError(ctypes.get_last_error())
    if actual >= len(buffer):
        raise OSError("来源的实际文件路径正在变化.")
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return Path(value)


# //// 按操作系统取得已打开文件的实际来源 [@x380kkm 2026-09-10] ////
def opened_file_path(descriptor: int) -> Path:
    if os.name == "nt":
        return windows_file_path(descriptor)
    if sys.platform.startswith("linux"):
        return Path(os.readlink(f"/proc/self/fd/{descriptor}"))
    if sys.platform == "darwin":
        import fcntl

        return Path(os.fsdecode(fcntl.fcntl(descriptor, fcntl.F_GETPATH, bytes(1024)).split(b"\0", 1)[0]))
    raise OSError("此系统需要提供文件句柄的来源路径解析.")


# //// 比较正文读取前后的文件身份与修改状态 [@x380kkm 2026-09-10] ////
def file_observation(info: os.stat_result) -> tuple:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns
