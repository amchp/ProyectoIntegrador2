from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INFRA_DIR = ROOT / "infra"
sys.path.insert(0, str(INFRA_DIR))

from utils.aws import lab_instance_profile_name


class NoSuchEntityException(Exception):
    pass


class FakeIamExceptions:
    NoSuchEntityException = NoSuchEntityException


class FakeIamClient:
    exceptions = FakeIamExceptions

    def __init__(self, *, role_profiles: list[dict] | None = None, named_profiles: dict[str, dict] | None = None):
        self.role_profiles = role_profiles or []
        self.named_profiles = named_profiles or {}

    def list_instance_profiles_for_role(self, *, RoleName: str) -> dict:
        return {"InstanceProfiles": self.role_profiles}

    def get_instance_profile(self, *, InstanceProfileName: str) -> dict:
        if InstanceProfileName not in self.named_profiles:
            raise NoSuchEntityException(InstanceProfileName)
        return {"InstanceProfile": self.named_profiles[InstanceProfileName]}


class AwsHelperTests(unittest.TestCase):
    def test_lab_instance_profile_uses_profile_attached_to_lab_role(self) -> None:
        client = FakeIamClient(
            role_profiles=[
                {"InstanceProfileName": "z-profile"},
                {"InstanceProfileName": "a-profile"},
            ]
        )

        self.assertEqual(lab_instance_profile_name(client), "a-profile")

    def test_lab_instance_profile_falls_back_to_lab_instance_profile_name(self) -> None:
        client = FakeIamClient(
            named_profiles={
                "LabInstanceProfile": {
                    "InstanceProfileName": "LabInstanceProfile",
                    "Roles": [{"RoleName": "LabRole"}],
                }
            }
        )

        self.assertEqual(lab_instance_profile_name(client), "LabInstanceProfile")


if __name__ == "__main__":
    unittest.main()
