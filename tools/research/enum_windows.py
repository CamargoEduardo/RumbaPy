"""Lista recursivamente as janelas (inclusive filhas) que pertencem a um PID. Qualquer Python.

    python tools\\research\\enum_windows.py <pid> [<pid> ...]

Útil para ver a hierarquia do emulador dentro do IE / modo IE do Edge, por exemplo:
  TabWindowClass "… - BRAZIL_D - Pro Client …"
    Shell DocObject View > Internet Explorer_Server
      AfxOleControl140 (ProContainer.ocx) > AfxWnd140 "CContainer2Ctrl - Control Container"
        AfxFrameOrView140 "Display Frame" > … > AfxOleControl140 (WdMfDsp.ocx) + filhos ocultos
"""
import ctypes.wintypes as wt
import ctypes
import sys

# WinDLL privado (não o ctypes.windll compartilhado); títulos por InternalGetWindowText, que NÃO manda WM_GETTEXT à
# janela de outro processo (GetWindowTextW manda e trava numa janela pendurada, A.10 l): a sonda não envia mensagens
user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.InternalGetWindowText.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
ENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
GA_PARENT = 1


def describe(hwnd):
    pid = wt.DWORD()
    tid = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    cls = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cls, 256)
    title = ctypes.create_unicode_buffer(512)
    user32.InternalGetWindowText(hwnd, title, 512)
    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    size = (rect.right - rect.left, rect.bottom - rect.top)
    return pid.value, tid, cls.value, title.value, bool(user32.IsWindowVisible(hwnd)), size


def children(hwnd):
    found = []

    def callback(child, _):
        if user32.GetAncestor(child, GA_PARENT) == hwnd:
            found.append(child)
        return True

    user32.EnumChildWindows(hwnd, ENUMPROC(callback), 0)
    return found


def dump(hwnd, pids, depth=0):
    pid, tid, cls, title, visible, size = describe(hwnd)
    shown = pid in pids
    if shown:
        print("  " * depth + f"{hwnd:#010x} pid={pid} tid={tid} [{cls}] {title[:80]!r} vis={visible} size={size}")
    for child in children(hwnd):
        dump(child, pids, depth + (1 if shown else 0))


def main():
    pids = {int(p) for p in sys.argv[1:]}
    tops = []
    user32.EnumWindows(ENUMPROC(lambda h, _: tops.append(h) or True), 0)
    for hwnd in tops:
        dump(hwnd, pids)


if __name__ == "__main__":
    main()
