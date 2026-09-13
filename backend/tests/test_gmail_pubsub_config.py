"""Push delivery must not start half-configured.

The degraded mode is the dangerous one. Push enabled with no topic registers no
watch, raises nothing, and looks exactly like a working system with a quiet
mailbox - while the recurring Inbox scans this feature replaces are already
switched off. So the check is fatal, and it runs at construction, which means
alembic, the worker and the subscriber all fail the same way the API does.
"""

import unittest

from pydantic import ValidationError

from app.config import Settings

CONFIGURED = {
    "gmail_pubsub_project_id": "codejob-prod",
    "gmail_pubsub_topic_id": "gmail-mailbox-events",
    "gmail_pubsub_subscription_id": "codejob-gmail-mailbox-events",
}


def _settings(**overrides: object) -> Settings:
    # _env_file=None so a developer's own backend/.env cannot decide the result.
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


class DefaultsTests(unittest.TestCase):
    def test_the_feature_is_off_by_default(self) -> None:
        settings = _settings()

        self.assertFalse(settings.feature_gmail_pubsub_enabled)
        self.assertEqual(settings.gmail_pubsub_project_id, "")

    def test_nothing_is_required_while_the_feature_is_off(self) -> None:
        """This lands on a running install without any Cloud setup at all."""
        self.assertFalse(_settings(feature_gmail_pubsub_enabled=False).feature_gmail_pubsub_enabled)

    def test_push_delivery_is_configured_by_resource_names_and_nothing_else(self) -> None:
        """Four fields, all of them identifiers for things created by hand in
        Google Cloud. Anything else under this prefix is a knob, and the one
        that would obviously be added first - the renewal cadence - must not
        be: Gmail expires a watch after seven days, daily renewal is its
        operational requirement rather than a preference, and a setting for it
        is only ever a way to set it too high. That failure is silent.
        Delivery stops and nothing errors.

        Named `gmail_pubsub_*` deliberately, so this notices an addition. The
        unrelated `label_*_watch*` settings are recruiter address watches, a
        different feature that happens to share the word.
        """
        fields = set(Settings.model_fields)

        self.assertEqual(
            {name for name in fields if name.startswith("gmail_pubsub")},
            set(CONFIGURED),
        )
        self.assertEqual({name for name in fields if "renew" in name}, set())

    def test_the_service_account_path_is_not_a_setting(self) -> None:
        """The Google client reads GOOGLE_APPLICATION_CREDENTIALS from the
        process environment itself. A field holding the path to a
        service-account key is one `repr()` away from a log line."""
        self.assertNotIn("google_application_credentials", set(Settings.model_fields))


class RequirementTests(unittest.TestCase):
    def test_the_feature_starts_when_every_resource_is_named(self) -> None:
        settings = _settings(feature_gmail_pubsub_enabled=True, **CONFIGURED)

        self.assertTrue(settings.feature_gmail_pubsub_enabled)

    def test_each_missing_identifier_is_refused_by_name(self) -> None:
        for absent in CONFIGURED:
            with self.subTest(missing=absent):
                with self.assertRaises(ValidationError) as caught:
                    _settings(
                        feature_gmail_pubsub_enabled=True,
                        **{**CONFIGURED, absent: ""},
                    )
                self.assertIn(absent.upper(), str(caught.exception))

    def test_one_error_names_every_missing_identifier(self) -> None:
        """Fixing three of these one restart at a time is three deployments."""
        with self.assertRaises(ValidationError) as caught:
            _settings(feature_gmail_pubsub_enabled=True)

        message = str(caught.exception)
        for name in CONFIGURED:
            self.assertIn(name.upper(), message)

    def test_the_error_says_who_creates_the_topic(self) -> None:
        """Otherwise the fix looks like 'set a variable' rather than 'create a
        subscription in Cloud'. This application deliberately creates neither."""
        with self.assertRaises(ValidationError) as caught:
            _settings(feature_gmail_pubsub_enabled=True)

        self.assertIn("does not create them", str(caught.exception))

    def test_whitespace_is_not_a_resource_name(self) -> None:
        with self.assertRaises(ValidationError):
            _settings(feature_gmail_pubsub_enabled=True, **{**CONFIGURED, "gmail_pubsub_topic_id": "   "})


class NormalisationTests(unittest.TestCase):
    def test_a_pasted_newline_is_stripped(self) -> None:
        """A trailing newline from a copy-paste becomes a subscription path
        Google cannot resolve, and the error names the wrong problem."""
        settings = _settings(
            feature_gmail_pubsub_enabled=True,
            **{**CONFIGURED, "gmail_pubsub_topic_id": " gmail-mailbox-events\n"},
        )

        self.assertEqual(settings.gmail_pubsub_topic_id, "gmail-mailbox-events")

    def test_values_are_normalised_even_with_the_feature_off(self) -> None:
        """Otherwise the padding survives until the day someone turns it on."""
        settings = _settings(
            feature_gmail_pubsub_enabled=False,
            gmail_pubsub_project_id="  codejob-prod  ",
        )

        self.assertEqual(settings.gmail_pubsub_project_id, "codejob-prod")


class ResourceNameTests(unittest.TestCase):
    def test_the_topic_is_fully_qualified(self) -> None:
        """`users.watch` takes the long form, not the bare topic id."""
        settings = _settings(feature_gmail_pubsub_enabled=True, **CONFIGURED)

        self.assertEqual(
            settings.gmail_pubsub_topic_name,
            "projects/codejob-prod/topics/gmail-mailbox-events",
        )

    def test_the_subscription_is_fully_qualified(self) -> None:
        settings = _settings(feature_gmail_pubsub_enabled=True, **CONFIGURED)

        self.assertEqual(
            settings.gmail_pubsub_subscription_name,
            "projects/codejob-prod/subscriptions/codejob-gmail-mailbox-events",
        )

    def test_an_unconfigured_name_is_empty_rather_than_half_built(self) -> None:
        """`projects//topics/` would reach Google and come back as an argument
        error about a topic, which reads like a Cloud problem rather than a
        missing setting."""
        settings = _settings()

        self.assertEqual(settings.gmail_pubsub_topic_name, "")
        self.assertEqual(settings.gmail_pubsub_subscription_name, "")


if __name__ == "__main__":
    unittest.main()
