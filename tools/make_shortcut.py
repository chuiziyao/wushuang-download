# -*- coding: utf-8 -*-
"""创建 Windows 桌面快捷方式（纯标准库 ctypes，不依赖 pywin32）

通过 COM 的 IShellLinkW + IPersistFile 生成 .lnk，可指定目标、工作目录、
描述和自定义图标（IconLocation 指向 exe 时用其内嵌图标）。

用法：python make_shortcut.py <目标exe> [快捷方式路径] [图标文件,序号]
"""
import ctypes
import os
import sys
from ctypes import POINTER, Structure, byref, c_int, c_long, c_ubyte, c_ulong, c_ushort, c_void_p, c_wchar_p

CLSCTX_INPROC_SERVER = 1


class GUID(Structure):
    _fields_ = [('Data1', c_ulong), ('Data2', c_ushort), ('Data3', c_ushort),
                ('Data4', c_ubyte * 8)]


_ole32 = ctypes.WinDLL('ole32')
_ole32.CoInitialize.argtypes = [c_void_p]
_ole32.CoInitialize.restype = c_long
_ole32.CLSIDFromString.argtypes = [c_wchar_p, POINTER(GUID)]
_ole32.CLSIDFromString.restype = c_long
_ole32.CoCreateInstance.argtypes = [POINTER(GUID), c_void_p, c_ulong,
                                    POINTER(GUID), POINTER(c_void_p)]
_ole32.CoCreateInstance.restype = c_long


def guid(text):
    g = GUID()
    if _ole32.CLSIDFromString(text, byref(g)) != 0:
        raise RuntimeError('GUID 解析失败: ' + text)
    return g


def vcall(ptr, index, *argtypes_and_values):
    """按 vtable 序号调用 COM 方法（x64 下必须显式声明签名）"""
    argtypes, values = argtypes_and_values[0], argtypes_and_values[1]
    vtbl = ctypes.cast(ptr, POINTER(POINTER(c_void_p)))[0]
    addr = ctypes.cast(vtbl, POINTER(c_void_p))[index]
    proto = ctypes.WINFUNCTYPE(c_long, c_void_p, *argtypes)
    return proto(addr)(ptr, *values)


def create(target, lnk, icon=None, desc=None, workdir=None):
    # .lnk 内统一用反斜杠路径，避免资源管理器解析图标时出问题
    target = os.path.normpath(target)
    lnk = os.path.normpath(lnk)
    if icon:
        icon = os.path.normpath(icon)
    if not os.path.exists(target):
        raise FileNotFoundError(target)
    _ole32.CoInitialize(None)

    clsid = guid('{00021401-0000-0000-C000-000000000046}')   # ShellLink
    iid_link = guid('{000214F9-0000-0000-C000-000000000046}')  # IShellLinkW
    iid_file = guid('{0000010b-0000-0000-C000-000000000046}')  # IPersistFile

    p = c_void_p()
    hr = _ole32.CoCreateInstance(byref(clsid), None, CLSCTX_INPROC_SERVER,
                                 byref(iid_link), byref(p))
    if hr != 0 or not p:
        raise RuntimeError(f'CoCreateInstance 失败 hr={hr:#x}')

    # IShellLinkW: 20=SetPath 17=SetIconLocation 7=SetDescription 9=SetWorkingDirectory
    vcall(p, 20, [c_wchar_p], [target])
    vcall(p, 9, [c_wchar_p], [workdir or os.path.dirname(target)])
    vcall(p, 7, [c_wchar_p], [desc or os.path.basename(target)])
    ic, idx = (icon or target), 0
    if icon and ',' in icon:
        ic, _, s = icon.rpartition(',')
        idx = int(s)
    vcall(p, 17, [c_wchar_p, c_int], [ic, idx])

    # 取 IPersistFile 并 Save
    pf = c_void_p()
    hr = vcall(p, 0, [POINTER(GUID), POINTER(c_void_p)], [byref(iid_file), byref(pf)])
    if hr != 0 or not pf:
        raise RuntimeError(f'QueryInterface(IPersistFile) 失败 hr={hr:#x}')
    os.makedirs(os.path.dirname(os.path.abspath(lnk)), exist_ok=True)
    hr = vcall(pf, 6, [c_wchar_p, c_int], [os.path.abspath(lnk), 1])  # Save
    if hr != 0:
        raise RuntimeError(f'Save 失败 hr={hr:#x}')

    vcall(pf, 2, [], [])   # Release
    vcall(p, 2, [], [])    # Release
    return os.path.abspath(lnk)


def desktop():
    """活动桌面路径（不受 OneDrive 重定向影响）"""
    CSIDL_DESKTOPDIRECTORY, SHGFP_TYPE_CURRENT = 0x0010, 0
    buf = ctypes.create_unicode_buffer(260)
    fn = ctypes.windll.shell32.SHGetFolderPathW
    fn.argtypes = [c_void_p, c_int, c_void_p, c_ulong, ctypes.c_wchar_p]
    fn.restype = c_long
    if fn(None, CSIDL_DESKTOPDIRECTORY, None, SHGFP_TYPE_CURRENT, buf) == 0:
        return buf.value
    return os.path.join(os.path.expanduser('~'), 'Desktop')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    target = sys.argv[1]
    lnk = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        desktop(), os.path.splitext(os.path.basename(target))[0] + '.lnk')
    icon = sys.argv[3] if len(sys.argv) > 3 else None
    path = create(target, lnk, icon,
                  desc='无双下载 - 多平台无水印下载工具')
    print('已创建快捷方式:', path)
    print('  目标:', target)
    print('  图标:', icon or (target + ',0'))
