# -*- coding: utf-8 -*-
"""
无双下载 v5.2 · 单文件绿色版
支持：微 / 抖 / Bl / 快 / 红 / 博 / 头 / 皮 / 直链
零第三方依赖（仅 Python 标准库），无需安装浏览器，无需登录任何账号。

原理：走公开解析接口拿无水印直链，再用标准库直接下载。
     Bl 走官方接口（可选清晰度、多P）。
     解析接口支持多源容错，可在「高级设置」里改成你自己的备用源。
     接口是公益服务，随时可能失效，「高级设置」里给了替换办法。

用法：
    双击运行              → 图形界面
    无双下载.py 链接…     → 命令行直接下载
    无双下载.py -h        → 查看命令行参数
"""
import json
import os
import queue
import re
import shutil
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

APP_NAME = '无双下载'
APP_VER = 'v5.2'

# 默认保存目录：系统盘根目录下的 Downloads（一般是 C:\Downloads）
DEFAULT_DIR = os.path.join(os.environ.get('SystemDrive', 'C:') + os.sep, 'Downloads')
FALLBACK_DIR = os.path.join(os.path.expanduser('~'), 'Downloads', '短视频')

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36')

# 解析接口模板：{name} = 平台标识，{url} = 链接（已 urlencode）
# 支持配多个源：用 | 或换行分隔，按顺序尝试，前一个失败自动换下一个（界面里也能改）。
# 公益免费接口有单点依赖风险 —— 哪天挂了，把你的备用源接到后面即可，格式照抄。
API_TEMPLATE = 'https://api.bugpk.com/api/{name}?url={url}'

# 平台标识 → (接口名, 下载时用的 Referer)
PLATFORMS = {
    'douyin':    ('douyin',   'https://www.douyin.com/'),
    'wxsph':     ('wxsph',    'https://channels.weixin.qq.com/'),
    'kuaishou':  ('kuaishou', 'https://www.kuaishou.com/'),
    'xhs':       ('xhs',      'https://www.xiaohongshu.com/'),
    'weibo':     ('weibo',    'https://weibo.com/'),
    'toutiao':   ('toutiao',  'https://www.toutiao.com/'),
    'pipixia':   ('pipixia',  'https://www.pipixia.com/'),
}

PLATFORM_CN = {
    'douyin': '抖', 'wxsph': '微', 'bili': 'Bl', 'kuaishou': '快',
    'xhs': '红', 'weibo': '博', 'toutiao': '头', 'pipixia': '皮',
    'mp4': '直链', 'm3u8': '直链',
}

# 界面与日志里统一用的平台简称（对外不写全称）
PLATFORM_BRIEF = '微 / 抖 / Bl / 快 / 红 / 博 / 头 / 皮 / 直链'

URL_RE = re.compile(r'https?://[^\s，,。、；;）)】\]}"\'<>（）]+')


def ensure_dir(path):
    """确保目录存在。C 盘根目录这类受限位置建不了时，退回用户下载目录。"""
    p = (path or '').strip() or DEFAULT_DIR
    for cand in (p, FALLBACK_DIR):
        try:
            os.makedirs(cand, exist_ok=True)
            return cand
        except Exception:
            continue
    return os.path.expanduser('~')


def probe_api(tpl, timeout=12):
    """探测单个解析源是否还活着。返回 (是否可达, 说明)。

    判定标准是「能不能拿到 HTTP 响应」——接口对无效链接也会回 JSON 错误码，
    那恰恰说明服务在线；连不上或超时才算挂。
    """
    t = str(tpl or '').strip()
    if not t:
        return False, '地址为空'
    if '{name}' not in t or '{url}' not in t:
        return False, '格式不对：缺少 {name} 或 {url} 占位符'
    api = t.format(name='wxsph', url=urllib.parse.quote('https://example.com/v', safe=''))
    try:
        body, _, _ = http(api, {'Accept': 'application/json'}, timeout=timeout)
    except urllib.error.HTTPError as e:
        return True, f'服务在线（HTTP {e.code}，属正常响应）'
    except Exception as e:
        return False, f'连不上：{repr(e)[:56]}'
    txt = body.decode('utf-8', 'ignore').strip()
    if txt[:1] in ('{', '['):
        try:
            d = json.loads(txt)
            code = d.get('code') if isinstance(d, dict) else None
            note = f'code={code}' if code is not None else 'JSON'
            return True, f'服务在线（测试链接是无效的，返回 {note} 属正常）'
        except Exception:
            return True, '服务在线（返回 JSON）'
    return True, f'服务在线，但返回的不是 JSON（{len(body)} 字节），接口可能已改版'


