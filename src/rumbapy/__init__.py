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
        import importlib as _importlib
        import os as _os
        from . import _buildid
        BUILD_ID = _buildid.compute(__path__[0])
        # Import ansioso (E13) de TODOS os módulos do cliente, na ordem da A.6.
        # TODO(P-G): session, compat, recipes, _bridge, _launcher, _runtime32, _doctor e _legacy só nascem na Onda 2
        # (P-C/P-D/P-E). Até lá, pula APENAS os que ainda não existem como arquivo no pacote; um módulo que existe e
        # falha no import continua indo para o heal. O P-G troca este laço pelas duas linhas literais da A.6.
        for _name in ("errors", "keys", "screen", "waits", "profiles", "session", "compat", "client", "recipes",
                      "_bridge", "_backend", "_launcher", "_runtime32", "_doctor", "_legacy"):
            if (_os.path.isfile(_os.path.join(__path__[0], _name + ".py"))
                    or _os.path.isfile(_os.path.join(__path__[0], _name, "__init__.py"))):
                _importlib.import_module("." + _name, __name__)
        from . import errors
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
    # TODO(P-G): sem os condicionais quando session/_doctor/compat existirem (Onda 2); até lá o RumbaClient
    # exportado é o legado de rumbapy.client (v0.1.5).
    if "session" in globals():
        from .session import open, attach, close  # noqa: F401
    if "_doctor" in globals():
        from ._doctor import doctor  # noqa: F401
    if "compat" in globals():
        from .compat import RumbaClient
    else:
        from .client import RumbaClient
    from .keys import Key
    from .screen import Screen, Field, Cursor, OIA
    from .errors import *                    # noqa: F403  (errors.__all__)
    __all__ = ["RumbaClient", "Key", "Screen", "Field", "Cursor", "OIA",
               *(["doctor"] if "_doctor" in globals() else []), *errors.__all__]
    # open/attach/close ficam FORA de __all__ (I34): use rumbapy.open(...)
