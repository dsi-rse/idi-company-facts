"""ECS cluster and Fargate task definition for the company facts processor.

Per-pipeline arguments (SEC bucket, output paths, mode) are injected by the
EventBridge schedule via ECS containerOverrides — see scheduling.py. The task
definition's baseline command is `--help` so a misconfigured override fails
loudly instead of silently running a default pipeline.
"""

import json

import pulumi_aws as aws

import pulumi

from . import config, ecr, iam, logs

# -----------------------------------------------------------------------------
# ECS Cluster (Fargate only)
# -----------------------------------------------------------------------------
cluster = aws.ecs.Cluster(
    "idi-ecs-cluster",
    name=f"{config.name_prefix}-cluster",
    settings=[
        aws.ecs.ClusterSettingArgs(
            name="containerInsights",
            value="enabled",
        )
    ],
    tags=config.tags(),
)

# -----------------------------------------------------------------------------
# Task Definition
# -----------------------------------------------------------------------------
CONTAINER_NAME = "company-facts-orchestrator"

cpu = config.config.get("cpu") or "1024"
memory = config.config.get("memory") or "4096"
num_workers = config.config.get("num_workers") or "10"

# Build S3 paths from the externally managed bucket (name from SSM via config).
# The orchestrator reads the SEC manifest at s3://{bucket}/sec/manifest.parquet
# (the "sec/" prefix is hardcoded in the orchestrator — only the bucket name is
# passed via --sec-bucket).
database_prefix = config.config.get("database_prefix") or "database"
output_file = f"s3://{config.bucket_name}/{database_prefix}/{config.app_name}/latest.parquet"
failure_file = f"s3://{config.bucket_name}/{config.app_name}/failures/failures.json"

# Container definition as JSON (required by aws.ecs.TaskDefinition)
container_definitions = pulumi.Output.all(
    image=ecr.orchestrator_image,
    log_group_name=logs.log_group.name,
    region=config.aws_region,
).apply(
    lambda args: json.dumps(
        [
            {
                "name": CONTAINER_NAME,
                "image": args["image"],
                "essential": True,
                "command": ["--help"],
                "environment": [
                    {"name": "AWS_REGION", "value": args["region"]},
                    {"name": "CLOUDWATCH_LOGS_ENABLED", "value": "false"},
                    {"name": "PYTHONUNBUFFERED", "value": "1"},
                    {"name": "SEC_USER_AGENT", "value": config.sec_user_agent},
                ],
                "logConfiguration": {
                    "logDriver": "awslogs",
                    "options": {
                        "awslogs-group": args["log_group_name"],
                        "awslogs-region": args["region"],
                        "awslogs-stream-prefix": "orchestrator",
                    },
                },
                "stopTimeout": 30,
            }
        ]
    )
)

task_definition = aws.ecs.TaskDefinition(
    "idi-ecs-task-definition",
    family=f"{config.name_prefix}",
    requires_compatibilities=["FARGATE"],
    network_mode="awsvpc",
    cpu=cpu,
    memory=memory,
    execution_role_arn=iam.task_execution_role.arn,
    task_role_arn=iam.task_role.arn,
    container_definitions=container_definitions,
    tags=config.tags(),
)