def api_templates(tpl=None):
    """把接口配置拆成模板列表（支持 | 或换行分隔多个源，按序尝试）"""
    raw = tpl if tpl is not None else API_TEMPLATE
    if not str(raw or '').strip():
        raw = API_TEMPLATE
    return [s.strip() for s in re.split(r'[|\n]+', str(raw)) if s.strip()]


def verify_media(path, ext):
    """下载后校验文件头，确认拿到的是媒体本身而不是 HTML 错误页。

    返回 (是否像该格式, 失败原因)。mp4 判 ftyp/mkv/FLV 头，图片判各自魔数。
    """
    try:
        with open(path, 'rb') as f:
            head = f.read(16)
    except Exception:
        return True, ''
    if len(head) < 12:
        return False, '文件不完整（不足 12 字节）'
    checks = {
        'mp4':  lambda h: h[4:8] == b'ftyp' or h[:4] == b'\x1aE\xdf\xa3' or h[:3] == b'FLV',
        'jpg':  lambda h: h[:3] == b'\xff\xd8\xff',
        'png':  lambda h: h[:4] == b'\x89PNG',
        'gif':  lambda h: h[:4] == b'GIF8',
        'webp': lambda h: h[:4] == b'RIFF' and h[8:12] == b'WEBP',
    }
    fn = checks.get((ext or '').lower())
    if not fn:
        return True, ''
    if fn(head):
        return True, ''
    low = head[:6].lower().lstrip()
    if low.startswith(b'<') or low.startswith(b'{') or low[:1] == b'\xef':
        return False, '下到的是网页/JSON 而不是媒体文件（直链多半已过期或需鉴权）'
    return False, f'文件头不是有效的 {ext}（{head[:8].hex()}），可能无法播放'


# ============================== 通用小工具 ==============================

def http(url, headers=None, timeout=25, method='GET'):
    h = {'User-Agent': UA, 'Accept': '*/*'}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), dict(r.headers), getattr(r, 'status', 200)


def extract_urls(text):
    """从整段分享文案里把链接抠出来（分享文案常常前后带一堆字）"""
    out = []
    for m in URL_RE.finditer(text or ''):
        u = m.group(0).rstrip('.,;:!?)】')
        if u not in out:
            out.append(u)
    return out


def safe_name(name, limit=80):
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', '_', str(name or ''))
    name = re.sub(r'[\U0001F000-\U0001FAFF\u2600-\u27BF]', '', name)   # 去掉 emoji
    name = re.sub(r'\s+', ' ', name).strip().strip('.')
    bad = ('CON', 'PRN', 'AUX', 'NUL') + tuple(f'COM{i}' for i in range(1, 10)) \
        + tuple(f'LPT{i}' for i in range(1, 10))
    if name.upper().split('.')[0] in bad:
        name = '_' + name
    return (name[:limit].rstrip() if len(name) > limit else name) or 'video'


def human(n):
    n = float(n or 0)
    for u in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or u == 'GB':
            return f'{n:.1f}{u}' if u != 'B' else f'{int(n)}B'
        n /= 1024


def detect(url):
    """认出链接属于哪个平台"""
    u = url.strip().lower()
    if re.fullmatch(r'bv[0-9a-z]{10}', u) or re.fullmatch(r'av\d+', u):
        return 'bili'
    if 'bilibili.com' in u or 'b23.tv' in u or 'bili2233' in u:
        return 'bili'
    if 'weixin.qq.com/sph' in u or 'finder-preview' in u or 'channels.weixin.qq.com' in u:
        return 'wxsph'
    for key, hosts in (
        ('douyin',   ('douyin.com', 'iesdouyin.com')),
        ('kuaishou', ('kuaishou.com', 'chenzhongtech.com')),
        ('xhs',      ('xiaohongshu.com', 'xhslink.com')),
        ('weibo',    ('weibo.com', 'weibo.cn', 't.cn')),
        ('toutiao',  ('toutiao.com', 'ixigua.com')),
        ('pipixia',  ('pipixia.com', 'ppxia.com', 'ippx')),
    ):
        if any(h in u for h in hosts):
            return key
    if u.startswith('http'):
        if '.m3u8' in u:
            return 'm3u8'
        if '.mp4' in u or '.mov' in u:
            return 'mp4'
    return 'other'


