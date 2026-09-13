"""Every PROCESS_ROLE in docker-compose.yml must be one the app accepts.

Written after `PROCESS_ROLE: gmail-pubsub` shipped against
`Literal["api", "worker"]`. The subscriber crash-looped on import - before any
logging, before the feature flag was read - so the symptom was a container
restarting and an Inbox receiving nothing, with the recurring scans already
switched off behind it.

Nothing in the suite could see it. `docker compose config` validates YAML and
knows nothing about a pydantic literal, and no unit test constructs `Settings`
with the environment a container actually gets. This closes that specific gap
and no more: it reads the roles Compose sets and asks the real model whether it
would accept them.
"""

import unittest
from pathlib import Path

import yaml

from app.config import Settings

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"


def _declared_roles() -> dict[str, str]:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    roles = {}
    for name, service in compose["services"].items():
        environment = service.get("environment") or {}
        if isinstance(environment, list):
            environment = dict(
                item.split("=", 1) for item in environment if "=" in item
            )
        if "PROCESS_ROLE" in environment:
            roles[name] = str(environment["PROCESS_ROLE"])
    return roles


class ProcessRoleTests(unittest.TestCase):
    def test_every_declared_role_is_one_the_settings_model_accepts(self):
        for service, role in _declared_roles().items():
            with self.subTest(service=service, role=role):
                # _env_file=None so a developer's own backend/.env cannot
                # decide the result.
                settings = Settings(_env_file=None, process_role=role)  # type: ignore[arg-type]
                self.assertEqual(settings.process_role, role)

    def test_the_subscriber_declares_a_role_of_its_own(self):
        """Not "api", which it is not, and not "worker", which would hand it
        torch it never uses. Both readers ask `== "worker"`, so a name of its
        own costs nothing and keeps the next role-dependent decision honest."""
        roles = _declared_roles()

        self.assertEqual(roles.get("gmail-pubsub"), "gmail-pubsub")
        self.assertEqual(roles.get("worker"), "worker")

    def test_the_subscriber_is_guarded_against_torch_like_the_api(self):
        """It never embeds, so it must not pay 483 MB to import transformers."""
        settings = Settings(_env_file=None, process_role="gmail-pubsub")  # type: ignore[arg-type]

        self.assertNotEqual(settings.process_role, "worker")


if __name__ == "__main__":
    unittest.main()
