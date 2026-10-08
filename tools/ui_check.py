# -*- coding: utf-8 -*-
"""GUI 自检：启动界面 → 截折叠态 → 点开高级设置 → 截展开态

不依赖打包，直接源码启动 + 截屏，用于改动界面后快速目检。
"""
import os
import sys
import time

import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'assets')

_holder = {}
_OrigTk = tk.Tk


class _Tk(_OrigTk):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        _holder['root'] = self
        self.after(1400, self._shot_closed)
        self.after(1900, self._toggle)
        self.after(2800, self._shot_open)
        self.after(3400, self.destroy)

    def _grab(self, tag):
        from PIL import ImageGrab
        self.update_idletasks()
        self.lift()
        self.attributes('-topmost', True)
        self.update()
        time.sleep(0.35)
        x, y = self.winfo_rootx(), self.winfo_rooty()
        w, h = self.winfo_width(), self.winfo_height()
        im = ImageGrab.grab(bbox=(x - 8, y - 40, x + w + 8, y + h + 8))
        p = os.path.join(OUT, f'_ui_{tag}.png')
        im.save(p)
        print('shot ->', p, im.size)

    def _shot_closed(self):
        self._grab('closed')

    def _find_adv(self):
        stack = [self]
        while stack:
            w = stack.pop()
            stack.extend(w.winfo_children())
            try:
                if '高级设置' in (w.cget('text') or ''):
                    return w
            except Exception:
                pass
        return None

    def _toggle(self):
        b = self._find_adv()
        if b is None:
            print('!! 未找到「高级设置」按钮')
            return
        b.invoke()
        print('点击了高级设置：', b.cget('text'))

    def _shot_open(self):
        self._grab('open')


tk.Tk = _Tk
import wushuang_dl  # noqa: E402

wushuang_dl.run_gui()
print('GUI 自检结束')