# ============================== Bl（官方接口）==============================

BILI_QN = {120: '4K', 116: '1080P60', 80: '1080P', 64: '720P', 32: '480P', 16: '360P'}


def parse_bilibili(url, quality=80, all_parts=True):
    txt = url.strip()
    if 'b23.tv' in txt or 'bili2233' in txt:
        try:
            with urllib.request.urlopen(urllib.request.Request(txt, headers={'User-Agent': UA}),
                                       timeout=15) as r:
                txt = r.geturl()
        except Exception:
            pass
    m = re.search(r'(BV[0-9A-Za-z]{10})', txt)
    aid = re.search(r'(?:av|AV)(\d+)', txt)
    if not m and not aid:
        raise ValueError('未识别到 Bl 的 BV/av 号')
    ref = f'https://www.bilibili.com/video/{m.group(1)}' if m else 'https://www.bilibili.com/'
    H = {'User-Agent': UA, 'Referer': ref, 'Origin': 'https://www.bilibili.com',
         'Accept': 'application/json, text/plain, */*', 'Accept-Language': 'zh-CN,zh;q=0.9',
         'Sec-Fetch-Mode': 'cors', 'Sec-Fetch-Site': 'same-site', 'Sec-Fetch-Dest': 'empty',
         'Cookie': 'buvid3=' + ''.join(__import__('random').choice('0123456789abcdef')
                                       for _ in range(44))}
    api = ('https://api.bilibili.com/x/web-interface/view?bvid=' + m.group(1)) if m else \
          ('https://api.bilibili.com/x/web-interface/view?aid=' + aid.group(1))
    body, _, _ = http(api, H)
    info = json.loads(body.decode('utf-8', 'ignore'))['data']
    title, up, pages = info['title'], info['owner']['name'], info['pages']
    cids = [p['cid'] for p in pages] if all_parts else [pages[0]['cid']]
    items = []
    for cid in cids:
        pu = (f'https://api.bilibili.com/x/player/playurl?bvid={info["bvid"]}'
              f'&cid={cid}&qn={quality}&otype=json')
        d = json.loads(http(pu, H)[0].decode('utf-8', 'ignore'))
        if d.get('code') != 0:
            raise RuntimeError(f'取流失败：{d.get("message") or d.get("code")}')
        data = d['data']
        su = None
        if data.get('durl'):
            su = max(data['durl'], key=lambda x: x.get('size', 0))['url']
        else:
            st = data.get('streams') or {}
            vids = (st.get('dash') or {}).get('video') or []
            if vids:
                su = max(vids, key=lambda x: x.get('bandwidth', 0)).get('baseUrl')
            elif isinstance(st, list) and st:
                su = st[0].get('url')
        if not su:
            raise RuntimeError('该清晰度无可用流（可能是大会员专属，请调低画质）')
        pno = next((p.get('page') for p in pages if p['cid'] == cid), None)
        t = title if len(cids) == 1 else f'{title} P{pno}'
        items.append(Job(su, f'{up}_{t}', 'mp4', 'https://www.bilibili.com/', 'bili'))
    return items


# ============================== 通用解析（公开接口）==============================

