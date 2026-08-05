#!/usr/bin/env python
"""Django management entrypoint for ASHOS."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

#: Set on the child after a hand-over, so the child never hands over again. Without
#: it a virtualenv whose python resolves to a different path than we compared
#: against would exec itself forever.
_HANDOVER_FLAG = "ASHOS_MANAGE_VENV"


def _venv_python() -> Path | None:
    """This project's own interpreter, if it has been created."""
    root = Path(__file__).resolve().parent
    if os.name == "nt":
        candidate = root / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = root / ".venv" / "bin" / "python"
    return candidate if candidate.exists() else None


def _use_the_project_interpreter() -> None:
    """Re-run this command under ``.venv`` when it was started with another python.

    ``py manage.py runserver`` is what a Django developer types, and on Windows
    ``py`` is the system launcher: it finds the global interpreter, which has none
    of this project's dependencies, and the first thing that fails is
    ``import environ`` — an error that names a package rather than the actual
    problem, which is the interpreter. Every answer to it is either wrong (install
    Django globally, and now there are two dependency sets to keep in step) or a
    thing to remember on every terminal (activate first).

    So the entrypoint fixes it instead of reporting it. The dependencies are not
    optional and there is exactly one interpreter that has them; which python typed
    the command is not information worth acting on.

    Either way the command runs under the virtualenv's python, so runserver's
    autoreloader respawns from the right interpreter — it uses ``sys.executable``,
    which in the process doing the work is now the virtualenv's.

    A missing ``.venv`` is left alone: somebody running in Docker, in CI or in an
    already-activated environment is on the right interpreter by other means, and
    the ImportError below still explains it if they are not.
    """
    if os.environ.get(_HANDOVER_FLAG) == "1":
        return

    venv = _venv_python()
    if venv is None:
        return

    current = Path(sys.executable)
    try:
        if current.samefile(venv):
            return
    except OSError:  # pragma: no cover - a path that cannot be stat'ed is not ours
        if current.resolve() == venv.resolve():
            return

    os.environ[_HANDOVER_FLAG] = "1"
    # One line, to stderr, only when it actually switches. A command that silently
    # runs on a different interpreter than the one named is a debugging session
    # waiting to happen the first time the two disagree.
    sys.stderr.write(f"manage.py: using {venv}\n")

    # Not untrusted input, which is what S603/S606 are about: the executable is a
    # path this file computed from its own location and then stat'ed, and the
    # arguments are the ones the developer typed at their own shell.
    argv = [str(venv), *sys.argv]

    if os.name != "nt":
        # The process is REPLACED, so signals, the exit code and job control all
        # behave as if the right python had been used in the first place.
        os.execv(str(venv), argv)  # noqa: S606
        return  # pragma: no cover - execv does not return

    # Windows has no execve. os.execv() there hands the argument LIST to the
    # spawn family, which flattens it into one command line without quoting — so
    # "E:\Python Project\..." arrives as two arguments and the child tries to open
    # a path that does not exist. This project lives in a directory with a space in
    # it, and so does most of Windows.
    #
    # subprocess quotes properly. The cost is a parent process that does nothing but
    # wait: Ctrl-C reaches both (the console sends it to the whole group), so the
    # child shuts runserver down and the wrapper passes its code up rather than
    # printing a traceback over the top of it.
    try:
        finished = subprocess.run(argv, check=False)  # noqa: S603
    except KeyboardInterrupt:  # pragma: no cover - interactive
        raise SystemExit(130) from None
    raise SystemExit(finished.returncode)


def main() -> None:
    # Before anything is imported: nothing below is installed on a global python.
    _use_the_project_interpreter()

    # Load .env early so DJANGO_SETTINGS_MODULE itself can come from the file.
    env_file = Path(__file__).resolve().parent / ".env"
    if env_file.exists():
        import environ

        environ.Env.read_env(str(env_file))

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "Django is not importable. There is no .venv in this project to hand "
            "this command over to, so create one and install into it: `make setup`, "
            "or `python -m venv .venv` then "
            "`.venv/Scripts/python -m pip install -r requirements/dev.txt`."
        ) from exc

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
