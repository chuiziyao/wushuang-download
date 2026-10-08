# -*- coding: utf-8 -*-
"""从 PE 文件（exe/dll）中提取内嵌图标，用于验证打包后图标是否真的换掉了。

做法：解析 PE 资源表 → RT_GROUP_ICON(14) 找尺寸-图标ID 映射 → RT_ICON(3) 取数据。
若图标以 PNG 封装（Vista+），可直接落盘为 .png 目检。

用法：python extract_exe_icon.py 无双下载.exe [输出目录]
"""
import os
import struct
import sys

RT_ICON, RT_GROUP_ICON = 3, 14


def _sec_map(data, pe, nsec, size_opt):
    """节表 → [(虚拟地址, 虚拟大小, 文件偏移, 原始大小)]"""
    base = pe + 24 + size_opt
    out = []
    for i in range(nsec):
        off = base + i * 40
        # 节表字段：Name(8) VirtualSize(4) VirtualAddress(4) SizeOfRawData(4) PointerToRawData(4)
        vsz, va, rsz, rptr = struct.unpack('<IIII', data[off + 8:off + 24])
        out.append((va, vsz, rptr, rsz))
    return out


def _rva2off(rva, secs):
    for va, vsz, rptr, rsz in secs:
        if va <= rva < va + max(vsz, rsz):
            return rva - va + rptr
    return None


def load_resources(data):
    """返回 {(type, id, lang): (数据, 长度)}"""
    pe = struct.unpack('<I', data[0x3c:0x40])[0]
    assert data[pe:pe + 4] == b'PE\0\0', '不是有效的 PE 文件'
    nsec = struct.unpack('<H', data[pe + 6:pe + 8])[0]
    size_opt = struct.unpack('<H', data[pe + 20:pe + 22])[0]
    magic = struct.unpack('<H', data[pe + 24:pe + 26])[0]
    dd = pe + 24 + (112 if magic == 0x20b else 96)
    res_rva = struct.unpack('<I', data[dd + 2 * 8:dd + 2 * 8 + 4])[0]
    if not res_rva:
        return {}
    secs = _sec_map(data, pe, nsec, size_opt)
    res_off = _rva2off(res_rva, secs)
    out = {}

    def walk(off, level, path):
        n_named, n_id = struct.unpack('<HH', data[off + 12:off + 16])
        for i in range(n_named + n_id):
            e = off + 16 + i * 8
            name, sub = struct.unpack('<II', data[e:e + 8])
            ident = name & 0x7fffffff
            if sub & 0x80000000:
                walk(res_off + (sub & 0x7fffffff), level + 1, path + [ident])
            else:
                de = res_off + sub          # 第三层才是数据项
                rva, size = struct.unpack('<II', data[de:de + 8])
                fo = _rva2off(rva, secs)
                if fo is not None and len(path) >= 2:
                    out[tuple(path + [ident])] = (data[fo:fo + size], size)

    walk(res_off, 0, [])
    return out


def extract(exe, outdir):
    with open(exe, 'rb') as f:
        data = f.read()
    res = load_resources(data)
    groups = {k: v for k, v in res.items() if k[0] == RT_GROUP_ICON}
    icons = {k: v for k, v in res.items() if k[0] == RT_ICON}
    if not groups:
        return 0, []

    os.makedirs(outdir, exist_ok=True)
    grp_id, (blob, _) = next(iter(groups.items()))
    _, _, count = struct.unpack('<HHH', blob[:6])
    picked, table = [], []
    for i in range(count):
        e = 6 + i * 14
        w, h, _cc, _rs, _pl, _bc, bytelen, nid = struct.unpack('<BBBBHHIH', blob[e:e + 14])
        px = 256 if w == 0 else w
        table.append((px, nid, bytelen))
    table.sort(key=lambda x: -x[0])
    for px, nid, _bl in table:
        for (t, i, lang), (body, size) in icons.items():
            if i == nid:
                ext = 'png' if body[:4] == b'\x89PNG' else 'ico'
                p = os.path.join(outdir, f'exe-icon-{px}x{px}.{ext}')
                with open(p, 'wb') as f:
                    f.write(body)
                picked.append((px, p, ext))
                break
    return count, picked


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    target = sys.argv[1]
    outdir = sys.argv[2] if len(sys.argv) > 2 else 'exe_icons'
    n, picked = extract(target, outdir)
    print(f'{target}: RT_GROUP_ICON 内声明 {n} 个尺寸')
    if not picked:
        print('未提取到图标 —— 该 exe 可能没有嵌入图标资源')
        sys.exit(2)
    for px, p, ext in picked:
        print(f'  {px:>4}x{px:<4} {os.path.getsize(p) / 1024:7.1f} KB  {os.path.basename(p)}  [{ext}]')