def parse_api(url, kind, api_template=None, log=None, multi=False):
    """调公开解析接口，拿无水印直链。返回 Job 列表。

    multi=False（默认）只取最优的一份 —— 接口常把同一视频的多档清晰度都列出来，
    全下会一链接出好几个文件，不符合「输入链接下一条视频」的预期。
    """
    name, referer = PLATFORMS[kind]
    tpls = api_templates(api_template)
    if not tpls:
        raise RuntimeError('没有配置解析接口')
    data, errors = None, []
    for i, tpl in enumerate(tpls, 1):
        api = tpl.format(name=name, url=urllib.parse.quote(url, safe=''))
        if log:
            tag = f'（源 {i}/{len(tpls)}）' if len(tpls) > 1 else ''
            log(f'    解析{tag}：{api[:96]}')
        try:
            body, _, _ = http(api, {'Accept': 'application/json'}, timeout=30)
            txt = body.decode('utf-8', 'ignore')
            d = json.loads(txt)
            if str(d.get('code')) not in ('200', '0', '1'):
                raise RuntimeError(d.get('msg') or d.get('message') or txt[:80])
            dd = d.get('data') or {}
            if isinstance(dd, list):
                dd = (dd[0] if dd else {}) or {}
            if not isinstance(dd, dict) or not dd:
                raise RuntimeError('返回结构异常或为空')
            data = dd
            break
        except Exception as e:
            errors.append(f'源{i} {repr(e)[:70]}')
            if log and len(tpls) > 1:
                log(f'      ✘ 该源不可用：{repr(e)[:70]}')
    if data is None:
        raise RuntimeError('全部解析源都失败 —— ' + '；'.join(errors))

    title = (data.get('title') or data.get('desc') or '').strip()
    author = ''
    a = data.get('author')
    if isinstance(a, dict):
        author = a.get('name') or a.get('nickname') or ''
    elif isinstance(a, str):
        author = a
    base = ((author + '_') if author else '') + (title or f'{PLATFORM_CN.get(kind, kind)}视频')

    urls = []
    main = data.get('url') or data.get('video') or data.get('video_url') or data.get('download_url')
    if main:
        urls.append((data.get('quality') or '默认', main))
    if multi or not urls:
        for it in (data.get('video_backup') or data.get('videosList') or []):
            if isinstance(it, dict):
                u = it.get('url') or it.get('play_url')
                if u:
                    urls.append((str(it.get('quality') or it.get('label') or ''), u))
            elif isinstance(it, str):
                urls.append(('', it))
    # 图集（红等）—— 图集没有主视频地址，单独收
    pics = []
    for it in (data.get('picsList') or data.get('images') or []):
        if isinstance(it, str):
            pics.append(it)
        elif isinstance(it, dict):
            u = it.get('url') or it.get('origin_url') or it.get('img_url')
            if u:
                pics.append(u)

    if not urls and not pics:
        raise RuntimeError('接口没返回可下载地址（该内容可能是图文/直播/已删除）')

    jobs = []
    seen = set()
    for q, u in urls[:1] if not multi else urls:
        if u in seen:
            continue
        seen.add(u)
        jobs.append(Job(u, safe_name(base) + (f'_{q}' if multi and q else ''),
                        'mp4', referer, kind))
    for i, u in enumerate(pics, 1):
        if u in seen:
            continue
        seen.add(u)
        ext = 'jpg'
        m = re.search(r'\.(jpe?g|png|webp|gif)(\?|$)', u, re.I)
        if m:
            ext = m.group(1).lower().replace('jpeg', 'jpg')
        jobs.append(Job(u, f'{safe_name(base)}_{i}', ext, referer, kind))
    return jobs


# ============================== 下载 ==============================

class Job:
    __slots__ = ('url', 'title', 'ext', 'referer', 'kind')

    def __init__(self, url, title, ext='mp4', referer='', kind=''):
        self.url, self.title, self.ext = url, title or 'video', ext
        self.referer, self.kind = referer, kind

    def path(self, folder, idx=0, total=1):
        base = safe_name(self.title)
        if total > 1:
            base = f'{idx:02d}_{base}'
        p = os.path.join(folder, base + '.' + self.ext)
        n = 1
        while os.path.exists(p):
            p = os.path.join(folder, f'{base}({n}).{self.ext}')
            n += 1
        return p


