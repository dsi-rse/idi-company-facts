"""IAM roles and policies for ECS Fargate tasks.

Two roles:
  1. Task Execution Role — used by the ECS agent to pull images and write awslogs.
  2. Task Role — assumed by the container at runtime for S3 access and
     ECS Exec (SSM) support.
"""

import json

import pulumi_aws as aws

from . import config, ecr, logs

# -----------------------------------------------------------------------------
# Task Execution Role (ECS agent — pulls image, writes logs)
# -----------------------------------------------------------------------------
task_execution_role = aws.iam.Role(
    "idi-role-ecs-execution",
    name=f"{config.name_prefix}-role-ecs-execution",
    description="ECS task execution role: image pull, awslogs",
    assume_role_policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
    ),
    tags=config.tags(),
)

# Inline: pull from our specific ECR repo only
# GetAuthorizationToken must be * (account-level action, cannot be scoped to a repo)
task_execution_ecr_policy = aws.iam.RolePolicy(
    "idi-policy-ecs-execution-ecr",
    role=task_execution_role.id,
    policy=ecr.ecr_repo.arn.apply(
        lambda arn: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": "ecr:GetAuthorizationToken",
                        "Resource": "*",
                    },
                    {
                        "Effect": "Allow",
                        "Action": [
                            "ecr:BatchGetImage",
                            "ecr:GetDownloadUrlForLayer",
                            "ecr:BatchCheckLayerAvailability",
                        ],
                        "Resource": arn,
                    },
                ],
            }
        )
    ),
)

task_execution_logs_policy = aws.iam.RolePolicy(
    "idi-policy-ecs-execution-logs",
    role=task_execution_role.id,
    policy=logs.log_group.arn.apply(
        lambda arn: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "logs:CreateLogStream",
                            "logs:PutLogEvents",
                        ],
                        "Resource": f"{arn}:*",
                    }
                ],
            }
        )
    ),
)

# -----------------------------------------------------------------------------
# Task Role (container runtime — S3 access + ECS Exec via SSM)
# -----------------------------------------------------------------------------
task_role = aws.iam.Role(
    "idi-role-ecs-task",
    name=f"{config.name_prefix}-role-ecs-task",
    description="ECS task role: S3 read/write, ECS Exec",
    assume_role_policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
    ),
    tags=config.tags(),
)

# -----------------------------------------------------------------------------
# S3 bucket (looked up by name — owned by a separate project/stack)
# Name comes from SSM (/idi/<stack>/shared/processor_bucket_name) via config.
# -----------------------------------------------------------------------------
bucket = aws.s3.get_bucket_output(bucket=config.bucket_name)

task_s3_policy = aws.iam.RolePolicy(
    "idi-policy-ecs-task-s3",
    role=task_role.id,
    policy=bucket.arn.apply(
        lambda arn: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": ["s3:ListBucket"],
                        "Resource": arn,
                    },
                    {
                        "Effect": "Allow",
                        "Action": [
                            "s3:GetObject",
                            "s3:PutObject",
                            "s3:DeleteObject",
                            "s3:AbortMultipartUpload",
                            "s3:CreateMultipartUpload",
                            "s3:UploadPart",
                            "s3:CompleteMultipartUpload",
                            "s3:ListMultipartUploadParts",
                        ],
                        "Resource": f"{arn}/*",
                    },
                ],
            }
        )
    ),
)

# ECS Exec (SSM) policy — allows `aws ecs execute-command` for debugging
task_ssm_policy = aws.iam.RolePolicy(
    "idi-policy-ecs-task-ssm",
    role=task_role.id,
    policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "ssmmessages:CreateControlChannel",
                        "ssmmessages:CreateDataChannel",
                        "ssmmessages:OpenControlChannel",
                        "ssmmessages:OpenDataChannel",
                    ],
                    "Resource": "*",
                }
            ],
        }
    ),
)
