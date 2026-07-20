"""The one place the `.env` file is read.

Every credential the pipeline needs — the Toast API keys, the Postgres
connection string — reaches the code as an environment variable. On a laptop
those come from the `.env` file at the repo root; on a GitHub Actions runner
there is no such file and the same names arrive from repository secrets (see
`.github/workflows/`). `load_env` bridges the two: it loads `.env` into
`os.environ` *without* overriding anything already set, so a real environment
variable always wins and the same code runs in both places. That precedence is
what lets the workflows pass secrets straight through, and it means a missing
`.env` is not an error — only a missing value is.

The file is standard dotenv format (`KEY=value`, one per line, `#` comments),
parsed by python-dotenv rather than by hand. It used to be a JSON-ish blob
followed by `KEY = value` lines, with three modules regex-scraping it
separately; the names also carry a `TOAST_` prefix now, so a stray `URL` in the
ambient environment can no longer shadow the file.
"""
import os
from pathlib import Path
from typing import Mapping, Optional, Type

from dotenv import load_dotenv

ENV_PATH = Path(__file__).parent / ".env"


def load_env(env_path: Optional[Path] = None) -> Mapping[str, str]:
    """Load `.env` into `os.environ` and return the environment. Values already
    present are left alone. A missing file is fine — on a runner every value
    comes from secrets — so this never raises; the readers below are what fail
    loudly on a value that is actually absent.

    `ENV_PATH` is read here rather than bound as a default argument so a test
    can point it somewhere empty and get the no-configuration behaviour, even
    though the developer running it has a real `.env` on disk."""
    load_dotenv(ENV_PATH if env_path is None else env_path, override=False)
    return os.environ


def require(
    name: str,
    environ: Mapping[str, str],
    error: Type[Exception] = RuntimeError,
    hint: str = "",
) -> str:
    """The value of `name`, or raise `error` naming the variable that is missing.
    Callers pass their own exception type so the failure still reads in their
    vocabulary (`ToastAuthError` for a credential, `RuntimeError` for the DB),
    and a `hint` pointing at how to set it."""
    value = environ.get(name)
    if not value:
        raise error(f"{name} is not set. {hint}".rstrip())
    return value


def resolve(environ: Optional[Mapping[str, str]] = None) -> Mapping[str, str]:
    """The environment to read from: the caller's mapping if it passed one — the
    seam the tests use, which touches no global state — otherwise the real
    environment with `.env` loaded into it."""
    return load_env() if environ is None else environ
