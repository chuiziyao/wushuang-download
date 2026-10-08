# -*- coding: utf-8 -*-
"""成品 EXE 自检：启动打包后的 exe → 等窗口出现 → 截图 → 关掉。

为什么需要它：源码能跑不代表打包后能跑。漏 tkinter、漏图标、版本号没更新、
--noconsole 下日志区空白，这些都只有打包后才暴露。

用法：
    python tools/exe_check.py                 # 默认查 dist/无双下载.exe
    python tools/exe_check.py 路径\\xxx.exe
"""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)   # 截图坐标才和窗口对得上
except Exception:
    pass

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, 'dist', '无双下载.exe')
OUT = os.path.join(HERE, 'assets', '_exe_shot.png')
KEY = '无双下载'

user32 = ctypes.windll.user32
_WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)


def windows():
    """所有可见且有标题的顶层窗口：[(hwnd, title), …]"""
    out = []

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                out.append((hwnd, buf.value))
        return True

    user32.EnumWindows(_WNDPROC(cb), 0)
    return out


def myrect(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def find(hwnds):
    """挑主窗口：标题含关键字里面积最大的那个。

    onefile 启动过程中会短暂出现小的过渡窗口，只按标题匹配会抓错。
    """
    best, best_area = None, 0
    for h, t in hwnds:
        if KEY in t:
            l, tp, r, b = myrect(h)
            area = max(0, r - l) * max(0, b - tp)
            if area > best_area:
                best, best_area = (h, t), area
    return (best if best else (None, None))


def rect(hwnd):
    return myrect(hwnd)


class _BMIH(ctypes.Structure):
    _fields_ = [('biSize', wintypes.DWORD), ('biWidth', ctypes.c_long),
                ('biHeight', ctypes.c_long), ('biPlanes', wintypes.WORD),
                ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
                ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', ctypes.c_long),
                ('biYPelsPerMeter', ctypes.c_long), ('biClrUsed', wintypes.DWORD),
                ('biClrImportant', wintypes.DWORD)]


class _BMI(ctypes.Structure):
    _fields_ = [('bmiHeader', _BMIH), ('bmiColors', wintypes.DWORD * 3)]


def grab_window(hwnd):
    """用 PrintWindow 直接抓窗口位图，不受遮挡影响。

    ImageGrab.grab(bbox) 抓的是屏幕像素、不看窗口层级 —— 目标窗口后面/前面
    压着别的窗口时，截出来就是别人的画面（自检会误判）。
    PrintWindow + PW_RENDERFULLCONTENT(=2) 让窗口自己画一遍自己。
    """
    from PIL import Image
    l, t, r, b = rect(hwnd)
    w, h = max(1, r - l), max(1, b - t)
    gdi32 = ctypes.windll.gdi32
    hdc = user32.GetWindowDC(hwnd)
    memdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(memdc, bmp)
    user32.PrintWindow(hwnd, memdc, 2)
    bi = _BMI()
    bi.bmiHeader.biSize = ctypes.sizeof(_BMIH)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h                  # 负数 = 自上而下
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = 0              # BI_RGB
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(memdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    img = Image.frombuffer('RGBA', (w, h), buf, 'raw', 'BGRA', 0, 1).convert('RGB')
    gdi32.SelectObject(memdc, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(memdc)
    user32.ReleaseDC(hwnd, hdc)

    if all(a == b for a, b in img.getextrema()):   # 全黑/纯色 = PrintWindow 没抓到内容
        raise RuntimeError('PrintWindow 返回空图')
    return img


def grab(hwnd):
    """优先 PrintWindow，失败再退回抢前台 + ImageGrab"""
    try:
        return grab_window(hwnd), 'PrintWindow'
    except Exception as e:
        print('  PrintWindow 失败，退回前台截图：', repr(e))
    from PIL import ImageGrab
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.6)
    l, t, r, b = rect(hwnd)
    return ImageGrab.grab(bbox=(l, t, r, b)), 'ImageGrab'


def kill_tree(proc):
    """onefile 的 exe 会 fork 出子进程跑真正的代码，只杀父进程会留下孤儿窗口。"""
    try:
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                       capture_output=True, timeout=15)
    except Exception:
        proc.kill()


def main():
    if not os.path.exists(EXE):
        print('!! 找不到 exe：', EXE)
        return 1
    print('启动：', EXE, f'（{os.path.getsize(EXE) / 1048576:.2f} MB）')
    proc = subprocess.Popen([EXE])

    hwnd, title = None, None
    for _ in range(60):                     # 最多等 15 秒
        time.sleep(0.25)
        h, t = find(windows())
        if h:
            l, tp, r, b = rect(h)
            if (r - l) > 400 and (b - tp) > 300:   # 主窗口才算数
                hwnd, title = h, t
                break
    if not hwnd:
        print('!! 15 秒内没等到主窗口 —— 打包可能缺 tkinter 或启动即崩')
        kill_tree(proc)
        return 2
    print('窗口标题：', title)
    time.sleep(1.5)                         # 等界面绘制完

    try:
        im, how = grab(hwnd)
        im.save(OUT)
        print(f'截图：{OUT}  {im.size}  （方式：{how}）')
    except Exception as e:
        print('截图失败（不影响结论）：', repr(e))

    kill_tree(proc)
    print('已关闭进程。窗口起来了 = 打包 OK。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