def download(job, path, log=print, tries=3):
    """带进度、断点重试的下载。"""
    h = {'User-Agent': UA, 'Accept': '*/*', 'Accept-Language': 'zh-CN,zh;q=0.9'}
    if job.referer:
        h['Referer'] = job.referer
    last = ''
    for t in range(tries):
        try:
            req = urllib.request.Request(job.url, headers=h)
            with urllib.request.urlopen(req, timeout=60) as r, open(path, 'wb') as f:
                total = int(r.headers.get('Content-Length') or 0)
                got = 0
                t0 = time.time()
                while True:
                    buf = r.read(256 * 1024)
                    if not buf:
                        break
                    f.write(buf)
                    got += len(buf)
                    if total and time.time() - t0 > 0.6:
                        t0 = time.time()
                        pct = got * 100 // total
                        spd = got / max(time.time() - (t0 - 0.6), 0.001)
                        log(f'      {pct:3d}%  {human(got)}/{human(total)}')
            sz = os.path.getsize(path)
            if sz < 2048:
                raise RuntimeError('文件过小，可能是解析失败返回的页面')
            ok, why = verify_media(path, job.ext)
            if not ok:
                raise RuntimeError(why)
            return sz
        except Exception as e:
            last = repr(e)[:120]
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass
            if t < tries - 1:
                log(f'      第 {t + 1} 次失败，重试…（{last}）')
                time.sleep(1.2 * (t + 1))
                if t == 0 and 'Referer' in h:
                    h.pop('Referer', None)      # 有的 CDN 不认 Referer，去掉再试
    raise RuntimeError(last or '下载失败')


def download_m3u8(url, path, log=print):
    """m3u8 分片抓取 + 拼接（原样拼接，绝大多数播放器可播）"""
    body, _, _ = http(url, {'Referer': url}, timeout=25)
    txt = body.decode('utf-8', 'ignore')
    segs = []
    for line in txt.splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            segs.append(line if line.startswith('http') else urllib.parse.urljoin(url, line))
    if not segs:
        raise RuntimeError('m3u8 里没有分片')
    log(f'      m3u8 共 {len(segs)} 个分片')
    tmp = path + '.parts'
    os.makedirs(tmp, exist_ok=True)
    try:
        for i, s in enumerate(segs):
            p = os.path.join(tmp, f'{i:05d}.ts')
            for _ in range(2):
                try:
                    b, _, _ = http(s, {'Referer': url}, timeout=45)
                    with open(p, 'wb') as f:
                        f.write(b)
                    break
                except Exception:
                    time.sleep(0.6)
            if i % 10 == 0:
                log(f'      {i + 1}/{len(segs)}')
        with open(path, 'wb') as out:
            for i in range(len(segs)):
                p = os.path.join(tmp, f'{i:05d}.ts')
                if os.path.exists(p):
                    with open(p, 'rb') as f:
                        shutil.copyfileobj(f, out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    sz = os.path.getsize(path)
    if sz < 2048:
        raise RuntimeError('分片合并结果为空')
    return sz


def handle(link, outdir, quality=80, all_parts=True, api_template=None, log=print, multi=False):
    """解析单个链接 → 下载。返回成功条数。"""
    kind = detect(link)
    log(f'· [{PLATFORM_CN.get(kind, kind)}] {link[:88]}')
    if kind == 'other':
        raise RuntimeError(f'不支持的链接（目前支持：{PLATFORM_BRIEF}）')
    if kind == 'bili':
        jobs = parse_bilibili(link, quality=quality, all_parts=all_parts)
    elif kind in ('mp4', 'm3u8'):
        jobs = [Job(link, safe_name(os.path.basename(link.split('?')[0])) or '直链视频',
                    'ts' if kind == 'm3u8' else 'mp4', '', kind)]
    else:
        jobs = parse_api(link, kind, api_template, log, multi=multi)
        if kind == 'wxsph':
            log('    提示：微直链有效期约 24 小时，过期需重新分享取新链接')

    ok = 0
    for i, job in enumerate(jobs, 1):
        p = job.path(outdir, i, len(jobs))
        log(f'  [{i}/{len(jobs)}] {job.title[:66]}')
        try:
            if job.kind == 'm3u8' or job.ext == 'ts':
                sz = download_m3u8(job.url, p, log)
            else:
                sz = download(job, p, log)
            log(f'    ✔ 完成  {human(sz)}  →  {os.path.basename(p)}')
            ok += 1
        except Exception as e:
            log(f'    ✘ 失败：{e}')
            if job.kind == 'wxsph':
                log('      （微链接约 24 小时过期，请重新分享取新链接；'
                    '或用附带的 wx_video_download 工具兜底）')
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
    return ok


# ============================== 图形界面 ==============================

def icon_path():
    """定位图标文件：打包后从 _MEIPASS 取，源码运行从同级/上级 assets 取"""
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (os.path.join(getattr(sys, '_MEIPASS', here), 'wushuang.ico'),
              os.path.join(here, 'wushuang.ico'),
              os.path.join(here, 'assets', 'wushuang.ico'),
              os.path.join(here, '..', 'assets', 'wushuang.ico')):
        if os.path.exists(p):
            return os.path.abspath(p)
    return None


def set_app_id():
    """让任务栏/进程图标正确归属（否则 Windows 会显示 python 默认图标）"""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            'wushuang.downloader')
    except Exception:
        pass


