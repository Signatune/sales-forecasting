"""Getting a rendered Scheduled Report to the people who read it: the PDF to
Google Drive, a card to Google Chat (ADR 0010).

    resolve_destination(name, environ)                -> Destination
    report_filename(payload)                          -> str
    upload_pdf(pdf, filename, folder_id, credentials) -> str   # a shareable link
    post_card(payload, link, webhook_url)             -> None
    post_refusal(refusal, webhook_url)                -> None

The shape of this module is forced by one fact, and it is worth stating plainly
so nobody spends an afternoon fighting it: **a Google Chat incoming webhook
cannot upload an attachment.** Attachments need `media.upload` under OAuth *user*
credentials, which a webhook's `key`/`token` authorization cannot reach. Card
images must be HTTPS-hosted PNG or JPG, so the chart cannot be inlined either.
So the PDF goes to Drive under a service account and the webhook posts a card
carrying the numbers and a link. This is a fixed property of webhooks, not a
limitation of the report; a full Chat app would be the only way around it.

Two more properties of the same API shape the code below:

- **Webhooks are limited to one request per second per space.** Reports are
  delivered sequentially with a gap (`WEBHOOK_MIN_INTERVAL_SECONDS`), never
  concurrently — the caller paces them.
- **The service account has no Drive storage of its own.** Every upload must
  name a parent folder that a real account shared with it; the file lives in
  that account's space and inherits its sharing. A file created without a parent
  has nowhere to live, so that is an error here rather than a call to Google.

Destinations are symbolic. A report's `config["delivery"]` names one, and it is
resolved to `CHAT_WEBHOOK_<NAME>` / `DRIVE_FOLDER_<NAME>` through `env.py`. A
Chat webhook URL is a bearer credential and does not belong in Postgres, where
it would end up in backups and table dumps; keeping it in secrets also keeps
rotating it a secrets change. A name that resolves to nothing is a loud error
and never a skipped delivery — a report that silently goes nowhere looks exactly
like one that was never due.
"""
import json
import re
from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Tuple

import env
import report_render
from report_payload import Payload, Refusal

# Google Chat allows one request per second per space. The caller sleeps this
# long between reports rather than delivering them concurrently; a 429 in the
# middle of a Saturday morning would drop a report that had already been
# uploaded, leaving a PDF in Drive that nobody was told about.
WEBHOOK_MIN_INTERVAL_SECONDS = 1.0

# Bounded so a hung webhook cannot hold the whole job open. A delivery that
# times out fails loudly and is re-runnable; one that hangs is invisible.
HTTP_TIMEOUT_SECONDS = 30

PDF_MIME_TYPE = "application/pdf"



@dataclass(frozen=True)
class Destination:
    """Where one report goes, resolved from its symbolic name."""

    name: str
    webhook_url: str
    folder_id: str


