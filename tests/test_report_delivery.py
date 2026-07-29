"""Delivering a Scheduled Report (ticket 04 of scheduled-forecast-reports).

The Drive client and the HTTP post are passed in as seams, the pattern
`daily_forecast.main` uses for `connect` and `load_sales` --- so nothing here
contacts Google, and the assertions are about what *would* be sent.
"""
import datetime as dt

import pytest

import report_delivery
import report_payload
from report_payload import Day, Page, Payload, Refusal

ORIGIN = dt.date(2026, 8, 1)
WINDOW = [ORIGIN + dt.timedelta(days=lead) for lead in range(1, 8)]
VARIETIES = ["everything", "plain", "sesame"]


def day(date, forecast):
    direction, delta_pct = report_payload.direction_against(forecast, 380.0)
    return Day(
        date=date,
        forecast=forecast,
        varieties={name: forecast / 4 for name in VARIETIES},
        baseline=380.0,
        direction=direction,
        delta_pct=delta_pct,
    )


def page(model="ewma", base=400.0):
    # A distinct busiest and quietest day, so the card's choice of each is not
    # satisfied by an accident of ordering.
    forecasts = [base, base + 111, base + 22, base + 33, base + 44, base + 55, base - 66]
    days = [day(date, value) for date, value in zip(WINDOW, forecasts)]
    return Page(model=model, days=days, direction="up", delta_pct=5.3)


def payload(pages=None, window=None, headline_model="ewma"):
    window = window or WINDOW
    return Payload(
        report_name="Bagel forecast",
        target="wheat_bagels",
        varieties=VARIETIES,
        headline_model=headline_model,
        origin=window[0] - dt.timedelta(days=1),
        config_version=4,
        window=window,
        pages=pages or [page()],
    )


def refusal(reason="stale_origin"):
    return Refusal(
        report_name="Bagel forecast",
        reason=reason,
        message=(
            "Bagel forecast needs a Forecast Origin of 2026-08-01 under "
            "configuration version 4, and the newest is 2026-07-31. Today's "
            "forecast run did not complete — re-run the 'Daily Demand Forecast' "
            "workflow, then re-run 'Scheduled Forecast Reports'."
        ),
    )


class FakeDrive:
    """Records what would have been uploaded."""

    def __init__(self, link="https://drive.google.com/file/d/abc/view"):
        self.link = link
        self.calls = []

    def create(self, filename, folder_id, pdf):
        self.calls.append({"filename": filename, "folder_id": folder_id, "pdf": pdf})
        return self.link


