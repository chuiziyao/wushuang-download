# -*- coding: utf-8 -*-
"""无双下载 · 图标生成器

设计：圆角方形深色底（冷蓝黑渐变）+ 橙红渐变向下箭头 + 底部浅色托盘线。
小尺寸（<40px）自动切简化版，保证 16px 下仍清晰可辨。

产出：各尺寸 PNG、多尺寸 ICO、横版字标、SVG 矢量源。
"""
import os
import struct
import sys

from PIL import Image, ImageDraw, ImageFont

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'assets')
OUT = os.path.abspath(OUT)

# ---------------- 配色 ----------------
BG_TOP = (38, 52, 82)      # #26345 2 冷蓝
BG_BOT = (9, 13, 22)       # #090D16 近黑
ARROW_TOP = (255, 158, 66)  # #FF9E42 橙
ARROW_BOT = (232, 42, 32)   # #E82A20 红
BAR = (233, 239, 246)      # #E9EFF6 浅白

SS = 4                      # 超采样倍数
NAME_CN = '无双下载'


def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def vgrad(size, top, bot):
    """竖直线性渐变"""
    img = Image.new('RGB', (1, size))
    px = img.load()
    for y in range(size):
        px[0, y] = lerp(top, bot, y / max(size - 1, 1))
    return img.resize((size, size), Image.NEAREST)


