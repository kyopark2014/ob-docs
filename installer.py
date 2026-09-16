#!/usr/bin/env python3
"""Deploy ob-docs onto the shared agentic-work ALB / ECS / S3 stack.

Creates (idempotent):
  - ECR repository
  - ALB target group TG-for-ob-docs (port 8502)
  - Listener rule: path /vault* + CloudFront origin header → ob-docs TG
  - ECS task definition + Fargate service on cluster-for-agentic-work
  - Seeds s3://{bucket}/vault/ from data/vault/

Usage:
  python installer.py
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ob-docs-installer")

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"

PROJECT = "ob-docs"
SHARED = "agentic-work"
REGION = "us-west-2"
ACCOUNT = "262976740991"
CLUSTER = f"cluster-for-{SHARED}"
ALB_NAME = f"alb-for-{SHARED}"
SERVICE_NAME = f"service-for-{PROJECT}"
TASK_FAMILY = f"task-for-{PROJECT}"
TG_NAME = f"TG-for-{PROJECT}"
ECR_NAME = f"ecr-for-{PROJECT}"
CONTAINER_PORT = 8502
LOG_GROUP = f"/ecs/app-for-{PROJECT}"
ORIGIN_HEADER_SECRET = f"{SHARED}/cloudfront-alb-origin-header"
SESSION_SECRET = f"{SHARED}/session-signing-key"
VAULT_AGENT_SECRET = f"{SHARED}/vault-agent-token"
EXEC_ROLE = f"role-ecs-execution-for-{SHARED}-{REGION}"
TASK_ROLE = f"role-ecs-task-for-{SHARED}-{REGION}"


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg: dict[str, Any]) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def clients():
    return {
        "ecr": boto3.client("ecr", region_name=REGION),
        "ecs": boto3.client("ecs", region_name=REGION),
        "elbv2": boto3.client("elbv2", region_name=REGION),
        "ec2": boto3.client("ec2", region_name=REGION),
        "logs": boto3.client("logs", region_name=REGION),
        "s3": boto3.client("s3", region_name=REGION),
        "sm": boto3.client("secretsmanager", region_name=REGION),
        "sts": boto3.client("sts", region_name=REGION),
    }


def ensure_ecr(ecr) -> str:
    try:
        resp = ecr.describe_repositories(repositoryNames=[ECR_NAME])
        uri = resp["repositories"][0]["repositoryUri"]
        logger.info("ECR exists: %s", uri)
        return uri
    except ClientError as e:
        if e.response["Error"]["Code"] != "RepositoryNotFoundException":
            raise
    resp = ecr.create_repository(
        repositoryName=ECR_NAME,
        imageScanningConfiguration={"scanOnPush": True},
        tags=[{"Key": "Name", "Value": ECR_NAME}],
    )
    uri = resp["repository"]["repositoryUri"]
    logger.info("Created ECR: %s", uri)
    return uri


def docker_login(ecr, repo_uri: str) -> None:
    registry = repo_uri.split("/")[0]
    pw = ecr.get_authorization_token()["authorizationData"][0]["authorizationToken"]
    # token is base64 user:pass — docker login expects password via stdin with AWS username
    import base64

    user, token = base64.b64decode(pw).decode("utf-8").split(":", 1)
    proc = subprocess.run(
        ["docker", "login", "--username", user, "--password-stdin", registry],
        input=token,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"docker login failed: {proc.stderr}")


def build_and_push(repo_uri: str, tag: str) -> str:
    image = f"{repo_uri}:{tag}"
    logger.info("Building image %s (linux/arm64)...", image)
    # Prefer buildx push directly
    cmd = [
        "docker",
        "buildx",
        "build",
        "--platform",
        "linux/arm64",
        "-t",
        image,
        "--push",
        str(ROOT),
    ]
    proc = subprocess.run(cmd, check=False)
    if proc.returncode != 0:
        logger.warning("buildx failed; falling back to docker build + push")
        subprocess.run(
            ["docker", "build", "--platform", "linux/arm64", "-t", image, str(ROOT)],
            check=True,
        )
        subprocess.run(["docker", "push", image], check=True)
    # also tag latest
    latest = f"{repo_uri}:latest"
    subprocess.run(["docker", "buildx", "imagetools", "create", "-t", latest, image], check=False)
    logger.info("Pushed %s", image)
    return image


def seed_vault_to_s3(s3, bucket: str) -> int:
    vault = ROOT / "data" / "vault"
    uploaded = 0
    for path in vault.rglob("*"):
        if not path.is_file():
            continue
        if path.name == ".DS_Store":
            continue
        # skip regenerable cache
        if ".vault/cache" in path.as_posix():
            continue
        rel = path.relative_to(vault).as_posix()
        key = f"vault/{rel}"
        s3.upload_file(str(path), bucket, key)
        uploaded += 1
        logger.info("  ↑ s3://%s/%s", bucket, key)
    return uploaded


def ensure_log_group(logs) -> None:
    try:
        logs.create_log_group(logGroupName=LOG_GROUP)
        logger.info("Created log group %s", LOG_GROUP)
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceAlreadyExistsException":
            raise
        logger.info("Log group exists: %s", LOG_GROUP)


def get_secret_string(sm, name: str) -> str:
    return (sm.get_secret_value(SecretId=name).get("SecretString") or "").strip()


def get_secret_arn(sm, name: str) -> str:
    return sm.describe_secret(SecretId=name)["ARN"]


def ensure_vault_agent_token(sm) -> str:
    """Create or reuse shared vault-agent-token; return secret ARN."""
    import secrets as py_secrets

    try:
        return sm.describe_secret(SecretId=VAULT_AGENT_SECRET)["ARN"]
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
    value = py_secrets.token_urlsafe(32)
    resp = sm.create_secret(
        Name=VAULT_AGENT_SECRET,
        SecretString=value,
        Description="Shared HMAC token for AgentCore my-vaults → ob-docs auth",
    )
    logger.info("Created secret %s", VAULT_AGENT_SECRET)
    return resp["ARN"]


def ensure_target_group(elbv2, vpc_id: str) -> str:
    try:
        tgs = elbv2.describe_target_groups(Names=[TG_NAME])
        arn = tgs["TargetGroups"][0]["TargetGroupArn"]
        logger.info("Target group exists: %s", TG_NAME)
        return arn
    except ClientError as e:
        if e.response["Error"]["Code"] != "TargetGroupNotFound":
            raise
    resp = elbv2.create_target_group(
        Name=TG_NAME,
        Protocol="HTTP",
        Port=CONTAINER_PORT,
        VpcId=vpc_id,
        TargetType="ip",
        HealthCheckProtocol="HTTP",
        HealthCheckPath="/vault/api/health",
        HealthCheckIntervalSeconds=30,
        HealthCheckTimeoutSeconds=5,
        HealthyThresholdCount=2,
        UnhealthyThresholdCount=3,
        Matcher={"HttpCode": "200"},
    )
    arn = resp["TargetGroups"][0]["TargetGroupArn"]
    logger.info("Created target group %s", TG_NAME)
    try:
        elbv2.modify_target_group_attributes(
            TargetGroupArn=arn,
            Attributes=[
                {"Key": "stickiness.enabled", "Value": "true"},
                {"Key": "stickiness.type", "Value": "app_cookie"},
                {"Key": "stickiness.app_cookie.cookie_name", "Value": "agent_user_id"},
                {"Key": "stickiness.app_cookie.duration_seconds", "Value": "86400"},
            ],
        )
    except ClientError as e:
        logger.warning("Could not enable stickiness: %s", e)
    return arn


def ensure_listener_rule(elbv2, listener_arn: str, tg_arn: str, origin_header: str) -> str:
    """Path /vault* + origin header → ob-docs TG.

    Must be a *higher* priority (lower number) than the agentic-work catch-all
    header-only rule, otherwise /vault never reaches ob-docs.

    Prefer priority 1 so agentic-work's header-only rules (often 4/5/10) cannot
    steal /vault traffic. Also refuse to treat header-only rules as the vault rule.
    """
    rules = elbv2.describe_rules(ListenerArn=listener_arn)["Rules"]
    vault_rule = None
    for rule in rules:
        if rule.get("Priority") == "default":
            continue
        conds = rule.get("Conditions") or []
        has_vault_path = False
        for c in conds:
            if c.get("Field") != "path-pattern":
                continue
            values = c.get("Values") or []
            if any(str(v) == "/vault" or str(v).startswith("/vault/") or str(v) == "/vault/*" for v in values):
                has_vault_path = True
                break
        if has_vault_path:
            vault_rule = rule
            break

    desired_priority = 1
    used = {
        int(r["Priority"])
        for r in rules
        if r.get("Priority", "default").isdigit() and r is not vault_rule
    }
    while desired_priority in used:
        desired_priority += 1
        if desired_priority > 10:
            raise RuntimeError("No free ALB listener priority for /vault rule")

    conditions = [
        {
            "Field": "path-pattern",
            "Values": ["/vault", "/vault/*"],
        },
        {
            "Field": "http-header",
            "HttpHeaderConfig": {
                "HttpHeaderName": "X-Custom-Header",
                "Values": [origin_header],
            },
        },
    ]
    actions = [{"Type": "forward", "TargetGroupArn": tg_arn}]

    if vault_rule:
        elbv2.modify_rule(
            RuleArn=vault_rule["RuleArn"],
            Conditions=conditions,
            Actions=actions,
        )
        current = vault_rule.get("Priority")
        if str(current) != str(desired_priority):
            try:
                elbv2.set_rule_priorities(
                    RulePriorities=[
                        {"RuleArn": vault_rule["RuleArn"], "Priority": desired_priority}
                    ]
                )
                logger.info(
                    "Updated /vault listener rule %s priority %s → %s",
                    vault_rule["RuleArn"],
                    current,
                    desired_priority,
                )
            except ClientError as e:
                logger.warning(
                    "Could not move /vault rule to priority %s (kept %s): %s",
                    desired_priority,
                    current,
                    e,
                )
        else:
            logger.info("Updated existing /vault listener rule %s", vault_rule["RuleArn"])
        return vault_rule["RuleArn"]

    resp = elbv2.create_rule(
        ListenerArn=listener_arn,
        Priority=desired_priority,
        Conditions=conditions,
        Actions=actions,
    )
    arn = resp["Rules"][0]["RuleArn"]
    logger.info("Created listener rule priority=%s → %s", desired_priority, TG_NAME)
    return arn


def register_task_definition(
    ecs,
    image_uri: str,
    cfg: dict[str, Any],
    session_secret_arn: str,
    vault_agent_secret_arn: str,
) -> str:
    app_config = {
        "projectName": PROJECT,
        "sharedProjectName": SHARED,
        "accountId": ACCOUNT,
        "region": REGION,
        "s3_bucket": cfg["s3_bucket"],
        "s3_arn": cfg.get("s3_arn", f"arn:aws:s3:::{cfg['s3_bucket']}"),
        "s3_files_vault_prefix": "vault/",
        "s3_files_vault_mount_path": "/mnt/vault",
        "google_client_id": cfg.get("google_client_id", ""),
        "sharing_url": cfg.get("sharing_url") or cfg.get("agentic_work_url"),
        "agentic_work_url": cfg.get("agentic_work_url") or cfg.get("sharing_url"),
    }
    container = {
        "name": "app",
        "image": image_uri,
        "essential": True,
        "portMappings": [{"containerPort": CONTAINER_PORT, "protocol": "tcp"}],
        "environment": [
            {"name": "APP_CONFIG_JSON", "value": json.dumps(app_config)},
            {"name": "VAULT_S3_ENABLE", "value": "1"},
            {"name": "VAULT_DIR", "value": "/app/data/vault"},
            {"name": "SHARED_PROJECT_NAME", "value": SHARED},
        ],
        "secrets": [
            {"name": "SESSION_SIGNING_KEY", "valueFrom": session_secret_arn},
            {"name": "VAULT_AGENT_TOKEN", "valueFrom": vault_agent_secret_arn},
        ],
        "logConfiguration": {
            "logDriver": "awslogs",
            "options": {
                "awslogs-group": LOG_GROUP,
                "awslogs-region": REGION,
                "awslogs-stream-prefix": "ecs",
            },
        },
        "healthCheck": {
            "command": [
                "CMD-SHELL",
                f"curl -f http://localhost:{CONTAINER_PORT}/vault/api/health || exit 1",
            ],
            "interval": 30,
            "timeout": 5,
            "retries": 3,
            "startPeriod": 60,
        },
    }
    resp = ecs.register_task_definition(
        family=TASK_FAMILY,
        networkMode="awsvpc",
        requiresCompatibilities=["FARGATE"],
        cpu="512",
        memory="1024",
        executionRoleArn=f"arn:aws:iam::{ACCOUNT}:role/{EXEC_ROLE}",
        taskRoleArn=f"arn:aws:iam::{ACCOUNT}:role/{TASK_ROLE}",
        containerDefinitions=[container],
        runtimePlatform={"cpuArchitecture": "ARM64", "operatingSystemFamily": "LINUX"},
        tags=[{"key": "Name", "value": TASK_FAMILY}],
    )
    arn = resp["taskDefinition"]["taskDefinitionArn"]
    logger.info("Registered task definition %s", arn)
    return arn


def ensure_service(
    ecs,
    elbv2,
    task_def_arn: str,
    tg_arn: str,
    subnets: list[str],
    security_groups: list[str],
) -> None:
    try:
        desc = ecs.describe_services(cluster=CLUSTER, services=[SERVICE_NAME])
        services = [s for s in desc.get("services", []) if s.get("status") != "INACTIVE"]
    except ClientError:
        services = []

    if services:
        ecs.update_service(
            cluster=CLUSTER,
            service=SERVICE_NAME,
            taskDefinition=task_def_arn,
            desiredCount=1,
            forceNewDeployment=True,
            deploymentConfiguration={
                "minimumHealthyPercent": 100,
                "maximumPercent": 200,
            },
        )
        logger.info("Updated ECS service %s", SERVICE_NAME)
    else:
        ecs.create_service(
            cluster=CLUSTER,
            serviceName=SERVICE_NAME,
            taskDefinition=task_def_arn,
            desiredCount=1,
            launchType="FARGATE",
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets": subnets,
                    "securityGroups": security_groups,
                    "assignPublicIp": "DISABLED",
                }
            },
            loadBalancers=[
                {
                    "targetGroupArn": tg_arn,
                    "containerName": "app",
                    "containerPort": CONTAINER_PORT,
                }
            ],
            deploymentConfiguration={
                "minimumHealthyPercent": 100,
                "maximumPercent": 200,
            },
            healthCheckGracePeriodSeconds=120,
            tags=[{"key": "Name", "value": SERVICE_NAME}],
        )
        logger.info("Created ECS service %s", SERVICE_NAME)


def wait_service(ecs, elbv2, tg_arn: str, timeout: int = 600) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        svc = ecs.describe_services(cluster=CLUSTER, services=[SERVICE_NAME])["services"][0]
        running = svc.get("runningCount", 0)
        desired = svc.get("desiredCount", 0)
        health = elbv2.describe_target_health(TargetGroupArn=tg_arn)
        healthy = sum(
            1
            for t in health.get("TargetHealthDescriptions", [])
            if t.get("TargetHealth", {}).get("State") == "healthy"
        )
        logger.info(
            "  ... ECS running=%s/%s healthy_targets=%s",
            running,
            desired,
            healthy,
        )
        if running >= 1 and healthy >= 1:
            logger.info("Service is healthy")
            return
        # surface stop reasons
        for event in (svc.get("events") or [])[:2]:
            logger.info("  event: %s", event.get("message", "")[:160])
        time.sleep(15)
    raise TimeoutError("Timed out waiting for ob-docs ECS service")


def ensure_sg_ingress(ec2, ecs_sg: str, alb_sg: str) -> None:
    """Allow ALB → ob-docs container port."""
    try:
        ec2.authorize_security_group_ingress(
            GroupId=ecs_sg,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": CONTAINER_PORT,
                    "ToPort": CONTAINER_PORT,
                    "UserIdGroupPairs": [
                        {
                            "GroupId": alb_sg,
                            "Description": "ALB to ob-docs",
                        }
                    ],
                }
            ],
        )
        logger.info("Opened SG ingress %s ← %s :%s", ecs_sg, alb_sg, CONTAINER_PORT)
    except ClientError as e:
        if e.response["Error"]["Code"] != "InvalidPermission.Duplicate":
            raise
        logger.info("SG ingress already present for :%s", CONTAINER_PORT)


def main() -> int:
    if shutil_which("docker") is None:
        logger.error("Docker is required")
        return 1

    cfg = load_config()
    cfg.setdefault("sharing_url", "https://cowork.my-agentic-ai.click")
    cfg.setdefault("agentic_work_url", cfg["sharing_url"])
    bucket = cfg["s3_bucket"]

    c = clients()
    ident = c["sts"].get_caller_identity()
    logger.info("AWS account=%s arn=%s", ident.get("Account"), ident.get("Arn"))

    # Discover shared ALB / networking from existing agentic-work service
    aw = c["ecs"].describe_services(
        cluster=CLUSTER, services=[f"service-for-{SHARED}"]
    )["services"][0]
    subnets = aw["networkConfiguration"]["awsvpcConfiguration"]["subnets"]
    sgs = aw["networkConfiguration"]["awsvpcConfiguration"]["securityGroups"]
    aw_tg = aw["loadBalancers"][0]["targetGroupArn"]
    vpc_id = c["elbv2"].describe_target_groups(TargetGroupArns=[aw_tg])["TargetGroups"][0][
        "VpcId"
    ]
    alb = c["elbv2"].describe_load_balancers(Names=[ALB_NAME])["LoadBalancers"][0]
    alb_sg = alb["SecurityGroups"][0]
    listener = c["elbv2"].describe_listeners(LoadBalancerArn=alb["LoadBalancerArn"])[
        "Listeners"
    ][0]["ListenerArn"]

    ensure_sg_ingress(c["ec2"], sgs[0], alb_sg)

    origin_header = get_secret_string(c["sm"], ORIGIN_HEADER_SECRET)
    if not origin_header:
        raise RuntimeError(f"Empty origin header secret: {ORIGIN_HEADER_SECRET}")
    session_arn = get_secret_arn(c["sm"], SESSION_SECRET)
    vault_agent_arn = ensure_vault_agent_token(c["sm"])

    logger.info("[1/6] ECR")
    repo_uri = ensure_ecr(c["ecr"])
    docker_login(c["ecr"], repo_uri)

    tag = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    logger.info("[2/6] Build & push image tag=%s", tag)
    image_uri = build_and_push(repo_uri, tag)

    logger.info("[3/6] Seed vault → s3://%s/vault/", bucket)
    n = seed_vault_to_s3(c["s3"], bucket)
    logger.info("Uploaded %d vault files", n)

    logger.info("[4/6] Target group + listener rule")
    ensure_log_group(c["logs"])
    tg_arn = ensure_target_group(c["elbv2"], vpc_id)
    ensure_listener_rule(c["elbv2"], listener, tg_arn, origin_header)

    logger.info("[5/6] Task definition + service")
    task_arn = register_task_definition(
        c["ecs"], image_uri, cfg, session_arn, vault_agent_arn
    )
    ensure_service(c["ecs"], c["elbv2"], task_arn, tg_arn, subnets, sgs)

    logger.info("[6/6] Wait for healthy")
    wait_service(c["ecs"], c["elbv2"], tg_arn)

    cfg["latest_image_tag"] = tag
    cfg["ecr_repository_uri"] = repo_uri
    cfg["ecs_service"] = SERVICE_NAME
    cfg["ecs_cluster"] = CLUSTER
    cfg["target_group"] = TG_NAME
    save_config(cfg)

    url = f"{cfg.get('sharing_url', '').rstrip('/')}/vault"
    logger.info("Deployed: %s", url)
    print(url)
    return 0


def shutil_which(cmd: str) -> Optional[str]:
    from shutil import which

    return which(cmd)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        logger.exception("Deploy failed")
        raise SystemExit(1)
