"""Parser PE mínimo (só stdlib): exports, imports e strings de versão de DLLs/EXEs.

    python tools\\research\\pe_exports.py <modo> <arquivo> [<arquivo> ...]

<modo> combina letras: v = strings de versão, e = exports, i = imports. Ex.:
    python tools\\research\\pe_exports.py ve "C:\\Program Files (x86)\\Micro Focus\\RUMBA\\System\\ehlapi32.Dll"

A primeira linha de cada arquivo mostra machine (0x14c = x86), bitness, se é DLL e se é .NET (clr).
"""
import struct, sys, os

def rva_to_off(sections, rva):
    for (va, vsize, raw, rawsize) in sections:
        if va <= rva < va + max(vsize, rawsize):
            return rva - va + raw
    return None

def parse(path):
    data = open(path, 'rb').read()
    if data[:2] != b'MZ':
        return None
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    if data[pe:pe+4] != b'PE\0\0':
        return None
    machine, nsec = struct.unpack_from('<HH', data, pe+4)
    opt_size = struct.unpack_from('<H', data, pe+20)[0]
    chars = struct.unpack_from('<H', data, pe+22)[0]
    opt = pe + 24
    magic = struct.unpack_from('<H', data, opt)[0]
    is64 = magic == 0x20b
    dd_off = opt + (112 if is64 else 96)
    ndd = struct.unpack_from('<I', data, dd_off - 4)[0]
    dds = [struct.unpack_from('<II', data, dd_off + 8*i) for i in range(min(ndd, 16))]
    sec_off = opt + opt_size
    sections = []
    for i in range(nsec):
        name = data[sec_off+40*i:sec_off+40*i+8].rstrip(b'\0')
        vsize, va, rawsize, raw = struct.unpack_from('<IIII', data, sec_off+40*i+8)
        sections.append((va, vsize, raw, rawsize))
    res = {'machine': hex(machine), 'is64': is64, 'dll': bool(chars & 0x2000), 'exports': [], 'imports': {}, 'clr': dds[14][0] != 0 if len(dds) > 14 else False}
    # exports
    erva, esize = dds[0]
    if erva:
        eo = rva_to_off(sections, erva)
        (_, _, _, _, name_rva, base, nfunc, nnames, funcs_rva, names_rva, ords_rva) = struct.unpack_from('<IIHHIIIIIII', data, eo)
        fo = rva_to_off(sections, funcs_rva); no = rva_to_off(sections, names_rva); oo = rva_to_off(sections, ords_rva)
        named = {}
        for i in range(nnames):
            nrva = struct.unpack_from('<I', data, no + 4*i)[0]
            ordi = struct.unpack_from('<H', data, oo + 2*i)[0]
            n_off = rva_to_off(sections, nrva)
            nm = data[n_off:data.index(b'\0', n_off)].decode('latin1')
            named[ordi] = nm
        for i in range(nfunc):
            frva = struct.unpack_from('<I', data, fo + 4*i)[0]
            if frva == 0: continue
            fwd = None
            if erva <= frva < erva + esize:
                f_off = rva_to_off(sections, frva)
                fwd = data[f_off:data.index(b'\0', f_off)].decode('latin1')
            res['exports'].append((base + i, named.get(i), hex(frva), fwd))
    # imports
    irva, isize = dds[1]
    if irva:
        io = rva_to_off(sections, irva)
        while True:
            oft, ts, fc, nrva, ft = struct.unpack_from('<IIIII', data, io)
            if nrva == 0: break
            n_off = rva_to_off(sections, nrva)
            dllname = data[n_off:data.index(b'\0', n_off)].decode('latin1')
            funcs = []
            thunk = rva_to_off(sections, oft or ft)
            step = 8 if is64 else 4
            while thunk is not None:
                v = struct.unpack_from('<Q' if is64 else '<I', data, thunk)[0]
                if v == 0: break
                if v & (1 << (63 if is64 else 31)):
                    funcs.append(f'#{v & 0xffff}')
                else:
                    ho = rva_to_off(sections, v & 0x7fffffff)
                    funcs.append(data[ho+2:data.index(b'\0', ho+2)].decode('latin1'))
                thunk += step
            res['imports'][dllname] = funcs
            io += 20
    return res

def version_strings(path):
    """Extract StringFileInfo key/value pairs from VS_VERSION_INFO (UTF-16)."""
    data = open(path, 'rb').read()
    out = {}
    keys = ['CompanyName','FileDescription','FileVersion','InternalName','LegalCopyright','OriginalFilename','ProductName','ProductVersion','Comments']
    for k in keys:
        kb = k.encode('utf-16le') + b'\0\0'
        i = data.find(kb)
        if i < 0: continue
        j = i + len(kb)
        while j % 4: j += 1
        end = data.find(b'\0\0', j)
        while end != -1 and (end - j) % 2: end = data.find(b'\0\0', end+1)
        try:
            out[k] = data[j:end].decode('utf-16le')
        except Exception:
            pass
    return out

if __name__ == '__main__':
    mode = sys.argv[1]
    for p in sys.argv[2:]:
        r = parse(p)
        if r is None:
            print(f'### {p}: not PE'); continue
        print(f"### {os.path.basename(p)} machine={r['machine']} 64={r['is64']} dll={r['dll']} clr={r['clr']} nexp={len(r['exports'])}")
        if 'v' in mode:
            for k, v in version_strings(p).items(): print(f'   {k}: {v}')
        if 'e' in mode:
            for o, n, rva, fwd in r['exports']:
                print(f'   @{o:<4} {n or "(noname)"} {rva}' + (f' -> {fwd}' if fwd else ''))
        if 'i' in mode:
            for d, fs in r['imports'].items():
                print(f'   IMPORT {d}: {", ".join(fs)}')
