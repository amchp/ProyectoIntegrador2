"""Small boto3 construction helpers for infra commands."""

from __future__ import annotations

LAB_ROLE_NAME = "LabRole"
LAB_INSTANCE_PROFILE_CANDIDATES = ("LabRole", "LabInstanceProfile")


def aws_session(region: str):
    import boto3

    return boto3.Session(region_name=region)


def aws_client(service_name: str, *, region: str):
    return aws_session(region).client(service_name)


def aws_clients(region: str, *service_names: str):
    session = aws_session(region)
    return tuple(session.client(service_name) for service_name in service_names)


def lab_role_arn(iam_client) -> str:
    return iam_client.get_role(RoleName=LAB_ROLE_NAME)["Role"]["Arn"]


def lab_instance_profile_name(iam_client) -> str:
    try:
        profiles = iam_client.list_instance_profiles_for_role(RoleName=LAB_ROLE_NAME)[
            "InstanceProfiles"
        ]
    except Exception as error:
        if error.__class__.__name__ != "NoSuchEntityException":
            raise
        profiles = []

    if profiles:
        return sorted(profile["InstanceProfileName"] for profile in profiles)[0]

    for profile_name in LAB_INSTANCE_PROFILE_CANDIDATES:
        try:
            profile = iam_client.get_instance_profile(InstanceProfileName=profile_name)[
                "InstanceProfile"
            ]
        except Exception as error:
            if error.__class__.__name__ != "NoSuchEntityException":
                raise
            continue

        role_names = {role["RoleName"] for role in profile.get("Roles", [])}
        if LAB_ROLE_NAME in role_names or profile_name == LAB_ROLE_NAME:
            return profile_name

    raise RuntimeError(
        f"No EC2 instance profile was found for {LAB_ROLE_NAME}. "
        "AWS Academy accounts often expose an instance profile named LabInstanceProfile; "
        "if it is missing, the lab must provide or create an instance profile containing LabRole."
    )
