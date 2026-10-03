"""Disassembler minimalista para DLLs x86 do RUMBA (pesquisa/engenharia reversa).

Requer: pip install pefile capstone   (use um venv temporário; NÃO faz parte do pacote rumbapy)

Uso:
    python rdis.py <dll> <export|endereco_hex> [...]          # desmonta até o primeiro ret
    python rdis.py --range <dll> <inicio_hex> <fim_hex>       # varredura linear de um intervalo
    python rdis.py --xref <dll> <substring>                   # linhas que citam um import/string

As instruções são anotadas com imports (DLL!Função), exports e strings ASCII/UTF-16 referenciadas.
Nota: o nome do arquivo não pode ser "dis.py" (sombrearia o módulo `dis` da stdlib usado pelo capstone).
"""
import sys

import capstone
import pefile
from capstone import x86


class Dis:
    def __init__(self, path):
        self.pe = pefile.PE(path)
        self.base = self.pe.OPTIONAL_HEADER.ImageBase
        self.md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        self.md.detail = True
        self.iat = {}
        for entry in getattr(self.pe, 'DIRECTORY_ENTRY_IMPORT', []):
            for imp in entry.imports:
                name = imp.name.decode() if imp.name else f'#{imp.ordinal}'
                self.iat[imp.address] = f'{entry.dll.decode()}!{name}'
        self.exports = {}
        self.exp_by_name = {}
        if hasattr(self.pe, 'DIRECTORY_ENTRY_EXPORT'):
            for sym in self.pe.DIRECTORY_ENTRY_EXPORT.symbols:
                name = sym.name.decode() if sym.name else f'ord{sym.ordinal}'
                self.exports[self.base + sym.address] = name
                self.exp_by_name[name] = self.base + sym.address
        self.data = self.pe.get_memory_mapped_image()

    def read(self, va, n):
        off = va - self.base
        return self.data[off:off + n]

    def cstr(self, va, maxlen=200):
        try:
            raw = self.read(va, maxlen)
            end = raw.find(b'\0')
            if end >= 2 and all(32 <= c < 127 or c in (9, 10, 13) for c in raw[:end]):
                return raw[:end].decode()
            raw = self.read(va, maxlen * 2)
            end = raw.find(b'\0\0')
            while end > 0 and end % 2:
                end = raw.find(b'\0\0', end + 1)
            if end >= 4:
                text = raw[:end].decode('utf-16le')
                if all(32 <= ord(c) < 127 for c in text):
                    return 'L"' + text + '"'
        except Exception:
            pass
        return None

    def annotate(self, ins):
        notes = []
        for op in ins.operands:
            value = None
            if op.type == x86.X86_OP_IMM:
                value = op.imm & 0xffffffff
            elif op.type == x86.X86_OP_MEM and op.mem.base == 0 and op.mem.index == 0:
                value = op.mem.disp & 0xffffffff
            if value is None:
                continue
            if value in self.iat:
                notes.append(self.iat[value])
            elif value in self.exports:
                notes.append('EXPORT ' + self.exports[value])
            else:
                text = self.cstr(value)
                if text:
                    notes.append(repr(text))
        return notes

    def fmt(self, ins):
        notes = self.annotate(ins)
        line = f'{ins.address:08x}: {ins.mnemonic:6} {ins.op_str}'
        return line + ('   ; ' + ' | '.join(notes) if notes else '')

    def disasm(self, va, maxins=400, out=print):
        """Desmonta a partir de `va` até ret / jmp indireto / maxins."""
        for n, ins in enumerate(self.md.disasm(self.read(va, 0x4000), va)):
            out(self.fmt(ins))
            if ins.mnemonic in ('ret', 'retn'):
                break
            if ins.mnemonic == 'jmp' and ins.operands[0].type == x86.X86_OP_MEM:
                break
            if n >= maxins:
                break

    def disrange(self, start, end, out=print):
        """Varredura linear de [start, end); pula bytes não decodificáveis (jump tables etc.)."""
        code = self.read(start, end - start)
        off = 0
        while off < len(code):
            decoded = False
            for ins in self.md.disasm(code[off:], start + off):
                name = self.exports.get(ins.address)
                if name:
                    out(f'---- {name}')
                out(self.fmt(ins))
                off = ins.address + ins.size - start
                decoded = True
            if not decoded:
                out(f'{start + off:08x}: db {code[off]:02x}')
                off += 1

    def text_section(self):
        sec = next(s for s in self.pe.sections if s.Name.startswith(b'.text'))
        start = self.base + sec.VirtualAddress
        return start, start + sec.Misc_VirtualSize


def main(argv):
    if argv and argv[0] == '--range':
        d = Dis(argv[1])
        d.disrange(int(argv[2], 16), int(argv[3], 16))
    elif argv and argv[0] == '--xref':
        d = Dis(argv[1])
        lines = []
        d.disrange(*d.text_section(), out=lines.append)
        for i, line in enumerate(lines):
            if argv[2] in line:
                print('\n'.join(lines[max(0, i - 4):i + 1]))
                print('   ---')
    else:
        d = Dis(argv[0])
        for target in argv[1:]:
            va = d.exp_by_name.get(target) or int(target, 16)
            print(f'==== {target} @ {va:08x}')
            d.disasm(va)
            print()


if __name__ == '__main__':
    main(sys.argv[1:])