def rounded_mask(size, radius):
    m = Image.new('L', (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return m


def poly_mask(size, pts):
    m = Image.new('L', (size, size), 0)
    ImageDraw.Draw(m).polygon(pts, fill=255)
    return m


def draw_icon(size, simple=False):
    """返回该尺寸的 RGBA 图标（带透明背景）"""
    S = size * SS
    k = S / 1024.0                      # 以 1024 设计稿为基准缩放

    # ---- 底板 ----
    bg = vgrad(S, BG_TOP, BG_BOT).convert('RGBA')
    radius = int((232 if not simple else 220) * k)
    base = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    base.paste(bg, (0, 0), rounded_mask(S, radius))
    if size >= 64:                      # 细高光边，大尺寸更精致
        hl = Image.new('RGBA', (S, S), (0, 0, 0, 0))
        ImageDraw.Draw(hl).rounded_rectangle(
            [int(1.6 * k), int(1.6 * k), S - int(1.6 * k), S - int(1.6 * k)],
            radius=int(radius - 1.6 * k), outline=(255, 255, 255, 34), width=max(int(2 * k), 1))
        base = Image.alpha_composite(base, hl)

    # ---- 箭头：圆角箭杆 + 尖三角（两个独立形状叠加，交界自然融合）----
    if simple:
        cx, sc = S / 2.0, 1.06
        mid = 485 * k
        y0 = mid - (862 - 108) * k * sc / 2
        shaft_w, head_w = 160 * k * sc, 460 * k * sc
        y1 = y0 + (485 - 108) * k * sc
        y2 = y0 + (862 - 108) * k * sc
    else:
        cx, sc = S / 2.0, 1.0
        y0, y1, y2 = 140 * k, 485 * k, 745 * k
        shaft_w, head_w = 160 * k, 460 * k

    am = Image.new('L', (S, S), 0)
    da = ImageDraw.Draw(am)
    da.rounded_rectangle([cx - shaft_w / 2.0, y0, cx + shaft_w / 2.0, y1 + shaft_w * 0.5],
                         radius=shaft_w / 2.0, fill=255)
    da.polygon([(cx - head_w / 2.0, y1), (cx + head_w / 2.0, y1), (cx, y2)], fill=255)

    arrow = vgrad(S, ARROW_TOP, ARROW_BOT).convert('RGBA')
    base.paste(arrow, (0, 0), am)

    # ---- 托盘线（圆头）----
    d = ImageDraw.Draw(base)
    if not simple:
        bx0, bx1, by0, by1 = 232 * k, 792 * k, 820 * k, 890 * k
        d.rounded_rectangle([bx0, by0, bx1, by1], radius=(by1 - by0) / 2.0, fill=BAR + (255,))
    elif size >= 32:
        bw, by0, by1 = 520 * k, 850 * k, 908 * k
        d.rounded_rectangle([cx - bw / 2, by0, cx + bw / 2, by1],
                            radius=(by1 - by0) / 2.0, fill=BAR + (255,))

    return base.resize((size, size), Image.LANCZOS)


def save_ico(path, pngs_with_sizes):
    """手写 ICO 容器：内嵌各尺寸 PNG（Vista+），质量最优"""
    n = len(pngs_with_sizes)
    header = struct.pack('<HHH', 0, 1, n)
    offset = 6 + 16 * n
    entries, blobs = b'', b''
    for size, data in pngs_with_sizes:
        w = 0 if size >= 256 else size
        entries += struct.pack('<BBBBHHII', w, w, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    with open(path, 'wb') as f:
        f.write(header + entries + blobs)


def find_font(size, bold=True):
    names = ['msyhbd.ttc', 'msyh.ttc', 'simhei.ttf'] if bold else ['msyh.ttc', 'simhei.ttf']
    for n in names:
        p = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts', n)
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def make_wordmark(icon_path, path, H=520):
    """横版字标：图标 + 竖线 + 中文名 + 英文副标（按墨迹实测排版，不裁切）"""
    probe = Image.new('RGBA', (10, 10))
    pd = ImageDraw.Draw(probe)
    f_cn = find_font(198)
    f_en = find_font(56, bold=False)
    cn_w = pd.textlength(NAME_CN, font=f_cn)
    en_w = pd.textlength('WUSHUANG  DOWNLOADER', font=f_en)

    x_text = H + 78
    block_w = max(cn_w, en_w)
    W = int(x_text + block_w + 56)

    canvas = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    ic = Image.open(icon_path).convert('RGBA').resize((H, H), Image.LANCZOS)
    canvas.paste(ic, (0, 0), ic)
    d = ImageDraw.Draw(canvas)

    def put(text, font, tx, ty, fill):
        bx0, by0, _, _ = d.textbbox((0, 0), text, font=font)
        d.text((tx - bx0, ty - by0), text, font=font, fill=fill)
        return d.textbbox((0, 0), text, font=font)

    cn_top = 118
    bb = put(NAME_CN, f_cn, x_text, cn_top, (17, 21, 30, 255))
    cn_h = bb[3] - bb[1]
    cn_bottom = cn_top + cn_h
    put('WUSHUANG  DOWNLOADER', f_en, x_text + 4, cn_bottom + 38, (124, 135, 152, 255))

    # 品牌色竖线，与中文墨迹同高
    d.rounded_rectangle([x_text - 36, cn_top + 8, x_text - 24, cn_bottom - 8],
                        radius=6, fill=ARROW_BOT + (255,))
    canvas.save(path)
    return path


def write_svg(path):
    """矢量源（与 PNG 同参数），便于以后改色/改形"""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="1024" height="1024">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#263452"/><stop offset="1" stop-color="#090D16"/>
    </linearGradient>
    <linearGradient id="ar" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#FF9E42"/><stop offset="1" stop-color="#E82A20"/>
    </linearGradient>
  </defs>
  <rect x="0" y="0" width="1024" height="1024" rx="232" fill="url(#bg)"/>
  <rect x="432" y="140" width="160" height="425" rx="80" fill="url(#ar)"/>
  <path d="M282 485 H742 L512 745 Z" fill="url(#ar)"/>
  <rect x="232" y="820" width="560" height="70" rx="35" fill="#E9EFF6"/>
</svg>
'''
    with open(path, 'w', encoding='utf-8') as f:
        f.write(svg)
    return path


def main():
    os.makedirs(OUT, exist_ok=True)
    sizes = [16, 20, 24, 28, 32, 40, 48, 64, 96, 128, 256, 512, 1024]
    made = []
    for s in sizes:
        im = draw_icon(s, simple=(s < 40))
        p = os.path.join(OUT, f'wushuang_{s}.png')
        im.save(p)
        made.append((s, p))

    # 主图 & 常用名
    for alias, s in [('wushuang_logo.png', 1024), ('wushuang_logo_512.png', 512),
                     ('wushuang_logo_256.png', 256)]:
        Image.open(os.path.join(OUT, f'wushuang_{s}.png')).save(os.path.join(OUT, alias))

    # 多尺寸 ICO
    ico_sizes = [16, 20, 24, 28, 32, 40, 48, 64, 96, 128, 256]
    blobs = []
    for s in ico_sizes:
        with open(os.path.join(OUT, f'wushuang_{s}.png'), 'rb') as f:
            blobs.append((s, f.read()))
    save_ico(os.path.join(OUT, 'wushuang.ico'), blobs)

    make_wordmark(os.path.join(OUT, 'wushuang_512.png'),
                  os.path.join(OUT, 'wushuang_wordmark.png'))
    write_svg(os.path.join(OUT, 'wushuang.svg'))

    print('生成于:', OUT)
    for s, p in made:
        print(f'  {s:>5}px  {os.path.getsize(p) / 1024:7.1f} KB  {os.path.basename(p)}')
    for f in ['wushuang.ico', 'wushuang_wordmark.png', 'wushuang.svg']:
        print(f'  {"":>5}    {os.path.getsize(os.path.join(OUT, f)) / 1024:7.1f} KB  {f}')


if __name__ == '__main__':
    main()