def run_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, scrolledtext, messagebox

    set_app_id()
    root = tk.Tk()
    root.title(f'{APP_NAME} {APP_VER} · 多平台无水印下载')
    root.geometry('940x734')
    root.minsize(820, 620)
    ic = icon_path()
    if ic:                                  # 窗口与任务栏图标
        try:
            root.iconbitmap(default=ic)
        except Exception:
            pass
    q = queue.Queue()
    state = {'running': False, 'stop': False, 'chrome': None, 'ph': False}

    tk.Label(root, text='把链接粘进来 —— 整段分享文案可以直接粘，会自动把链接抠出来：',
             font=('Microsoft YaHei', 10)).pack(anchor='w', padx=12, pady=(12, 0))
    tk.Label(root, text=f'支持：{PLATFORM_BRIEF}',
             font=('Microsoft YaHei', 9), foreground='#888').pack(anchor='w', padx=12, pady=(0, 3))
    links = tk.Text(root, height=9, font=('Consolas', 10), relief='solid', bd=1)
    links.pack(fill='x', padx=12)

    # 用灰色占位符给格式示例。不预填真实链接 —— 一打开界面就带着别人的视频链接不合适。
    links.tag_configure('ph', foreground='#a9a9a9')
    PH_TEXT = ('https://weixin.qq.com/sph/1234567\n'
               'https://v.douyin.com/1234567/\n'
               'https://www.bilibili.com/video/1234567')

    def clear_ph(_e=None):
        if state['ph']:
            links.delete('1.0', 'end')
            state['ph'] = False

    def show_ph(_e=None):
        links.delete('1.0', 'end')
        links.insert('1.0', PH_TEXT, 'ph')
        state['ph'] = True

    links.bind('<FocusIn>', clear_ph)
    links.bind('<Button-1>', clear_ph)
    links.bind('<Key>', lambda e: clear_ph() if state['ph'] else None)
    links.bind('<FocusOut>',
               lambda e: show_ph() if not links.get('1.0', 'end').strip() else None)
    show_ph()

    tk.Label(root, text='建议一次下一个视频 —— 批量容易被接口限流；确需批量请走命令行（参数见说明文档）。',
             font=('Microsoft YaHei', 9), foreground='#b06a2c').pack(anchor='w', padx=12, pady=(3, 0))

    bar = ttk.Frame(root)
    bar.pack(fill='x', padx=12, pady=8)
    btn_start = ttk.Button(bar, text='开始下载')
    btn_start.pack(side='left')
    ttk.Button(bar, text='清空', command=lambda: show_ph()).pack(side='left', padx=6)
    ttk.Button(bar, text='打开文件夹', command=lambda: _open(outdir.get())).pack(side='left', padx=6)

    # 解析接口默认收起，普通使用不必关心
    adv_var = tk.BooleanVar(value=False)
    btn_adv = ttk.Button(bar, text='高级设置 ⌄')
    btn_adv.pack(side='right')

    opt = ttk.Frame(root)
    opt.pack(fill='x', padx=12)
    ttk.Label(opt, text='保存到：').pack(side='left')
    outdir = tk.StringVar(value=ensure_dir(DEFAULT_DIR))
    ttk.Entry(opt, textvariable=outdir, width=46).pack(side='left', padx=4)
    ttk.Button(opt, text='…', width=4,
               command=lambda: outdir.set(filedialog.askdirectory() or outdir.get())
               ).pack(side='left')
    ttk.Label(opt, text='Bl 画质：').pack(side='left', padx=(12, 2))
    qn = tk.StringVar(value='80 (1080P)')
    ttk.Combobox(opt, textvariable=qn, width=12, state='readonly',
                 values=['120 (4K)', '116 (1080P60)', '80 (1080P)', '64 (720P)', '32 (480P)']
                 ).pack(side='left')
    allp = tk.BooleanVar(value=True)
    ttk.Checkbutton(opt, text='Bl 多P全下', variable=allp).pack(side='left', padx=8)
    multi = tk.BooleanVar(value=False)
    ttk.Checkbutton(opt, text='保留全部清晰度', variable=multi).pack(side='left', padx=4)

    # —— 高级设置：解析接口（默认折叠，日常使用不必关心）——
    opt2 = ttk.Frame(root)
    row1 = ttk.Frame(opt2)
    row1.pack(fill='x')
    ttk.Label(row1, text='解析接口：').pack(side='left')
    api_var = tk.StringVar(value=API_TEMPLATE)
    ttk.Entry(row1, textvariable=api_var).pack(side='left', fill='x', expand=True, padx=4)
    btn_test = ttk.Button(row1, text='测试', width=6)
    btn_test.pack(side='left')
    ttk.Label(opt2, text='多个源用 | 分隔，按顺序尝试，前一个失败自动换下一个',
              foreground='#888').pack(anchor='w', pady=(4, 0))
    ttk.Label(opt2, text=(
        '接口挂了怎么办：这是公益服务，没有服务承诺，随时可能被关停或换域名。\n'
        '换源办法：搜索「短视频解析 免费接口」，挑一个能用的，把地址按上面格式填回来 —— '
        '只换域名，{name} 和 {url} 两个占位符照抄，多个源用 | 分隔。填好点「测试」自检。\n'
        '本工具仅用于下载你自己发布的、或已获授权的素材。'),
        foreground='#888', justify='left', wraplength=880).pack(anchor='w', pady=(2, 0))

    pf = ttk.Frame(root)
    pf.pack(fill='x', padx=12, pady=8)
    pb = ttk.Progressbar(pf, mode='determinate', maximum=100)
    pb.pack(fill='x')
    status = tk.StringVar(value='就绪')
    ttk.Label(pf, textvariable=status, foreground='#1a6b3c').pack(anchor='w')

    def toggle_adv():
        """展开/收起解析接口设置"""
        adv_var.set(not adv_var.get())
        if adv_var.get():
            opt2.pack(fill='x', padx=12, pady=(2, 0), before=pf)
            btn_adv.config(text='高级设置 ⌃')
        else:
            opt2.pack_forget()
            btn_adv.config(text='高级设置 ⌄')
    btn_adv.config(command=toggle_adv)

    def test_api():
        """逐个探测解析源，告诉用户当前配置还能不能用"""
        tpls = api_templates(api_var.get())
        if not tpls:
            messagebox.showwarning(APP_NAME, '请先填写解析接口地址')
            return
        btn_test.config(state='disabled', text='检测中')

        def work():
            res = []
            for i, t in enumerate(tpls, 1):
                ok, msg = probe_api(t)
                res.append((ok, f'源 {i}　【{"可用" if ok else "不可用"}】  {msg}'))
            alive = sum(1 for ok, _ in res if ok)
            lines = [x for _, x in res]

            def show():
                btn_test.config(state='normal', text='测试')
                if alive:
                    tail = f'\n\n结论：{alive}/{len(res)} 个源可用，直接下载即可。'
                else:
                    tail = ('\n\n结论：全部不可用 —— 接口多半已失效。\n'
                            '换源办法：搜索「短视频解析 免费接口」，找一个新的，'
                            '按原格式（只换域名）填回来，多个源用 | 分隔。')
                messagebox.showinfo(f'{APP_NAME} · 接口检测', '\n'.join(lines) + tail)
            root.after(0, show)

        threading.Thread(target=work, daemon=True).start()
    btn_test.config(command=test_api)

    lf = ttk.Frame(root)
    lf.pack(fill='both', expand=True, padx=12, pady=(0, 12))
    logw = scrolledtext.ScrolledText(lf, font=('Consolas', 9), relief='solid', bd=1)
    logw.pack(fill='both', expand=True)
    logw.insert('end', f'{APP_NAME} {APP_VER} 就绪。粘贴链接 → 点「开始下载」，无需登录、无需安装浏览器。\n'
                       '整段分享文案可以直接粘，会自动把链接抠出来；建议一次下一个，批量容易被接口限流。\n'
                       '若某个链接失败，多半是该视频被删/仅粉丝可见，换一条再试。\n')

    def emit(m):
        q.put(str(m))

    def drain():
        try:
            while True:
                logw.insert('end', q.get_nowait() + '\n')
                logw.see('end')
        except queue.Empty:
            pass
        root.after(120, drain)

    def worker(items, folder, quality, parts, tpl, multi):
        state['running'] = True
        total_ok = 0
        for n, line in enumerate(items, 1):
            if state['stop']:
                emit('已停止')
                break
            status.set(f'处理 {n}/{len(items)}')
            pb['value'] = (n - 1) * 100.0 / max(len(items), 1)
            try:
                total_ok += handle(line, folder, quality, parts, tpl, emit, multi)
            except Exception as e:
                emit(f'  ✘ {e}')
            pb['value'] = n * 100.0 / max(len(items), 1)
        emit(f'—— 全部结束，成功 {total_ok} 个文件，位置：{folder}')
        status.set(f'完成（成功 {total_ok} 个）')
        state['running'] = False
        try:
            os.startfile(folder)
        except Exception:
            pass

    def start():
        if state['running']:
            emit('正在下载中，请等这次跑完')
            return
        raw = '' if state['ph'] else links.get('1.0', 'end').strip()
        items = extract_urls(raw) or [x.strip() for x in raw.splitlines() if x.strip()]
        # 整段文案里没链接时，按行当作 BV 号之类的短标识
        if not items:
            emit('请先粘贴链接')
            return
        if len(items) > 1:
            emit(f'提示：这次有 {len(items)} 条。批量容易被接口限流，建议一次下一个。')
        folder = ensure_dir(outdir.get())
        outdir.set(folder)
        try:
            quality = int(re.sub(r'\D', '', qn.get().split('(')[0]) or 80)
        except Exception:
            quality = 80
        state['stop'] = False
        show_ph()
        emit(f'开始处理 {len(items)} 条链接 → {folder}')
        threading.Thread(target=worker,
                         args=(items, folder, quality, allp.get(), api_var.get().strip(),
                               multi.get()),
                         daemon=True).start()

    btn_start.config(command=start)
    root.protocol('WM_DELETE_WINDOW', lambda: (state.update(stop=True), root.destroy()))
    drain()
    root.mainloop()