class FakePost:
    """Records what would have been POSTed, and answers 200."""

    def __init__(self, status_code=200, text="ok"):
        self.status_code = status_code
        self.text = text
        self.calls = []

    def __call__(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        return self

    @property
    def sent(self):
        return self.calls[-1]["json"]


class TestResolvingADestination:
    def test_it_reads_the_upper_cased_secret_names(self):
        destination = report_delivery.resolve_destination(
            "bagel-team",
            {
                "CHAT_WEBHOOK_BAGEL_TEAM": "https://chat.example/hook",
                "DRIVE_FOLDER_BAGEL_TEAM": "folder-1",
            },
        )

        assert destination.webhook_url == "https://chat.example/hook"
        assert destination.folder_id == "folder-1"

    def test_a_missing_webhook_raises_naming_the_variable(self):
        # A report that silently goes nowhere looks exactly like one that was
        # never due (ADR 0010).
        with pytest.raises(RuntimeError, match="CHAT_WEBHOOK_BAGEL_TEAM"):
            report_delivery.resolve_destination(
                "bagel-team", {"DRIVE_FOLDER_BAGEL_TEAM": "folder-1"}
            )

    def test_a_missing_folder_raises_naming_the_variable(self):
        with pytest.raises(RuntimeError, match="DRIVE_FOLDER_BAGEL_TEAM"):
            report_delivery.resolve_destination(
                "bagel-team", {"CHAT_WEBHOOK_BAGEL_TEAM": "https://chat.example/hook"}
            )

    def test_an_empty_destination_name_raises(self):
        with pytest.raises(RuntimeError, match="delivery"):
            report_delivery.resolve_destination("", {})

    def test_the_webhook_url_never_appears_in_the_error(self):
        # The message is printed into an Actions log; a bearer credential must
        # not travel with it.
        with pytest.raises(RuntimeError) as raised:
            report_delivery.resolve_destination(
                "bagel-team", {"CHAT_WEBHOOK_BAGEL_TEAM": "https://chat.example/secret"}
            )
        assert "secret" not in str(raised.value)


class TestDriveCredentials:
    def test_a_missing_key_raises_naming_the_variable(self):
        with pytest.raises(RuntimeError, match="GOOGLE_SERVICE_ACCOUNT_JSON"):
            report_delivery.drive_credentials({})

    def test_a_key_that_is_not_json_says_so(self):
        # Both misconfigurations are caught before google-auth is imported, so
        # this reads as configuration even on a `dev`-only install.
        with pytest.raises(RuntimeError, match="not valid JSON"):
            report_delivery.drive_credentials(
                {"GOOGLE_SERVICE_ACCOUNT_JSON": "/path/to/key.json"}
            )


class TestTheFilename:
    def test_it_derives_from_the_report_and_the_window_start(self):
        assert (
            report_delivery.report_filename(payload())
            == "bagel-forecast-2026-08-02.pdf"
        )

    def test_two_windows_never_collide(self):
        later = [date + dt.timedelta(days=7) for date in WINDOW]
        assert report_delivery.report_filename(payload()) != (
            report_delivery.report_filename(payload(window=later))
        )

    def test_a_name_with_punctuation_becomes_one_slug(self):
        report = payload()
        report = Payload(**{**report.__dict__, "report_name": "Bagel  Forecast (v2)!"})
        assert report_delivery.report_filename(report).startswith("bagel-forecast-v2-")


class TestUploadingToDrive:
    def test_it_always_names_a_parent_folder(self):
        # The service account has no storage of its own, so a file without a
        # parent has nowhere to live (ADR 0010).
        drive = FakeDrive()
        report_delivery.upload_pdf(b"%PDF-x", "a.pdf", "folder-1", None, client=drive)

        assert drive.calls[0]["folder_id"] == "folder-1"

    def test_a_missing_folder_raises_rather_than_uploading(self):
        drive = FakeDrive()
        with pytest.raises(RuntimeError, match="parent folder"):
            report_delivery.upload_pdf(b"%PDF-x", "a.pdf", "", None, client=drive)
        assert drive.calls == []

    def test_it_returns_the_shareable_link(self):
        drive = FakeDrive(link="https://drive.google.com/file/d/xyz/view")
        link = report_delivery.upload_pdf(
            b"%PDF-x", "a.pdf", "folder-1", None, client=drive
        )
        assert link == "https://drive.google.com/file/d/xyz/view"

    def test_it_uploads_the_bytes_it_was_given(self):
        drive = FakeDrive()
        report_delivery.upload_pdf(b"%PDF-1.7 body", "a.pdf", "f", None, client=drive)
        assert drive.calls[0]["pdf"] == b"%PDF-1.7 body"


def card_text(sent):
    """Every string in the posted card, flattened --- so an assertion about
    what the card says cannot miss it for sitting in a different widget."""
    found = []

    def walk(node):
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, str):
            found.append(node)

    walk(sent)
    return " | ".join(found)


