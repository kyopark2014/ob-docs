#!/usr/bin/env python3
"""Remove infrastructure created by ob-docs/installer.py.

Always deletes **ob-docs-owned** resources:
  - ECS service ``service-for-ob-docs``
  - Task definitions ``task-for-ob-docs``
  - Target group ``TG-for-ob-docs``
  - ALB listener rules for ``/vault*``
  - ECR ``ecr-for-ob-docs``
  - Log group ``/ecs/app-for-ob-docs``
  - Secret ``agentic-work/vault-agent-token`` (created by ob-docs)

**Shared** agentic-work resources (ALB, VPC, cluster, S3 bucket, IAM roles,
origin/session secrets) are left alone when sharing is detected
(``service-for-agentic-work`` is ACTIVE, or other non-ob-docs services exist
on the cluster).

When the stack is **not** shared (standalone install via shared_infra), those
shared-named resources are deleted as well — same idea as
``agentic-work/uninstaller.py``, scoped to what this installer may have created.

Usage:
  python uninstaller.py
  python uninstaller.py --yes
  python uninstaller.py --yes --purge-vault-prefix   # also empty s3://…/vault/ when shared
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

from shared_infra import (
    ALB_NAME,
    CLUSTER,
    DEFAULT_REGION,
    ORIGIN_HEADER_SECRET,
    PROJECT,
    SESSION_SECRET,
    SHARED,
    VPC_NAME,
    default_bucket_name,
    load_json_if_exists,
)

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"

SERVICE_NAME = f"service-for-{PROJECT}"
SHARED_SERVICE = f"service-for-{SHARED}"
TASK_FAMILY = f"task-for-{PROJECT}"
TG_NAME = f"TG-for-{PROJECT}"
ECR_NAME = f"ecr-for-{PROJECT}"
LOG_GROUP = f"/ecs/app-for-{PROJECT}"
VAULT_AGENT_SECRET = f"{SHARED}/vault-agent-token"


def setup_logging() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    return logging.getLogger("ob-docs-uninstaller")


logger = setup_logging()


def load_cfg() -> dict[str, Any]:
    return load_json_if_exists(CONFIG_PATH)


def clients(region: str) -> dict[str, Any]:
    return {
        "ecs": boto3.client("ecs", region_name=region),
        "elbv2": boto3.client("elbv2", region_name=region),
        "ec2": boto3.client("ec2", region_name=region),
        "ecr": boto3.client("ecr", region_name=region),
        "logs": boto3.client("logs", region_name=region),
        "s3": boto3.client("s3", region_name=region),
        "sm": boto3.client("secretsmanager", region_name=region),
        "iam": boto3.client("iam"),
        "sts": boto3.client("sts", region_name=region),
    }


def _service_active(ecs, cluster: str, service: str) -> bool:
    try:
        services = ecs.describe_services(cluster=cluster, services=[service]).get(
            "services"
        ) or []
        return any(s.get("status") == "ACTIVE" for s in services)
    except ClientError:
        return False


def is_stack_shared(ecs) -> bool:
    """True when agentic-work (or another non-ob-docs service) still uses the cluster."""
    if _service_active(ecs, CLUSTER, SHARED_SERVICE):
        logger.info("  Sharing detected: %s is ACTIVE — shared infra will be kept", SHARED_SERVICE)
        return True
    try:
        arns = ecs.list_services(cluster=CLUSTER).get("serviceArns") or []
        if not arns:
            return False
        names = [a.rsplit("/", 1)[-1] for a in arns]
        others = [n for n in names if n != SERVICE_NAME]
        if others:
            logger.info(
                "  Sharing detected: other ECS services on %s: %s — shared infra kept",
                CLUSTER,
                ", ".join(others),
            )
            return True
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code != "ClusterNotFoundException":
            logger.warning("  Could not list cluster services: %s", e)
    logger.info("  No sharing detected — shared-named infra may be deleted")
    return False


def delete_ob_docs_ecs_service(ecs) -> None:
    logger.info("[1/8] Deleting ECS service %s", SERVICE_NAME)
    try:
        services = ecs.describe_services(cluster=CLUSTER, services=[SERVICE_NAME]).get(
            "services"
        ) or []
        svc = next((s for s in services if s.get("status") != "INACTIVE"), None)
        if not svc:
            logger.info("  Service not found / inactive")
            return
        ecs.update_service(cluster=CLUSTER, service=SERVICE_NAME, desiredCount=0)
        logger.info("  Scaled to 0")
        time.sleep(8)
        ecs.delete_service(cluster=CLUSTER, service=SERVICE_NAME, force=True)
        logger.info("  ✓ Deleted service %s", SERVICE_NAME)
        time.sleep(10)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code not in {"ClusterNotFoundException", "ServiceNotFoundException"}:
            logger.warning("  Could not delete service: %s", e)


def deregister_task_definitions(ecs) -> None:
    logger.info("[2/8] Deregistering task definitions %s*", TASK_FAMILY)
    try:
        paginator = ecs.get_paginator("list_task_definitions")
        for page in paginator.paginate(familyPrefix=TASK_FAMILY, sort="DESC"):
            for arn in page.get("taskDefinitionArns") or []:
                try:
                    ecs.deregister_task_definition(taskDefinition=arn)
                    logger.info("  ✓ Deregistered %s", arn.rsplit("/", 1)[-1])
                except ClientError as e:
                    logger.warning("  Could not deregister %s: %s", arn, e)
    except ClientError as e:
        logger.warning("  list_task_definitions: %s", e)


def delete_vault_listener_rules(elbv2) -> None:
    logger.info("[3/8] Deleting ALB /vault* listener rules")
    try:
        alb = elbv2.describe_load_balancers(Names=[ALB_NAME])["LoadBalancers"][0]
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "LoadBalancerNotFound":
            logger.info("  ALB %s not found", ALB_NAME)
            return
        raise

    listeners = elbv2.describe_listeners(LoadBalancerArn=alb["LoadBalancerArn"]).get(
        "Listeners"
    ) or []
    deleted = 0
    for listener in listeners:
        rules = elbv2.describe_rules(ListenerArn=listener["ListenerArn"]).get("Rules") or []
        for rule in rules:
            if rule.get("Priority") == "default":
                continue
            has_vault = False
            for cond in rule.get("Conditions") or []:
                if cond.get("Field") != "path-pattern":
                    continue
                for v in cond.get("Values") or []:
                    s = str(v)
                    if s == "/vault" or s.startswith("/vault/") or s == "/vault/*":
                        has_vault = True
                        break
            if not has_vault:
                continue
            try:
                elbv2.delete_rule(RuleArn=rule["RuleArn"])
                deleted += 1
                logger.info("  ✓ Deleted rule %s (priority=%s)", rule["RuleArn"], rule.get("Priority"))
            except ClientError as e:
                logger.warning("  Could not delete rule: %s", e)
    if not deleted:
        logger.info("  No /vault rules found")


def delete_ob_docs_target_group(elbv2) -> None:
    logger.info("[4/8] Deleting target group %s", TG_NAME)
    try:
        tgs = elbv2.describe_target_groups(Names=[TG_NAME])["TargetGroups"]
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "TargetGroupNotFound":
            logger.info("  Target group not found")
            return
        raise
    for tg in tgs:
        arn = tg["TargetGroupArn"]
        # Wait briefly if still draining from service delete
        for _ in range(12):
            try:
                elbv2.delete_target_group(TargetGroupArn=arn)
                logger.info("  ✓ Deleted %s", TG_NAME)
                return
            except ClientError as e:
                if e.response.get("Error", {}).get("Code") != "ResourceInUse":
                    logger.warning("  Could not delete TG: %s", e)
                    return
                logger.info("  TG still in use, waiting…")
                time.sleep(10)
        logger.warning("  Timed out waiting to delete %s", TG_NAME)


def delete_ecr_and_logs(ecr, logs) -> None:
    logger.info("[5/8] Deleting ECR %s + log group %s", ECR_NAME, LOG_GROUP)
    try:
        ecr.delete_repository(repositoryName=ECR_NAME, force=True)
        logger.info("  ✓ Deleted ECR %s", ECR_NAME)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "RepositoryNotFoundException":
            logger.warning("  ECR: %s", e)
        else:
            logger.info("  ECR not found")

    try:
        logs.delete_log_group(logGroupName=LOG_GROUP)
        logger.info("  ✓ Deleted log group %s", LOG_GROUP)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
            logger.warning("  Log group: %s", e)
        else:
            logger.info("  Log group not found")


def delete_vault_agent_secret(sm) -> None:
    logger.info("[6/8] Deleting secret %s", VAULT_AGENT_SECRET)
    try:
        sm.delete_secret(SecretId=VAULT_AGENT_SECRET, ForceDeleteWithoutRecovery=True)
        logger.info("  ✓ Deleted %s", VAULT_AGENT_SECRET)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
            logger.warning("  Secret: %s", e)
        else:
            logger.info("  Secret not found")


def empty_s3_prefix(s3, bucket: str, prefix: str) -> int:
    """Delete all object versions under prefix. Returns deleted count."""
    deleted = 0
    paginator = s3.get_paginator("list_object_versions")
    try:
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            to_delete: list[dict[str, str]] = []
            for obj in page.get("Versions") or []:
                to_delete.append({"Key": obj["Key"], "VersionId": obj["VersionId"]})
            for obj in page.get("DeleteMarkers") or []:
                to_delete.append({"Key": obj["Key"], "VersionId": obj["VersionId"]})
            for i in range(0, len(to_delete), 1000):
                batch = to_delete[i : i + 1000]
                if not batch:
                    continue
                s3.delete_objects(Bucket=bucket, Delete={"Objects": batch, "Quiet": True})
                deleted += len(batch)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in {"NoSuchBucket", "404"}:
            return 0
        # Fallback without versioning
        if code in {"InvalidArgument", "NotImplemented"}:
            paginator2 = s3.get_paginator("list_objects_v2")
            for page in paginator2.paginate(Bucket=bucket, Prefix=prefix):
                keys = [{"Key": o["Key"]} for o in page.get("Contents") or []]
                for i in range(0, len(keys), 1000):
                    batch = keys[i : i + 1000]
                    if batch:
                        s3.delete_objects(
                            Bucket=bucket, Delete={"Objects": batch, "Quiet": True}
                        )
                        deleted += len(batch)
            return deleted
        raise
    return deleted


def delete_s3_bucket_fully(s3, bucket: str) -> None:
    logger.info("  Emptying and deleting bucket %s …", bucket)
    try:
        n = empty_s3_prefix(s3, bucket, "")
        logger.info("  Removed %d object versions", n)
        s3.delete_bucket(Bucket=bucket)
        logger.info("  ✓ Deleted bucket %s", bucket)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") not in {"NoSuchBucket", "404"}:
            logger.warning("  Bucket delete: %s", e)


def delete_secret_if_exists(sm, name: str) -> None:
    try:
        sm.delete_secret(SecretId=name, ForceDeleteWithoutRecovery=True)
        logger.info("  ✓ Deleted secret %s", name)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
            logger.warning("  Secret %s: %s", name, e)


def delete_iam_role(iam, role_name: str) -> None:
    try:
        for p in iam.list_attached_role_policies(RoleName=role_name).get(
            "AttachedPolicies"
        ) or []:
            iam.detach_role_policy(RoleName=role_name, PolicyArn=p["PolicyArn"])
        for p in iam.list_role_policies(RoleName=role_name).get("PolicyNames") or []:
            iam.delete_role_policy(RoleName=role_name, PolicyName=p)
        iam.delete_role(RoleName=role_name)
        logger.info("  ✓ Deleted IAM role %s", role_name)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "NoSuchEntity":
            logger.warning("  IAM role %s: %s", role_name, e)


def delete_alb_fully(elbv2) -> Optional[str]:
    """Delete shared ALB + listeners. Returns VPC id if known."""
    vpc_id: Optional[str] = None
    try:
        alb = elbv2.describe_load_balancers(Names=[ALB_NAME])["LoadBalancers"][0]
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "LoadBalancerNotFound":
            logger.info("  ALB %s not found", ALB_NAME)
            return None
        raise
    vpc_id = alb.get("VpcId")
    alb_arn = alb["LoadBalancerArn"]
    for listener in elbv2.describe_listeners(LoadBalancerArn=alb_arn).get("Listeners") or []:
        try:
            elbv2.delete_listener(ListenerArn=listener["ListenerArn"])
            logger.info("  ✓ Deleted listener %s", listener["ListenerArn"])
        except ClientError as e:
            logger.warning("  Listener: %s", e)
    elbv2.delete_load_balancer(LoadBalancerArn=alb_arn)
    logger.info("  ✓ Deleted ALB %s", ALB_NAME)
    time.sleep(20)
    return vpc_id


def delete_vpc_by_id(ec2, vpc_id: str) -> None:
    """Best-effort delete of a VPC created for the shared stack (minimal install)."""
    logger.info("  Cleaning VPC %s …", vpc_id)
    try:
        # ENIs
        for eni in ec2.describe_network_interfaces(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
        ).get("NetworkInterfaces") or []:
            try:
                ec2.delete_network_interface(NetworkInterfaceId=eni["NetworkInterfaceId"])
            except ClientError:
                pass

        for sg in ec2.describe_security_groups(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
        ).get("SecurityGroups") or []:
            if sg.get("GroupName") == "default":
                continue
            try:
                ec2.delete_security_group(GroupId=sg["GroupId"])
                logger.info("  ✓ Deleted SG %s", sg.get("GroupName"))
            except ClientError as e:
                logger.warning("  SG %s: %s", sg.get("GroupId"), e)

        for subnet in ec2.describe_subnets(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
        ).get("Subnets") or []:
            try:
                ec2.delete_subnet(SubnetId=subnet["SubnetId"])
            except ClientError as e:
                logger.warning("  Subnet: %s", e)

        for rt in ec2.describe_route_tables(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
        ).get("RouteTables") or []:
            main = any(a.get("Main") for a in rt.get("Associations") or [])
            if main:
                continue
            try:
                ec2.delete_route_table(RouteTableId=rt["RouteTableId"])
            except ClientError as e:
                logger.warning("  Route table: %s", e)

        for igw in ec2.describe_internet_gateways(
            Filters=[{"Name": "attachment.vpc-id", "Values": [vpc_id]}]
        ).get("InternetGateways") or []:
            try:
                ec2.detach_internet_gateway(
                    InternetGatewayId=igw["InternetGatewayId"], VpcId=vpc_id
                )
                ec2.delete_internet_gateway(InternetGatewayId=igw["InternetGatewayId"])
            except ClientError as e:
                logger.warning("  IGW: %s", e)

        ec2.delete_vpc(VpcId=vpc_id)
        logger.info("  ✓ Deleted VPC %s", vpc_id)
    except ClientError as e:
        logger.warning("  VPC %s: %s", vpc_id, e)


def find_vpc_id(ec2) -> Optional[str]:
    vpcs = ec2.describe_vpcs(
        Filters=[{"Name": "tag:Name", "Values": [VPC_NAME]}]
    ).get("Vpcs") or []
    if vpcs:
        return vpcs[0]["VpcId"]
    return None


def delete_unshared_stack(
    *,
    ecs,
    elbv2,
    ec2,
    s3,
    sm,
    iam,
    region: str,
    account: str,
    bucket: str,
) -> None:
    """Delete shared-named resources when nothing else shares them."""
    logger.info("[7/8] Deleting unshared agentic-work-compatible stack")

    # Cluster (ob-docs service already gone)
    try:
        ecs.delete_cluster(cluster=CLUSTER)
        logger.info("  ✓ Deleted cluster %s", CLUSTER)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "ClusterNotFoundException":
            logger.warning("  Cluster: %s", e)

    vpc_id = delete_alb_fully(elbv2) or find_vpc_id(ec2)
    if vpc_id:
        # Remaining TGs in VPC that might block (should be gone)
        try:
            for tg in elbv2.describe_target_groups().get("TargetGroups") or []:
                if tg.get("VpcId") == vpc_id and PROJECT in tg.get("TargetGroupName", ""):
                    try:
                        elbv2.delete_target_group(TargetGroupArn=tg["TargetGroupArn"])
                    except ClientError:
                        pass
        except ClientError:
            pass
        time.sleep(5)
        delete_vpc_by_id(ec2, vpc_id)

    delete_secret_if_exists(sm, ORIGIN_HEADER_SECRET)
    delete_secret_if_exists(sm, SESSION_SECRET)

    delete_iam_role(iam, f"role-ecs-task-for-{SHARED}-{region}")
    delete_iam_role(iam, f"role-ecs-execution-for-{SHARED}-{region}")

    if bucket:
        delete_s3_bucket_fully(s3, bucket)
    else:
        delete_s3_bucket_fully(s3, default_bucket_name(account, region))


def purge_vault_prefix(s3, bucket: str) -> None:
    logger.info("[7/8] Purging shared bucket prefix vault/ (bucket kept)")
    try:
        n = empty_s3_prefix(s3, bucket, "vault/")
        logger.info("  ✓ Removed %d objects under vault/", n)
    except ClientError as e:
        logger.warning("  vault/ purge: %s", e)


def clean_local_config(cfg: dict[str, Any]) -> None:
    logger.info("[8/8] Cleaning deploy metadata in config.json")
    if not CONFIG_PATH.is_file():
        logger.info("  No config.json")
        return
    drop = {
        "latest_image_tag",
        "ecr_repository_uri",
        "ecs_service",
        "ecs_cluster",
        "target_group",
        "alb_dns",
    }
    changed = False
    for k in drop:
        if k in cfg:
            del cfg[k]
            changed = True
    if changed:
        CONFIG_PATH.write_text(
            json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        logger.info("  ✓ Removed deploy keys from config.json")
    else:
        logger.info("  Nothing to clean")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Uninstall ob-docs AWS resources (keeps shared agentic-work infra when in use)"
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt",
    )
    parser.add_argument(
        "--purge-vault-prefix",
        action="store_true",
        help="When stack is shared, also delete s3://{bucket}/vault/ objects (bucket kept)",
    )
    parser.add_argument(
        "--force-unshared",
        action="store_true",
        help="Delete shared-named ALB/VPC/cluster/S3/roles even if sharing is detected (dangerous)",
    )
    args = parser.parse_args()

    cfg = load_cfg()
    region = str(cfg.get("region") or DEFAULT_REGION)
    c = clients(region)
    account = str(cfg.get("accountId") or c["sts"].get_caller_identity()["Account"])
    bucket = str(cfg.get("s3_bucket") or default_bucket_name(account, region))

    logger.info("=" * 60)
    logger.info("ob-docs Infrastructure Cleanup")
    logger.info("=" * 60)
    logger.info("Project: %s", PROJECT)
    logger.info("Shared:  %s", SHARED)
    logger.info("Region:  %s", region)
    logger.info("Account: %s", account)
    logger.info("Bucket:  %s", bucket)
    logger.info("=" * 60)

    shared = is_stack_shared(c["ecs"])
    if args.force_unshared and shared:
        logger.warning("--force-unshared: will delete shared-named stack despite sharing")
        shared = False

    if not args.yes:
        logger.info("")
        logger.info("Will delete ob-docs: service, TG, /vault rules, ECR, logs, vault-agent-token")
        if shared:
            logger.info("Shared stack KEEP (agentic-work still in use)")
            if args.purge_vault_prefix:
                logger.info("Also purge s3://%s/vault/", bucket)
        else:
            logger.info(
                "Shared stack DELETE (ALB/VPC/cluster/S3/IAM/origin+session secrets)"
            )
        response = input("\nAre you sure you want to continue? (yes/no): ")
        if response.lower() != "yes":
            logger.info("Uninstallation cancelled.")
            return 0

    start = time.time()
    try:
        delete_ob_docs_ecs_service(c["ecs"])
        deregister_task_definitions(c["ecs"])
        delete_vault_listener_rules(c["elbv2"])
        delete_ob_docs_target_group(c["elbv2"])
        delete_ecr_and_logs(c["ecr"], c["logs"])
        delete_vault_agent_secret(c["sm"])

        if shared:
            if args.purge_vault_prefix:
                purge_vault_prefix(c["s3"], bucket)
            else:
                logger.info("[7/8] Skipping shared S3/ALB/VPC/cluster/IAM/secrets")
        else:
            delete_unshared_stack(
                ecs=c["ecs"],
                elbv2=c["elbv2"],
                ec2=c["ec2"],
                s3=c["s3"],
                sm=c["sm"],
                iam=c["iam"],
                region=region,
                account=account,
                bucket=bucket,
            )

        clean_local_config(cfg)

        elapsed = time.time() - start
        logger.info("")
        logger.info("=" * 60)
        logger.info("Cleanup completed successfully")
        logger.info("Total time: %.2f minutes", elapsed / 60)
        logger.info("=" * 60)
        return 0
    except Exception as e:
        logger.exception("Cleanup failed: %s", e)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
