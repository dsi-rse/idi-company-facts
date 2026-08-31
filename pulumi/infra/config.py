"""Pulumi configuration and shared constants."""

import pulumi_aws as aws

import pulumi

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
config = pulumi.Config("idi")
project_name = pulumi.get_project()
app_name = config.get("app_name") or "company-facts"
stack_name = pulumi.get_stack()
name_prefix = f"{project_name}-{stack_name}-{app_name}"
# Shared values are published by the shared stack to SSM (/idi/<stack>/shared/*)
# and read here.
bucket_name = aws.ssm.get_parameter(name=f"/idi/{stack_name}/shared/processor_bucket_name").value
shared_dlq_name = aws.ssm.get_parameter(name=f"/idi/{stack_name}/shared/dlq_name").value
log_retention_days = int(config.get("log_retention_days") or "30")
ecr_image_count = int(config.get("ecr_image_count") or "5")
sec_user_agent = config.require("sec_user_agent")

# AWS
aws_config = pulumi.Config("aws")
aws_region = aws_config.require("region")
caller = aws.get_caller_identity()


def tags(extra: dict | None = None) -> dict:
    """Common resource tags."""
    t = {
        "project": project_name,
        "environment": stack_name,
        "managed_by": "Pulumi",
        "app_name": app_name,
    }
    if extra:
        t.update(extra)
    return t