class TestTheChatCard:
    def test_it_quotes_the_headline_models_numbers(self):
        post = FakePost()
        report = payload()
        report_delivery.post_card(report, "https://link", "https://hook", post=post)
        text = card_text(post.sent)

        assert "Bagel forecast" in text
        assert f"{report.headline.total:,.0f}" in text
        assert "2026-08-02" in text and "2026-08-08" in text
        assert "ewma" in text

    def test_it_names_the_busiest_and_quietest_days(self):
        post = FakePost()
        report = payload()
        report_delivery.post_card(report, "https://link", "https://hook", post=post)
        text = card_text(post.sent)

        assert report.headline.busiest.date.strftime("%a") in text
        assert f"{report.headline.busiest.forecast:,.0f}" in text
        assert f"{report.headline.quietest.forecast:,.0f}" in text

    def test_it_quotes_no_other_models_numbers(self):
        # ADR 0011: the card quotes the headline model only.
        post = FakePost()
        other = page(model="holt_winters", base=900.0)
        report_delivery.post_card(
            payload(pages=[page(), other]), "https://link", "https://hook", post=post
        )
        text = card_text(post.sent)

        assert "holt_winters" not in text
        assert f"{other.total:,.0f}" not in text

    def test_it_carries_a_button_linking_to_the_pdf(self):
        # Incoming webhooks cannot upload media and card images must be
        # HTTPS-hosted, so the card carries numbers and a link (ADR 0010).
        post = FakePost()
        report_delivery.post_card(
            payload(), "https://drive/x", "https://hook", post=post
        )

        assert "https://drive/x" in card_text(post.sent)

    def test_it_attaches_nothing_and_inlines_no_image(self):
        post = FakePost()
        report_delivery.post_card(payload(), "https://link", "https://hook", post=post)
        sent = post.sent

        assert "attachment" not in card_text(sent).lower()
        assert "image" not in str(sent).lower()

    def test_it_says_these_are_not_bake_quantities(self):
        # The card is where someone reads a number and goes to bake; the
        # caution travels with it (ADR 0012).
        post = FakePost()
        report_delivery.post_card(payload(), "https://link", "https://hook", post=post)
        assert "not bake quantities" in card_text(post.sent)

    def test_it_says_when_the_headline_model_is_a_substitute(self):
        # The headline model failed to cover the window, so the leading page is
        # another model's. Quoting it under a card that looks like every other
        # week would be a silent substitution (ADR 0011).
        post = FakePost()
        report_delivery.post_card(
            payload(pages=[page(model="holt_winters")]),
            "https://link", "https://hook", post=post,
        )
        text = card_text(post.sent)

        assert "holt_winters" in text
        assert "ewma" in text
        assert "did not cover the whole window" in text

    def test_it_says_nothing_about_substitution_when_the_headline_led(self):
        post = FakePost()
        report_delivery.post_card(payload(), "https://link", "https://hook", post=post)
        assert "did not cover" not in card_text(post.sent)

    def test_it_posts_to_the_webhook_url(self):
        post = FakePost()
        report_delivery.post_card(payload(), "https://link", "https://hook", post=post)
        assert post.calls[0]["url"] == "https://hook"

    def test_a_rejected_post_raises(self):
        post = FakePost(status_code=400, text="invalid card")
        with pytest.raises(RuntimeError, match="400"):
            report_delivery.post_card(
                payload(), "https://link", "https://hook", post=post
            )


class TestTheRefusal:
    def test_it_posts_plain_text_not_a_card(self):
        # It has to be legible when something is already broken.
        post = FakePost()
        report_delivery.post_refusal(refusal(), "https://hook", post=post)

        assert set(post.sent) == {"text"}

    def test_it_names_the_report_the_failure_and_the_fix(self):
        post = FakePost()
        report_delivery.post_refusal(refusal(), "https://hook", post=post)
        text = post.sent["text"]

        assert "Bagel forecast" in text
        assert "2026-07-31" in text  # the origin it actually found
        assert "Daily Demand Forecast" in text  # the fix

    def test_a_superseded_config_refusal_carries_its_own_fix(self):
        post = FakePost()
        superseded = Refusal(
            report_name="Bagel forecast",
            reason="superseded_config",
            message="… repoint this report's forecast_config_version at it.",
        )
        report_delivery.post_refusal(superseded, "https://hook", post=post)

        assert "forecast_config_version" in post.sent["text"]

    def test_a_rejected_post_raises(self):
        post = FakePost(status_code=500, text="boom")
        with pytest.raises(RuntimeError, match="500"):
            report_delivery.post_refusal(refusal(), "https://hook", post=post)


class TestTheWebhookRateLimit:
    def test_the_minimum_interval_is_at_least_a_second(self):
        # Webhooks are limited to 1 request/second per space, so the caller
        # paces deliveries rather than firing them concurrently (ADR 0010).
        assert report_delivery.WEBHOOK_MIN_INTERVAL_SECONDS >= 1.0
