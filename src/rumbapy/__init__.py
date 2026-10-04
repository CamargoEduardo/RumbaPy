"""RumbaPy — automação de terminais 3270 do RUMBA. Leia: help(rumbapy.open)."""
import sys as _sys
try:
    from . import _role
    _ROLE = _role.detect()
    if _ROLE in ("user", "cli"):
        from . import _update
        _update.ensure_latest(context="import")
    from ._version import __version__  # noqa: F401
    if _ROLE in ("user", "cli"):
        from . import _buildid
        BUILD_ID = _buildid.compute(__path__[0])
        from . import errors, keys, screen, waits, profiles, session, compat, client, recipes  # noqa: F401  ansioso (E13)
        from . import _bridge, _backend, _launcher, _runtime32, _doctor, _legacy               # noqa: F401
        if _buildid.compute(__path__[0]) != BUILD_ID:
            raise errors.UpdateRequired("O RumbaPy foi atualizado durante a importação. Rode de novo.")
except BaseException as _e:
    if getattr(type(_e), "_rumbapy_error", False) or isinstance(_e, (SystemExit, KeyboardInterrupt)):
        raise
    _r = globals().get("_ROLE") or ("legacy-shim" if _sys.orig_argv[1:3] == ["-m", "rumbapy.server"] else "user")
    if _r not in ("user", "cli"):           # _role pode ser o módulo quebrado: papel calculado inline
        raise
    from . import _bootstrap
    _bootstrap.heal(_e)                      # sempre levanta (H.6)

if _ROLE in ("user", "cli"):
    from .session import open, attach, close  # noqa: F401
    from ._doctor import doctor
    from .compat import RumbaClient
    from .keys import Key
    from .screen import Screen, Field, Cursor, OIA
    from .errors import *                    # noqa: F403  (errors.__all__)
    __all__ = ["RumbaClient", "Key", "Screen", "Field", "Cursor", "OIA", "doctor", *errors.__all__]
    # open/attach/close ficam FORA de __all__ (I34): use rumbapy.open(...)