def _open(folder):
    try:
        os.startfile(ensure_dir(folder))
    except Exception:
        pass


# ============================== 命令行 ==============================

def run_cli(argv):
    outdir = DEFAULT_DIR
    quality, all_parts, tpl, raw, multi = 80, True, API_TEMPLATE, [], False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ('-o', '--out') and i + 1 < len(argv):
            outdir, i = argv[i + 1], i + 2
        elif a in ('-q', '--quality') and i + 1 < len(argv):
            quality, i = int(argv[i + 1]), i + 2
        elif a in ('--api',) and i + 1 < len(argv):
            tpl, i = argv[i + 1], i + 2
        elif a in ('--first-part',):
            all_parts, i = False, i + 1
        elif a in ('--all-quality',):
            multi, i = True, i + 1
        elif a in ('-h', '--help'):
            print(__doc__)
            return 0
        else:
            raw.append(a)
            i += 1
    links = []
    for r in raw:
        links += extract_urls(r) or [r]
    if not links:
        print(__doc__)
        return 1
    outdir = ensure_dir(outdir)
    print(f'保存到：{outdir}\n共 {len(links)} 条链接\n')
    tot = 0
    for n, link in enumerate(links, 1):
        print(f'== {n}/{len(links)} ==')
        try:
            tot += handle(link, outdir, quality, all_parts, tpl, print, multi)
        except Exception as e:
            print(f'  ✘ {e}')
    print(f'\n全部结束，成功 {tot} 个文件 → {outdir}')
    return 0


def main():
    argv = sys.argv[1:]
    if argv:
        sys.exit(run_cli(argv))
    set_app_id()
    try:
        import tkinter  # noqa: F401
    except ImportError:
        print('当前 Python 缺少 tkinter，无法显示界面。\n'
              f'可直接用命令行：{os.path.basename(sys.argv[0])} 链接 -o 保存目录')
        sys.exit(1)
    run_gui()


if __name__ == '__main__':
    main()