def _slug(text: str) -> str:
    """`text` as a filename-safe slug: lower case, runs of anything else
    collapsed to one hyphen."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def secret_names(name: str) -> Tuple[str, str]:
    """The two secret names a destination resolves to: upper-cased, hyphens to
    underscores, so `bagel-team` reads `CHAT_WEBHOOK_BAGEL_TEAM` and
    `DRIVE_FOLDER_BAGEL_TEAM`."""
    key = re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")
    return f"CHAT_WEBHOOK_{key}", f"DRIVE_FOLDER_{key}"


def resolve_destination(
    name: str, environ: Optional[Mapping[str, str]] = None
) -> Destination:
    """The webhook and Drive folder a report's `delivery` name points at.

    Raises, naming the variable that is missing, rather than returning something
    partly filled in: a half-resolved destination would upload a PDF nobody is
    told about, or post a link to a file that was never written. The message
    names the *variable*, never the value — it is printed into an Actions log,
    and a webhook URL is a bearer credential."""
    if not name:
        raise RuntimeError(
            "This report names no delivery destination. Set its `delivery` key "
            "in report_configs.config to a destination name (e.g. "
            "'bagel-team'), which resolves to the CHAT_WEBHOOK_<NAME> and "
            "DRIVE_FOLDER_<NAME> secrets — see docs/reports.md."
        )
    webhook_var, folder_var = secret_names(name)
    environ = env.resolve(environ)
    hint = (
        f"The report's delivery destination is {name!r}, so it needs both "
        f"{webhook_var} and {folder_var} set. See docs/reports.md."
    )
    return Destination(
        name=name,
        webhook_url=env.require(webhook_var, environ, RuntimeError, hint),
        folder_id=env.require(folder_var, environ, RuntimeError, hint),
    )


def report_filename(payload: Payload) -> str:
    """`<report-slug>-<window-start>.pdf` — one file per Report Window.

    Keyed by the window's first day rather than by the day it was generated, so
    the same window re-run by hand overwrites nothing and lands on the same
    name, and two different windows can never collide. Files are never
    overwritten (ADR 0010): an old Chat link must still open the week it
    announced."""
    return f"{_slug(payload.report_name)}-{payload.window[0].isoformat()}.pdf"


# --- Drive -----------------------------------------------------------------

SERVICE_ACCOUNT_ENV_VAR = "GOOGLE_SERVICE_ACCOUNT_JSON"

# Per-file access: the token can touch the files this job created and nothing
# else in the folder it was given. A shared *Drive* (rather than a folder shared
# out of someone's My Drive) may need the broader
# `https://www.googleapis.com/auth/drive` — that is the one line to change, and
# docs/reports.md says so.
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def drive_credentials(environ: Optional[Mapping[str, str]] = None):
    """Service-account credentials for the Drive upload, built from the
    `GOOGLE_SERVICE_ACCOUNT_JSON` secret.

    The variable holds the whole key file as JSON — one secret rather than a
    path, because a runner has no file to point at. The environment is read and
    the JSON parsed *before* google-auth is imported, so both of the ways this
    is normally misconfigured fail with a message about configuration on an
    install that does not have the `report` extra at all."""
    raw = env.require(
        SERVICE_ACCOUNT_ENV_VAR,
        env.resolve(environ),
        RuntimeError,
        "It holds the whole Drive service-account key file as JSON. See "
        "docs/reports.md for creating the account and sharing a folder to it.",
    )
    try:
        info = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{SERVICE_ACCOUNT_ENV_VAR} is not valid JSON ({exc}). Paste the "
            "service-account key file verbatim, including its braces — see "
            "docs/reports.md."
        ) from exc

    from google.oauth2 import service_account

    return service_account.Credentials.from_service_account_info(
        info, scopes=DRIVE_SCOPES
    )


class DriveClient:
    """The one Drive call this feature makes, and no others.

    A narrow object rather than a `googleapiclient` service passed around: it is
    what makes `upload_pdf` testable without Google, and it keeps the discovery
    import lazy so a `dev`-only install can import this module."""

    def __init__(self, credentials):
        self._credentials = credentials
        self._service = None

    def _drive(self):
        if self._service is None:
            from googleapiclient.discovery import build

            self._service = build(
                "drive", "v3", credentials=self._credentials, cache_discovery=False
            )
        return self._service

    def create(self, filename: str, folder_id: str, pdf: bytes) -> str:
        """Create one file under `folder_id` and return its shareable link.
        The file inherits the parent folder's sharing, which is what makes the
        link openable by the people the folder was shared with."""
        import io

        from googleapiclient.http import MediaIoBaseUpload

        media = MediaIoBaseUpload(
            io.BytesIO(pdf), mimetype=PDF_MIME_TYPE, resumable=False
        )
        created = (
            self._drive()
            .files()
            .create(
                body={"name": filename, "parents": [folder_id]},
                media_body=media,
                fields="id, webViewLink",
                supportsAllDrives=True,
            )
            .execute()
        )
        return created["webViewLink"]


def upload_pdf(
    pdf: bytes,
    filename: str,
    folder_id: str,
    credentials,
    *,
    client: Optional[DriveClient] = None,
) -> str:
    """Upload `pdf` into `folder_id` and return a shareable link.

    `client` is the seam the tests drive, the way `daily_forecast.main` takes
    `connect` and `load_sales`; left out, it is built from `credentials`.

    A missing folder raises before anything is sent. The service account has no
    storage of its own, so a parentless create would either fail obscurely
    against a quota that does not exist or land the file somewhere nobody
    shares — and the Chat card would then link to a file its readers cannot
    open."""
    if not folder_id:
        raise RuntimeError(
            "A Drive upload must name a parent folder: the service account has "
            "no storage of its own, so the file has nowhere to live. Set "
            "DRIVE_FOLDER_<NAME> to a folder shared with the service account — "
            "see docs/reports.md."
        )
    client = client if client is not None else DriveClient(credentials)
    return client.create(filename, folder_id, pdf)


# --- Google Chat -----------------------------------------------------------


def _default_post(url: str, json: Optional[Dict] = None, timeout: Optional[int] = None):
    import requests

    return requests.post(url, json=json, timeout=timeout)


def _send(webhook_url: str, body: Dict, post, what: str) -> None:
    response = post(webhook_url, json=body, timeout=HTTP_TIMEOUT_SECONDS)
    status = getattr(response, "status_code", None)
    if status is None or not 200 <= status < 300:
        # The URL is a bearer credential, so the failure names the destination's
        # status and Google's reply and never the endpoint it was sent to.
        raise RuntimeError(
            f"Google Chat rejected the {what} with status {status}: "
            f"{getattr(response, 'text', '')!r}"
        )


def _window_move(payload: Payload) -> str:
    """How the window compares with typical, phrased exactly as the PDF phrases
    it — `report_render` owns the wording so the card and the page cannot come
    to disagree about the same number."""
    page = payload.headline
    return f"{report_render.move_phrase(page.direction, page.delta_pct)} on typical"


def build_card(payload: Payload, link: str) -> Dict:
    """The `cardsV2` message body — the **headline model only** (ADR 0011).

    A PDF has a page 1 and a Chat message has to quote a number, so the card
    answers "how many on Saturday?" with one model's figures. Every other
    qualifying model still has a full page in the PDF the button links to;
    flipping pages is the model toggle, because a webhook can carry no
    interaction at all.

    When the report's named headline model did not cover the window, the page
    that leads is a *substitute* — and the card says so. Quoting one model's
    numbers under a card that looks like every other week, without mentioning
    that the model changed, is the same class of silent substitution ADR 0010
    refuses a stale window for.

    No attachment and no chart image, deliberately — see the module docstring."""
    page = payload.headline
    window = f"{payload.window[0].isoformat()} to {payload.window[-1].isoformat()}"
    busiest, quietest = page.busiest, page.quietest
    substitution = (
        []
        if payload.headline_is_the_named_model
        else [
            {
                "textParagraph": {
                    "text": (
                        f"These are <b>{page.model}</b>'s numbers: "
                        f"{payload.headline_model}, this report's headline model, "
                        "did not cover the whole window. The report names why."
                    )
                }
            }
        ]
    )
    return {
        "cardsV2": [
            {
                "cardId": f"{_slug(payload.report_name)}-{payload.window[0].isoformat()}",
                "card": {
                    "header": {
                        "title": payload.report_name,
                        "subtitle": f"{window} · {page.model}",
                    },
                    "sections": [
                        {
                            "widgets": substitution
                            + [
                                {
                                    "decoratedText": {
                                        "topLabel": "Expected Demand across the window",
                                        "text": (
                                            f"<b>{report_render.quantity(page.total)}</b> "
                                            f"{payload.target}"
                                        ),
                                        "bottomLabel": _window_move(payload),
                                    }
                                },
                                {
                                    "decoratedText": {
                                        "topLabel": "Busiest day",
                                        "text": (
                                            f"{report_render.long_date(busiest.date)} — "
                                            f"<b>{report_render.quantity(busiest.forecast)}</b>"
                                        ),
                                    }
                                },
                                {
                                    "decoratedText": {
                                        "topLabel": "Quietest day",
                                        "text": (
                                            f"{report_render.long_date(quietest.date)} — "
                                            f"<b>{report_render.quantity(quietest.forecast)}</b>"
                                        ),
                                    }
                                },
                                {
                                    "textParagraph": {
                                        "text": (
                                            f"Forecast Origin {payload.origin.isoformat()} "
                                            f"· forecast configuration version "
                                            f"{payload.config_version}"
                                        )
                                    }
                                },
                                {"textParagraph": {"text": report_render.NOT_BAKE_QUANTITIES}},
                                {
                                    "buttonList": {
                                        "buttons": [
                                            {
                                                "text": "Open the full report",
                                                "onClick": {"openLink": {"url": link}},
                                            }
                                        ]
                                    }
                                },
                            ]
                        }
                    ],
                },
            }
        ]
    }


def post_card(payload: Payload, link: str, webhook_url: str, *, post=None) -> None:
    """Post the report's card to its Chat space. `post` is the HTTP seam."""
    _send(webhook_url, build_card(payload, link), post or _default_post, "report card")


def post_refusal(refusal: Refusal, webhook_url: str, *, post=None) -> None:
    """Post a refusal as **plain text, not a card**.

    A card is the wrong shape for this: it is read when something is already
    broken, often on a phone, and a text message renders the same way in every
    client and every notification preview. The message is the one the payload
    builder wrote — it already names which report, which failure, and the fix."""
    body = {"text": f"⚠️ {refusal.report_name} was not delivered.\n\n{refusal.message}"}
    _send(webhook_url, body, post or _default_post, "refusal")
