"""RumbaPy — automação de terminais 3270 do RUMBA. Leia: help(rumbapy.open)."""
import sys as _sys

# arquivos que o cliente não importa mas precisam estar íntegros (A.14): compilados (sem executar) dentro do try, para
# que um __main__/_worker quebrado ou ausente também vá ao heal. = _worker.main.WORKER_MODULES (teste confere)
_WORKER_MODULES = ("guard", "structs", "rc", "mask", "hllapi", "engine", "dispatch", "watchdog", "server", "main")


def _check_sources(pkgdir, names):
    """Compila (via ``.pyc`` em cache, sem executar) ``rumbapy.<nome>``; arquivo ausente/quebrado levanta."""
    import importlib.machinery as _machinery
    for _name in names:
        _path = pkgdir + "/" + _name.replace(".", "/") + ".py"
        _machinery.SourceFileLoader(__name__ + "." + _name, _path).get_code(__name__ + "." + _name)


def _purge_modules():
    """Tira ``rumbapy.*`` de ``sys.modules``: o próximo import no mesmo processo recarrega tudo do disco, nunca uma
    mistura de módulos velhos (em memória) e novos (R11/E13, A.14)."""
    for _name in [n for n in _sys.modules if n.startswith(__name__ + ".")]:
        del _sys.modules[_name]


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
        _check_sources(__path__[0], ["_worker." + _m for _m in _WORKER_MODULES] + (["__main__"] if _ROLE == "cli"
                                                                                      else []))
        if _buildid.compute(__path__[0]) != BUILD_ID:
            _purge_modules()
            raise errors.UpdateRequired("O RumbaPy foi atualizado durante a importação. Rode de novo.")
        from . import _log
        try:
            _log.setup_client_logging()          # logs\client-AAAAMMDD.log (D.7/A.5), uma vez por processo
        except OSError:
            pass                                 # pasta de dados sem escrita: segue sem o arquivo de log
except BaseException as _e:
    if getattr(type(_e), "_rumbapy_error", False) or isinstance(_e, (SystemExit, KeyboardInterrupt)):
        raise
    _r = globals().get("_ROLE") or ("legacy-shim" if _sys.orig_argv[1:3] == ["-m", "rumbapy.server"] else "user")
    if _r not in ("user", "cli"):           # _role pode ser o módulo quebrado: papel calculado inline
        raise
    _purge_modules()                         # depois do heal, um 2º import no processo recarrega tudo do disco
    from . import _bootstrap                 # (só stdlib) carregado de novo e fora do sys.modules: o próximo import
    _sys.modules.pop(__name__ + "._bootstrap", None)   # pega o reinstalado
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
