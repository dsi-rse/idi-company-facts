"""VPC, security groups, and VPC endpoints for ECS Fargate tasks."""

import pulumi_aws as aws

from . import config

# -----------------------------------------------------------------------------
# Default VPC
# -----------------------------------------------------------------------------
default_vpc = aws.ec2.get_vpc(default=True)
default_vpc_subnets = aws.ec2.get_subnets_output(
    filters=[aws.ec2.GetSubnetsFilterArgs(name="vpc-id", values=[default_vpc.id])],
)
# Single subnet — single AZ for simplicity.
primary_subnet_id = default_vpc_subnets.ids.apply(lambda ids: ids[0] if ids else None)

# -----------------------------------------------------------------------------
# ECS Fargate Security Group — egress only (tasks don't serve traffic)
# -----------------------------------------------------------------------------
ecs_sg = aws.ec2.SecurityGroup(
    "idi-sg-ecs",
    name=f"{config.name_prefix}-sg-ecs",
    description="Security group for ECS Fargate tasks - no inbound, all outbound",
    vpc_id=default_vpc.id,
    ingress=[],
    egress=[
        aws.ec2.SecurityGroupEgressArgs(
            description="Allow all outbound traffic",
            from_port=0,
            to_port=0,
            protocol="-1",
            cidr_blocks=["0.0.0.0/0"],
        )
    ],
    tags=config.tags({"purpose": "ECS Fargate tasks"}),
)
