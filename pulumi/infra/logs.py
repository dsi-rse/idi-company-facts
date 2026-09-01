"""CloudWatch log group for ECS task."""

import pulumi_aws as aws

from . import config

# -----------------------------------------------------------------------------
# CloudWatch Log Group for awslogs driver
# -----------------------------------------------------------------------------
log_group = aws.cloudwatch.LogGroup(
    "idi-ecs-log-group",
    name=f"/ecs/{config.name_prefix}",
    retention_in_days=config.log_retention_days,
    tags=config.tags(),
)
