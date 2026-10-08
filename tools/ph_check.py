# -*- coding: utf-8 -*-
"""输入框占位符自检：验证灰字示例不会被当成真链接提交。

要验的 5 件事（都是这个功能最容易出的 bug）：
  1. 初始态：输入框显示灰色占位符
  2. 占位符状态下点「开始下载」→ 必须提示「请先粘贴链接」，绝不能真去下 3 条
  3. 鼠标点进来（FocusIn）→ 占位符消失，输入框真空
  4. 输入内容后 → 内容保留，不被占位符覆盖
  5. 清空后失焦 → 占位符恢复（否则用户看到一个空框，不知道格式）
"""
import os
import sys
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PH_FIRST_LINE = 'https://weixin.qq.com/sph/1234567'
_holder = {}
_OrigTk = tk.Tk
LOG = []


def find_all(w, pred, out=None):
    out = [] if out is None else out
    for c in w.winfo_children():
        if pred(c):
            out.append(c)
        find_all(c, pred, out)
    return out


class _Tk(_OrigTk):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        _holder['root'] = self
        self.after(1200, self.s1_initial)
        self.after(2000, self.s2_submit_ph)
        self.after(2800, self.s3_focus)
        self.after(3400, self.s4_typing)
        self.after(4000, self.s5_refocus_out)
        self.after(4600, self.report)

    # ---- 定位控件 ----
    def links(self):
        for c in self.winfo_children():
            if isinstance(c, tk.Text) and c.winfo_class() == 'Text':
                return c
        return None

    def logbox(self):
        ts = find_all(self, lambda c: isinstance(c, tk.Text))
        return ts[-1] if ts else None

    def btn(self, text):
        for c in find_all(self, lambda c: c.winfo_class() == 'TButton'):
            try:
                if text in (c.cget('text') or ''):
                    return c
            except Exception:
                pass
        return None

    def check(self, name, ok, detail=''):
        LOG.append((name, ok, detail))
        print(f'  [{"PASS" if ok else "FAIL"}] {name}  {detail}')

    # ---- 各步 ----
    def s1_initial(self):
        t = self.links()
        txt = t.get('1.0', 'end').strip()
        self.check('1 初始显示占位符', txt.splitlines()[0] == PH_FIRST_LINE
                   if txt else False, repr(txt[:40]))
        ranges = t.tag_ranges('ph')
        self.check('1b 占位符打了灰色 tag', len(ranges) >= 2, f'tag 范围 {len(ranges) // 2} 段')

    def s2_submit_ph(self):
        b = self.btn('开始下载')
        if not b:
            self.check('2 找到开始下载按钮', False)
            return
        b.invoke()
        self.update()
        time.sleep(0.5)
        self.update()
        log = self.logbox().get('1.0', 'end')
        self.check('2 占位符未触发下载', '开始处理' not in log, '')
        self.check('2b 提示请先粘贴链接', '请先粘贴链接' in log,
                   '日志尾部：' + log.strip().splitlines()[-1][:30])

    def s3_focus(self):
        t = self.links()
        t.focus_force()
        self.update()
        time.sleep(0.3)
        self.update()
        txt = t.get('1.0', 'end').strip()
        self.check('3 点击后占位符消失', txt == '', repr(txt[:40]))
        self.check('3b 清除占位符标记', 'ph' not in t.tag_names('1.0'), '')

    def s4_typing(self):
        t = self.links()
        t.insert('1.0', 'https://v.douyin.com/gqlWj_Ft4XA/')
        self.update()
        txt = t.get('1.0', 'end').strip()
        self.check('4 输入内容保留', 'gqlWj_Ft4XA' in txt, repr(txt[:50]))

    def s5_refocus_out(self):
        t = self.links()
        t.delete('1.0', 'end')
        self.update()
        self._grab_shot()          # 清空但还没失焦：此时应是真的空
        empty_now = t.get('1.0', 'end').strip() == ''
        self.check('5 清空后失焦前为空', empty_now, '')
        self.focus_force()          # 焦点移走 → 触发 FocusOut
        self.update()
        time.sleep(0.4)
        self.update()
        txt = t.get('1.0', 'end').strip()
        self.check('5b 失焦后占位符恢复', txt.splitlines()[0] == PH_FIRST_LINE if txt else False,
                   repr(txt[:40]))

    def _grab_shot(self):
        try:
            from PIL import ImageGrab
            self.update_idletasks()
            x, y = self.winfo_rootx(), self.winfo_rooty()
            w, h = self.winfo_width(), self.winfo_height()
            p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             'assets', '_ph_focused.png')
            ImageGrab.grab(bbox=(x - 8, y - 40, x + w + 8, y + h + 8)).save(p)
            print('  shot ->', p)
        except Exception as e:
            print('  截图失败：', repr(e))

    def report(self):
        bad = [n for n, ok, _ in LOG if not ok]
        print()
        print(f'总计 {len(LOG)} 项，失败 {len(bad)} 项' + ('：' + '、'.join(bad) if bad else ' —— 全部通过'))
        self.destroy()


tk.Tk = _Tk
import wushuang_dl  # noqa: E402

wushuang_dl.run_gui()
print('占位符自检结束')
