"""EC2 helper functions shared by infra commands."""

from __future__ import annotations

from botocore.exceptions import ClientError

from utils.common import ensure, serialize_tags


ACTIVE_INSTANCE_STATES = ["pending", "running", "stopping", "stopped"]
GPU_INSTANCE_FAMILIES = {"g4dn", "g5", "g6", "p3", "p4", "p5"}
DEEP_LEARNING_AMI_PARAMETERS = [
    "/aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id",
    "/aws/service/deeplearning/ami/x86_64/pytorch-2.4-ubuntu-22.04/latest/ami-id",
    "/aws/service/deeplearning/ami/x86_64/pytorch-2.3-ubuntu-22.04/latest/ami-id",
]


def is_gpu_instance_type(instance_type: str) -> bool:
    return instance_type.split(".", 1)[0] in GPU_INSTANCE_FAMILIES


def find_deep_learning_ami_id(ec2_client, ssm_client) -> str:
    for parameter_name in DEEP_LEARNING_AMI_PARAMETERS:
        try:
            return ssm_client.get_parameter(Name=parameter_name)["Parameter"]["Value"]
        except ClientError:
            continue

    images = ec2_client.describe_images(
        Owners=["amazon"],
        Filters=[
            {"Name": "name", "Values": ["Deep Learning*GPU*PyTorch*Ubuntu*22.04*"]},
            {"Name": "state", "Values": ["available"]},
            {"Name": "architecture", "Values": ["x86_64"]},
        ],
    )["Images"]
    if not images:
        raise RuntimeError(
            "Could not resolve a Deep Learning AMI. Set FINBERT_AMI_ID to a GPU-ready AMI id."
        )
    return sorted(images, key=lambda image: image["CreationDate"])[-1]["ImageId"]


def find_amazon_linux_2_ami_id(ssm_client) -> str:
    return ssm_client.get_parameter(
        Name="/aws/service/ami-amazon-linux-latest/amzn2-ami-hvm-x86_64-gp2"
    )["Parameter"]["Value"]


def resolve_ami_id(
    ec2_client,
    ssm_client,
    *,
    instance_type: str,
    configured_ami_id: str = "",
) -> str:
    if configured_ami_id:
        return configured_ami_id
    if is_gpu_instance_type(instance_type):
        return find_deep_learning_ami_id(ec2_client, ssm_client)
    return find_amazon_linux_2_ami_id(ssm_client)


def find_public_subnet_id(ec2_client, *, vpc_id: str) -> str | None:
    response = ec2_client.describe_subnets(
        Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "map-public-ip-on-launch", "Values": ["true"]},
            {"Name": "state", "Values": ["available"]},
        ]
    )
    subnets = sorted(response["Subnets"], key=lambda subnet: subnet["SubnetId"])
    return subnets[0]["SubnetId"] if subnets else None


def find_instance_by_name(
    ec2_client,
    *,
    name: str,
    states: list[str] | None = None,
) -> dict | None:
    reservations = ec2_client.describe_instances(
        Filters=[
            {"Name": "tag:Name", "Values": [name]},
            {"Name": "instance-state-name", "Values": states or ACTIVE_INSTANCE_STATES},
        ]
    )["Reservations"]
    instances = [
        instance
        for reservation in reservations
        for instance in reservation["Instances"]
    ]
    return instances[0] if instances else None


def require_instance_by_name(
    ec2_client,
    *,
    name: str,
    states: list[str] | None = None,
    message: str | None = None,
) -> dict:
    instance = find_instance_by_name(ec2_client, name=name, states=states)
    if not instance:
        raise RuntimeError(message or f"No EC2 instance found with Name={name}.")
    return instance


def wait_for_running(ec2_client, instance_id: str) -> dict:
    ec2_client.get_waiter("instance_running").wait(InstanceIds=[instance_id])
    ec2_client.get_waiter("instance_status_ok").wait(InstanceIds=[instance_id])
    return ec2_client.describe_instances(InstanceIds=[instance_id])["Reservations"][0]["Instances"][0]


def ensure_instance(
    ec2_client,
    *,
    name: str,
    run_instances_kwargs: dict,
    tags: dict[str, str],
    display_name: str = "EC2 instance",
) -> str:
    def update(instance: dict) -> str:
        instance_id = instance["InstanceId"]
        state = instance["State"]["Name"]
        if state == "stopped":
            ec2_client.start_instances(InstanceIds=[instance_id])
            print(f"Started existing {display_name}: {instance_id}")
        else:
            print(f"Reusing existing {display_name}: {instance_id} ({state})")
        return instance_id

    def create() -> str:
        response = ec2_client.run_instances(
            **run_instances_kwargs,
            TagSpecifications=[
                {
                    "ResourceType": "instance",
                    "Tags": serialize_tags({"Name": name, **tags}),
                }
            ],
        )
        instance_id = response["Instances"][0]["InstanceId"]
        print(f"Launched {display_name}: {instance_id}")
        return instance_id

    return ensure(
        lambda: find_instance_by_name(ec2_client, name=name),
        create,
        update=update,
    )


def find_elastic_ip(ec2_client, *, name: str) -> dict | None:
    addresses = ec2_client.describe_addresses(
        Filters=[{"Name": "tag:Name", "Values": [name]}]
    )["Addresses"]
    return addresses[0] if addresses else None


def ensure_elastic_ip(
    ec2_client,
    *,
    name: str,
    tags: dict[str, str],
) -> dict:
    def create() -> dict:
        response = ec2_client.allocate_address(
            Domain="vpc",
            TagSpecifications=[
                {
                    "ResourceType": "elastic-ip",
                    "Tags": serialize_tags({"Name": name, **tags}),
                }
            ],
        )
        print(f"Allocated Elastic IP: {response['PublicIp']} ({response['AllocationId']})")
        return response

    return ensure(lambda: find_elastic_ip(ec2_client, name=name), create)


def associate_elastic_ip(ec2_client, *, allocation_id: str, instance_id: str) -> None:
    address = ec2_client.describe_addresses(AllocationIds=[allocation_id])["Addresses"][0]
    if address.get("InstanceId") == instance_id:
        print(f"Elastic IP is already associated with {instance_id}.")
        return
    if "AssociationId" in address:
        ec2_client.disassociate_address(AssociationId=address["AssociationId"])
        print(f"Disassociated Elastic IP from {address.get('InstanceId', 'previous resource')}.")

    try:
        ec2_client.associate_address(
            AllocationId=allocation_id,
            InstanceId=instance_id,
            AllowReassociation=True,
        )
    except ClientError as error:
        if error.response["Error"]["Code"] == "IncorrectInstanceState":
            raise RuntimeError(
                "The EC2 instance is not in a state that can receive an Elastic IP. "
                "Start it, then rerun this script."
            ) from error
        raise
    print(f"Associated Elastic IP with instance: {instance_id}")


def associate_preallocated_elastic_ip(
    ec2_client,
    *,
    elastic_ip_name: str,
    instance_id: str,
) -> str | None:
    address = find_elastic_ip(ec2_client, name=elastic_ip_name)
    if not address:
        return None
    if address.get("InstanceId") == instance_id:
        return address["PublicIp"]

    allocation_id = address["AllocationId"]
    ec2_client.associate_address(
        AllocationId=allocation_id,
        InstanceId=instance_id,
        AllowReassociation=True,
    )
    refreshed = ec2_client.describe_addresses(AllocationIds=[allocation_id])["Addresses"][0]
    public_ip = refreshed["PublicIp"]
    print(f"Associated preallocated Elastic IP with instance: {public_ip}")
    return public_ip
