#!/usr/bin/env python3
"""Clean up legacy ob-docs AWS stack after ob-note cutover.

Hardcodes obsolete resource names from config.ob-docs.backup.json.
Does NOT touch the live ob-note stack.

Usage:
  python cleanup_ob_docs_legacy.py --yes
  python cleanup_ob_docs_legacy.py --yes --keep-s3
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("cleanup-ob-docs-legacy")

ROOT = Path(__file__).resolve().parent
BACKUP = ROOT / "config.ob-docs.backup.json"

PROJECT = "ob-docs"
CLUSTER = f"cluster-for-{PROJECT}"
SERVICE_NAME = f"service-for-{PROJECT}"
TASK_FAMILY = f"task-for-{PROJECT}"
TG_NAME = f"TG-for-{PROJECT}"
ECR_NAME = f"ecr-for-{PROJECT}"
LOG_GROUP = f"/ecs/app-for-{PROJECT}"
ALB_NAME = f"alb-for-{PROJECT}"
CF_COMMENT = f"CloudFront-for-{PROJECT}"
SECRETS = [
    f"{PROJECT}/vault-agent-token",
    f"{PROJECT}/cloudfront-alb-origin-header",
    f"{PROJECT}/session-signing-key",
]
HARNESS_NAME = "ob_docs"
MCP_RUNTIME_NAME = "use_vault_of_ob_docs"


def load_backup() -> dict[str, Any]:
    if BACKUP.is_file():
        return json.loads(BACKUP.read_text(encoding="utf-8"))
    return {}


def delete_secret(sm, name: str) -> None:
    try:
        sm.delete_secret(SecretId=name, ForceDeleteWithoutRecovery=True)
        logger.info("Deleted secret %s", name)
    except ClientError as e:
        if e.response["Error"]["Code"] not in {
            "ResourceNotFoundException",
            "SecretNotFoundException",
        }:
            logger.warning("secret %s: %s", name, e)


def empty_and_delete_bucket(s3, bucket: str) -> None:
    if not bucket:
        return
    try:
        paginator = s3.get_paginator("list_object_versions")
        for page in paginator.paginate(Bucket=bucket):
            objs = []
            for v in page.get("Versions") or []:
                objs.append({"Key": v["Key"], "VersionId": v["VersionId"]})
            for m in page.get("DeleteMarkers") or []:
                objs.append({"Key": m["Key"], "VersionId": m["VersionId"]})
            for i in range(0, len(objs), 1000):
                batch = objs[i : i + 1000]
                if batch:
                    s3.delete_objects(
                        Bucket=bucket, Delete={"Objects": batch, "Quiet": True}
                    )
        s3.delete_bucket(Bucket=bucket)
        logger.info("Deleted bucket %s", bucket)
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchBucket":
            logger.warning("bucket %s: %s", bucket, e)


def delete_cloudfront(cloudfront) -> None:
    marker = None
    dist = None
    while True:
        kwargs = {}
        if marker:
            kwargs["Marker"] = marker
        resp = cloudfront.list_distributions(**kwargs)
        for item in (resp.get("DistributionList") or {}).get("Items") or []:
            if item.get("Comment") == CF_COMMENT:
                dist = item
                break
        if dist or not (resp.get("DistributionList") or {}).get("IsTruncated"):
            break
        marker = (resp.get("DistributionList") or {}).get("NextMarker")
    if not dist:
        logger.info("No CloudFront with comment %s", CF_COMMENT)
        return
    dist_id = dist["Id"]
    try:
        cfg_resp = cloudfront.get_distribution_config(Id=dist_id)
        etag = cfg_resp["ETag"]
        cfg = cfg_resp["DistributionConfig"]
        if cfg.get("Enabled"):
            cfg["Enabled"] = False
            cfg["Aliases"] = {"Quantity": 0}
            cloudfront.update_distribution(
                Id=dist_id, IfMatch=etag, DistributionConfig=cfg
            )
            logger.info("Disabled CloudFront %s — waiting", dist_id)
            for _ in range(60):
                d = cloudfront.get_distribution(Id=dist_id)["Distribution"]
                if d.get("Status") == "Deployed" and not d["DistributionConfig"].get(
                    "Enabled"
                ):
                    break
                time.sleep(10)
        etag = cloudfront.get_distribution_config(Id=dist_id)["ETag"]
        cloudfront.delete_distribution(Id=dist_id, IfMatch=etag)
        logger.info("Deleted CloudFront %s", dist_id)
    except ClientError as e:
        logger.warning("CloudFront cleanup: %s", e)


def delete_iam_role(iam, role_name: str) -> None:
    try:
        for p in iam.list_role_policies(RoleName=role_name).get("PolicyNames") or []:
            iam.delete_role_policy(RoleName=role_name, PolicyName=p)
        for p in iam.list_attached_role_policies(RoleName=role_name).get(
            "AttachedPolicies"
        ) or []:
            iam.detach_role_policy(RoleName=role_name, PolicyArn=p["PolicyArn"])
        iam.delete_role(RoleName=role_name)
        logger.info("Deleted role %s", role_name)
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            logger.warning("role %s: %s", role_name, e)


def delete_harness_and_mcp(region: str, backup: dict[str, Any]) -> None:
    control = boto3.client("bedrock-agentcore-control", region_name=region)
    harness_arn = str(backup.get("HARNESS_ARN") or "").strip()
    if harness_arn:
        harness_id = harness_arn.rsplit("/", 1)[-1]
        try:
            control.delete_harness(harnessId=harness_id)
            logger.info("Deleted harness %s", harness_id)
        except Exception as e:
            logger.warning("harness delete: %s", e)
    runtime_arn = str(backup.get("use_vault_mcp_runtime_arn") or "").strip()
    if runtime_arn:
        # AgentCore expects agentRuntimeId, not full ARN
        runtime_id = runtime_arn.rsplit("/", 1)[-1]
        try:
            control.delete_agent_runtime(agentRuntimeId=runtime_id)
            logger.info("Deleted MCP runtime %s", runtime_id)
        except Exception as e:
            logger.warning("MCP runtime delete: %s", e)
    role = str(backup.get("use_vault_mcp_role") or "").strip()
    if role:
        delete_iam_role(boto3.client("iam"), role.rsplit("/", 1)[-1])
    ecr_name = str(backup.get("use_vault_mcp_ecr_repository") or MCP_RUNTIME_NAME)
    try:
        boto3.client("ecr", region_name=region).delete_repository(
            repositoryName=ecr_name, force=True
        )
        logger.info("Deleted ECR %s", ecr_name)
    except ClientError as e:
        if e.response["Error"]["Code"] != "RepositoryNotFoundException":
            logger.warning("MCP ECR: %s", e)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--keep-s3", action="store_true")
    args = parser.parse_args()
    if not args.yes:
        logger.error("Refusing without --yes")
        return 1

    backup = load_backup()
    region = str(backup.get("region") or "us-west-2")
    bucket = str(backup.get("s3_bucket") or f"storage-for-{PROJECT}-262976740991-{region}")

    ecs = boto3.client("ecs", region_name=region)
    elbv2 = boto3.client("elbv2", region_name=region)
    ecr = boto3.client("ecr", region_name=region)
    logs = boto3.client("logs", region_name=region)
    sm = boto3.client("secretsmanager", region_name=region)
    s3 = boto3.client("s3", region_name=region)
    iam = boto3.client("iam")
    cloudfront = boto3.client("cloudfront", region_name="us-east-1")
    ec2 = boto3.client("ec2", region_name=region)

    logger.info("=== Cleaning legacy project %s ===", PROJECT)

    try:
        ecs.update_service(
            cluster=CLUSTER, service=SERVICE_NAME, desiredCount=0
        )
        ecs.delete_service(cluster=CLUSTER, service=SERVICE_NAME, force=True)
        logger.info("Deleted ECS service %s", SERVICE_NAME)
    except ClientError as e:
        logger.info("ECS service: %s", e)

    try:
        for td in ecs.list_task_definitions(familyPrefix=TASK_FAMILY).get(
            "taskDefinitionArns"
        ) or []:
            ecs.deregister_task_definition(taskDefinition=td)
    except ClientError as e:
        logger.info("task defs: %s", e)

    try:
        ecs.delete_cluster(cluster=CLUSTER)
        logger.info("Deleted cluster %s", CLUSTER)
    except ClientError as e:
        logger.info("cluster: %s", e)

    try:
        tgs = elbv2.describe_target_groups(Names=[TG_NAME])["TargetGroups"]
        for tg in tgs:
            elbv2.delete_target_group(TargetGroupArn=tg["TargetGroupArn"])
            logger.info("Deleted TG %s", TG_NAME)
    except ClientError as e:
        logger.info("TG: %s", e)

    try:
        albs = elbv2.describe_load_balancers(Names=[ALB_NAME])["LoadBalancers"]
        for alb in albs:
            alb_arn = alb["LoadBalancerArn"]
            vpc_id = alb.get("VpcId")
            for listener in elbv2.describe_listeners(LoadBalancerArn=alb_arn).get(
                "Listeners"
            ) or []:
                elbv2.delete_listener(ListenerArn=listener["ListenerArn"])
            elbv2.delete_load_balancer(LoadBalancerArn=alb_arn)
            logger.info("Deleted ALB %s", ALB_NAME)
            time.sleep(15)
            if vpc_id:
                # Best-effort SG/subnet cleanup skipped — may be shared; leave VPC if in use
                logger.info("Left VPC %s for manual review if unused", vpc_id)
    except ClientError as e:
        logger.info("ALB: %s", e)

    try:
        ecr.delete_repository(repositoryName=ECR_NAME, force=True)
        logger.info("Deleted ECR %s", ECR_NAME)
    except ClientError as e:
        logger.info("ECR: %s", e)

    try:
        logs.delete_log_group(logGroupName=LOG_GROUP)
    except ClientError:
        pass

    for name in SECRETS:
        delete_secret(sm, name)

    delete_cloudfront(cloudfront)

    for role in (
        f"role-ecs-task-for-{PROJECT}-{region}",
        f"role-ecs-execution-for-{PROJECT}-{region}",
        f"role-harness-for-{PROJECT}-{region}",
        f"role-s3files-sync-for-{PROJECT}",
    ):
        delete_iam_role(iam, role)

    delete_harness_and_mcp(region, backup)

    if not args.keep_s3:
        empty_and_delete_bucket(s3, bucket)
    else:
        logger.info("Keeping S3 bucket %s", bucket)

    logger.info("Legacy cleanup done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
