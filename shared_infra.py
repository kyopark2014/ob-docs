#!/usr/bin/env python3
"""Shared agentic-work-compatible infrastructure helpers for ob-docs.

Naming and secrets match ``agentic-work/installer.py`` so both projects can share
the same ALB / ECS cluster / S3 bucket / Secrets Manager entries.

When ``../agentic-work/application/config.json`` exists it is merged for keys
like ``google_client_id`` / ``sharing_url``. When shared AWS resources are
missing, this module creates them (idempotent) so ob-docs can run without a
prior agentic-work install.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger("ob-docs-shared-infra")

ROOT = Path(__file__).resolve().parent
AGENTIC_WORK_ROOT = ROOT.parent / "agentic-work"
AGENTIC_WORK_CONFIG = AGENTIC_WORK_ROOT / "application" / "config.json"

SHARED = "agentic-work"
PROJECT = "ob-docs"
DEFAULT_REGION = "us-west-2"

CLUSTER = f"cluster-for-{SHARED}"
ALB_NAME = f"alb-for-{SHARED}"
VPC_NAME = f"vpc-for-{SHARED}"
ALB_SG_NAME = f"alb-sg-for-{SHARED}"
ECS_SG_NAME = f"ecs-sg-for-{SHARED}"
ORIGIN_HEADER_SECRET = f"{SHARED}/cloudfront-alb-origin-header"
SESSION_SECRET = f"{SHARED}/session-signing-key"
CUSTOM_HEADER_NAME = "X-Custom-Header"

# Keys copied from a local agentic-work config when present.
_MERGE_FROM_AGENTIC_KEYS = (
    "s3_bucket",
    "s3_arn",
    "sharing_url",
    "google_client_id",
    "region",
    "accountId",
    "hybrid_graph_search",
)


@dataclass
class NetworkInfo:
    vpc_id: str
    subnets: list[str]
    security_groups: list[str]
    alb_arn: str
    alb_dns: str
    alb_sg: str
    listener_arn: str
    assign_public_ip: str  # "ENABLED" | "DISABLED"


def _sts_identity(region: str) -> tuple[str, str]:
    sts = boto3.client("sts", region_name=region)
    ident = sts.get_caller_identity()
    return str(ident["Account"]), str(ident.get("Arn") or "")


def default_bucket_name(account: str, region: str) -> str:
    """Same pattern as agentic-work/installer.py ``bucket_name``."""
    return f"storage-for-{SHARED}-{account}-{region}"


def load_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return {}


def bootstrap_config(config_path: Path) -> dict[str, Any]:
    """Load or create config.json; merge agentic-work config + AWS identity defaults."""
    cfg = load_json_if_exists(config_path)
    aw_cfg = load_json_if_exists(AGENTIC_WORK_CONFIG)
    if aw_cfg:
        logger.info("Merging keys from %s", AGENTIC_WORK_CONFIG)
        for key in _MERGE_FROM_AGENTIC_KEYS:
            if key in aw_cfg and aw_cfg[key] not in (None, "") and not cfg.get(key):
                cfg[key] = aw_cfg[key]

    region = str(cfg.get("region") or DEFAULT_REGION).strip() or DEFAULT_REGION
    account = str(cfg.get("accountId") or "").strip()
    if not account:
        account, _ = _sts_identity(region)

    cfg.setdefault("projectName", PROJECT)
    cfg.setdefault("sharedProjectName", SHARED)
    cfg["region"] = region
    cfg["accountId"] = account
    cfg.setdefault("s3_files_vault_prefix", "vault/")
    cfg.setdefault("s3_files_vault_mount_path", "/mnt/vault")

    if not cfg.get("s3_bucket"):
        cfg["s3_bucket"] = default_bucket_name(account, region)
        logger.info("config s3_bucket default → %s", cfg["s3_bucket"])
    cfg.setdefault("s3_arn", f"arn:aws:s3:::{cfg['s3_bucket']}")

    if cfg.get("sharing_url") and not cfg.get("agentic_work_url"):
        cfg["agentic_work_url"] = cfg["sharing_url"]
    if cfg.get("agentic_work_url") and not cfg.get("sharing_url"):
        cfg["sharing_url"] = cfg["agentic_work_url"]

    config_path.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    logger.info("Wrote bootstrap config → %s", config_path)
    return cfg


def ensure_s3_bucket(s3, bucket: str, region: str) -> str:
    """Create shared storage bucket if missing (agentic-work naming)."""
    try:
        s3.head_bucket(Bucket=bucket)
        logger.info("S3 bucket exists: %s", bucket)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code not in {"404", "NoSuchBucket", "NotFound", "403", "AccessDenied"}:
            raise
        try:
            logger.info("Creating S3 bucket %s …", bucket)
            if region == "us-east-1":
                s3.create_bucket(Bucket=bucket)
            else:
                s3.create_bucket(
                    Bucket=bucket,
                    CreateBucketConfiguration={"LocationConstraint": region},
                )
        except ClientError as create_err:
            err = create_err.response.get("Error", {}).get("Code", "")
            if err not in {"BucketAlreadyExists", "BucketAlreadyOwnedByYou"}:
                raise
            logger.info("S3 bucket already owned: %s", bucket)

    try:
        s3.put_public_access_block(
            Bucket=bucket,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        )
    except ClientError as e:
        logger.warning("public access block: %s", e)

    try:
        s3.put_bucket_cors(
            Bucket=bucket,
            CORSConfiguration={
                "CORSRules": [
                    {
                        "AllowedHeaders": ["*"],
                        "AllowedMethods": ["GET", "POST", "PUT"],
                        "AllowedOrigins": ["*"],
                    }
                ]
            },
        )
    except ClientError as e:
        logger.warning("bucket CORS: %s", e)

    try:
        s3.put_bucket_versioning(
            Bucket=bucket,
            VersioningConfiguration={"Status": "Enabled"},
        )
    except ClientError as e:
        logger.warning("bucket versioning: %s", e)

    try:
        s3.put_object(Bucket=bucket, Key="vault/", Body=b"")
    except ClientError as e:
        logger.warning("vault/ prefix: %s", e)

    return bucket


def ensure_origin_header_secret(sm) -> str:
    """CloudFront→ALB origin header (agentic-work/cloudfront-alb-origin-header)."""
    try:
        current = (
            sm.get_secret_value(SecretId=ORIGIN_HEADER_SECRET).get("SecretString") or ""
        ).strip()
        if current:
            logger.info("Reusing origin header secret %s", ORIGIN_HEADER_SECRET)
            return current
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise

    value = secrets.token_urlsafe(32)
    try:
        sm.create_secret(
            Name=ORIGIN_HEADER_SECRET,
            Description=(
                f"CloudFront to ALB origin verification header ({CUSTOM_HEADER_NAME})"
            ),
            SecretString=value,
            Tags=[
                {"Key": "Name", "Value": ORIGIN_HEADER_SECRET},
                {"Key": "Project", "Value": SHARED},
            ],
        )
        logger.info("Created origin header secret %s", ORIGIN_HEADER_SECRET)
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceExistsException":
            raise
        value = (
            sm.get_secret_value(SecretId=ORIGIN_HEADER_SECRET).get("SecretString") or ""
        ).strip()
    return value


def ensure_session_signing_key(sm) -> str:
    """HMAC session cookie key shared with agentic-work."""
    try:
        current = (
            sm.get_secret_value(SecretId=SESSION_SECRET).get("SecretString") or ""
        ).strip()
        if current:
            logger.info("Reusing session signing key %s", SESSION_SECRET)
            return current
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise

    value = secrets.token_urlsafe(32)
    try:
        sm.create_secret(
            Name=SESSION_SECRET,
            Description=f"HMAC signing key for {SHARED} / {PROJECT} session cookies",
            SecretString=value,
            Tags=[
                {"Key": "Name", "Value": SESSION_SECRET},
                {"Key": "Project", "Value": SHARED},
            ],
        )
        logger.info("Created session signing key %s", SESSION_SECRET)
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceExistsException":
            raise
        value = (
            sm.get_secret_value(SecretId=SESSION_SECRET).get("SecretString") or ""
        ).strip()
    return value


def _put_role_policy(iam, role_name: str, policy_name: str, document: dict[str, Any]) -> None:
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=policy_name,
        PolicyDocument=json.dumps(document),
    )


def ensure_ecs_roles(iam, account: str, region: str, bucket: str) -> dict[str, str]:
    """Create ECS task/execution roles using agentic-work naming (if missing)."""
    task_role = f"role-ecs-task-for-{SHARED}-{region}"
    exec_role = f"role-ecs-execution-for-{SHARED}-{region}"
    assume = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }

    def _ensure_role(name: str, managed: Optional[list[str]] = None) -> str:
        try:
            arn = iam.get_role(RoleName=name)["Role"]["Arn"]
            logger.info("IAM role exists: %s", name)
        except ClientError as e:
            if e.response["Error"]["Code"] != "NoSuchEntity":
                raise
            arn = iam.create_role(
                RoleName=name,
                AssumeRolePolicyDocument=json.dumps(assume),
                Description=f"ECS role for {SHARED} (created by ob-docs installer)",
                Tags=[
                    {"Key": "Name", "Value": name},
                    {"Key": "Project", "Value": SHARED},
                ],
            )["Role"]["Arn"]
            logger.info("Created IAM role %s", name)
            time.sleep(8)
        for policy_arn in managed or []:
            try:
                iam.attach_role_policy(RoleName=name, PolicyArn=policy_arn)
            except ClientError as e:
                logger.warning("attach %s: %s", policy_arn, e)
        return arn

    task_arn = _ensure_role(task_role)
    exec_arn = _ensure_role(
        exec_role,
        managed=["arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"],
    )

    bucket_arn = f"arn:aws:s3:::{bucket}"
    _put_role_policy(
        iam,
        task_role,
        f"ecs-task-s3-vault-for-{SHARED}",
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "VaultBucketList",
                    "Effect": "Allow",
                    "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
                    "Resource": [bucket_arn],
                },
                {
                    "Sid": "VaultObjectRW",
                    "Effect": "Allow",
                    "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                    "Resource": [f"{bucket_arn}/*"],
                },
            ],
        },
    )

    secret_arns = [f"arn:aws:secretsmanager:{region}:{account}:secret:{SHARED}/*"]
    secrets_doc = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "ReadProjectSecrets",
                "Effect": "Allow",
                "Action": ["secretsmanager:GetSecretValue"],
                "Resource": secret_arns,
            }
        ],
    }
    _put_role_policy(iam, exec_role, f"ecs-execution-secrets-for-{SHARED}", secrets_doc)
    _put_role_policy(iam, task_role, f"ecs-task-secrets-for-{SHARED}", secrets_doc)

    return {"task_role_arn": task_arn, "execution_role_arn": exec_arn}


def ensure_ecs_cluster(ecs) -> str:
    try:
        desc = ecs.describe_clusters(clusters=[CLUSTER])["clusters"]
        if desc and desc[0].get("status") == "ACTIVE":
            logger.info("ECS cluster exists: %s", CLUSTER)
            return CLUSTER
    except ClientError:
        pass
    ecs.create_cluster(
        clusterName=CLUSTER,
        capacityProviders=["FARGATE", "FARGATE_SPOT"],
        defaultCapacityProviderStrategy=[{"capacityProvider": "FARGATE", "weight": 1}],
        tags=[{"key": "Name", "value": CLUSTER}, {"key": "Project", "value": SHARED}],
    )
    logger.info("Created ECS cluster %s", CLUSTER)
    return CLUSTER


def _classify_subnets(ec2, subnet_ids: list[str]) -> tuple[list[str], list[str]]:
    if not subnet_ids:
        return [], []
    rts = ec2.describe_route_tables(
        Filters=[{"Name": "association.subnet-id", "Values": subnet_ids}]
    )["RouteTables"]
    public_ids: set[str] = set()
    for rt in rts:
        has_igw = any(
            (r.get("GatewayId") or "").startswith("igw-") for r in rt.get("Routes", [])
        )
        if not has_igw:
            continue
        for assoc in rt.get("Associations", []):
            sid = assoc.get("SubnetId")
            if sid:
                public_ids.add(sid)
    detail = ec2.describe_subnets(SubnetIds=subnet_ids)["Subnets"]
    for sn in detail:
        if sn.get("MapPublicIpOnLaunch"):
            public_ids.add(sn["SubnetId"])
    public = [s for s in subnet_ids if s in public_ids]
    private = [s for s in subnet_ids if s not in public_ids]
    return public, private


def _find_named_sg(ec2, vpc_id: str, name_substr: str) -> Optional[str]:
    sgs = ec2.describe_security_groups(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])[
        "SecurityGroups"
    ]
    for sg in sgs:
        for tag in sg.get("Tags") or []:
            if tag.get("Key") == "Name" and name_substr in str(tag.get("Value") or ""):
                return sg["GroupId"]
        if name_substr in (sg.get("GroupName") or ""):
            return sg["GroupId"]
    return None


def _create_minimal_network(ec2, elbv2, region: str) -> NetworkInfo:
    """Create a minimal VPC+ALB for Fargate (public subnets).

    Resource names match agentic-work so a later agentic-work install can reuse them.
    """
    logger.info("Creating minimal shared network (%s / %s) …", VPC_NAME, ALB_NAME)
    azs = [
        a["ZoneName"]
        for a in ec2.describe_availability_zones(
            Filters=[{"Name": "region-name", "Values": [region]}]
        )["AvailabilityZones"]
        if a.get("State") == "available"
    ][:2]
    if len(azs) < 2:
        raise RuntimeError(f"Need ≥2 AZs in {region} to create ALB")

    vpc_id = ec2.create_vpc(CidrBlock="10.91.0.0/16")["Vpc"]["VpcId"]
    ec2.create_tags(
        Resources=[vpc_id],
        Tags=[
            {"Key": "Name", "Value": VPC_NAME},
            {"Key": "Project", "Value": SHARED},
        ],
    )
    ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsSupport={"Value": True})
    ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={"Value": True})

    igw_id = ec2.create_internet_gateway()["InternetGateway"]["InternetGatewayId"]
    ec2.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
    ec2.create_tags(
        Resources=[igw_id],
        Tags=[{"Key": "Name", "Value": f"igw-for-{SHARED}"}],
    )

    public_subnets: list[str] = []
    for i, az in enumerate(azs):
        sid = ec2.create_subnet(
            VpcId=vpc_id,
            CidrBlock=f"10.91.{i}.0/24",
            AvailabilityZone=az,
        )["Subnet"]["SubnetId"]
        ec2.create_tags(
            Resources=[sid],
            Tags=[
                {"Key": "Name", "Value": f"public-subnet-{i}-for-{SHARED}"},
                {"Key": "Project", "Value": SHARED},
            ],
        )
        ec2.modify_subnet_attribute(SubnetId=sid, MapPublicIpOnLaunch={"Value": True})
        public_subnets.append(sid)

    rt = ec2.create_route_table(VpcId=vpc_id)["RouteTable"]["RouteTableId"]
    ec2.create_tags(
        Resources=[rt], Tags=[{"Key": "Name", "Value": f"public-rt-for-{SHARED}"}]
    )
    ec2.create_route(RouteTableId=rt, DestinationCidrBlock="0.0.0.0/0", GatewayId=igw_id)
    for sid in public_subnets:
        ec2.associate_route_table(RouteTableId=rt, SubnetId=sid)

    alb_sg = ec2.create_security_group(
        GroupName=ALB_SG_NAME,
        Description="ALB SG for shared agentic-work / ob-docs",
        VpcId=vpc_id,
        TagSpecifications=[
            {
                "ResourceType": "security-group",
                "Tags": [
                    {"Key": "Name", "Value": ALB_SG_NAME},
                    {"Key": "Project", "Value": SHARED},
                ],
            }
        ],
    )["GroupId"]
    ec2.authorize_security_group_ingress(
        GroupId=alb_sg,
        IpPermissions=[
            {
                "IpProtocol": "tcp",
                "FromPort": 80,
                "ToPort": 80,
                "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "HTTP"}],
            }
        ],
    )

    ecs_sg = ec2.create_security_group(
        GroupName=ECS_SG_NAME,
        Description="ECS SG for shared agentic-work / ob-docs",
        VpcId=vpc_id,
        TagSpecifications=[
            {
                "ResourceType": "security-group",
                "Tags": [
                    {"Key": "Name", "Value": ECS_SG_NAME},
                    {"Key": "Project", "Value": SHARED},
                ],
            }
        ],
    )["GroupId"]
    ec2.authorize_security_group_ingress(
        GroupId=ecs_sg,
        IpPermissions=[
            {
                "IpProtocol": "tcp",
                "FromPort": 8501,
                "ToPort": 8502,
                "UserIdGroupPairs": [{"GroupId": alb_sg, "Description": "ALB to ECS"}],
            }
        ],
    )

    alb = elbv2.create_load_balancer(
        Name=ALB_NAME,
        Subnets=public_subnets,
        SecurityGroups=[alb_sg],
        Scheme="internet-facing",
        Type="application",
        IpAddressType="ipv4",
        Tags=[
            {"Key": "Name", "Value": ALB_NAME},
            {"Key": "Project", "Value": SHARED},
        ],
    )["LoadBalancers"][0]

    listener = elbv2.create_listener(
        LoadBalancerArn=alb["LoadBalancerArn"],
        Protocol="HTTP",
        Port=80,
        DefaultActions=[
            {
                "Type": "fixed-response",
                "FixedResponseConfig": {
                    "StatusCode": "404",
                    "ContentType": "text/plain",
                    "MessageBody": "Not Found",
                },
            }
        ],
    )["Listeners"][0]

    logger.info("Created ALB %s (%s)", ALB_NAME, alb["DNSName"])
    return NetworkInfo(
        vpc_id=vpc_id,
        subnets=public_subnets,
        security_groups=[ecs_sg],
        alb_arn=alb["LoadBalancerArn"],
        alb_dns=alb["DNSName"],
        alb_sg=alb_sg,
        listener_arn=listener["ListenerArn"],
        assign_public_ip="ENABLED",
    )


def discover_or_create_network(ecs, elbv2, ec2, region: str) -> NetworkInfo:
    """Prefer existing agentic-work ECS/ALB; otherwise create a minimal shared stack."""
    try:
        services = ecs.describe_services(
            cluster=CLUSTER, services=[f"service-for-{SHARED}"]
        ).get("services") or []
        aw = next((s for s in services if s.get("status") != "INACTIVE"), None)
        if aw and aw.get("networkConfiguration"):
            net = aw["networkConfiguration"]["awsvpcConfiguration"]
            subnets = list(net.get("subnets") or [])
            sgs = list(net.get("securityGroups") or [])
            aw_tg = (aw.get("loadBalancers") or [{}])[0].get("targetGroupArn")
            vpc_id = None
            if aw_tg:
                vpc_id = elbv2.describe_target_groups(TargetGroupArns=[aw_tg])[
                    "TargetGroups"
                ][0]["VpcId"]
            alb = elbv2.describe_load_balancers(Names=[ALB_NAME])["LoadBalancers"][0]
            listener = elbv2.describe_listeners(LoadBalancerArn=alb["LoadBalancerArn"])[
                "Listeners"
            ][0]
            logger.info("Using network from ECS service-for-%s", SHARED)
            return NetworkInfo(
                vpc_id=vpc_id or alb["VpcId"],
                subnets=subnets,
                security_groups=sgs,
                alb_arn=alb["LoadBalancerArn"],
                alb_dns=alb["DNSName"],
                alb_sg=(alb.get("SecurityGroups") or [""])[0],
                listener_arn=listener["ListenerArn"],
                assign_public_ip=net.get("assignPublicIp") or "DISABLED",
            )
    except ClientError as e:
        logger.info(
            "No shared ECS service yet (%s)",
            e.response.get("Error", {}).get("Code"),
        )

    try:
        alb = elbv2.describe_load_balancers(Names=[ALB_NAME])["LoadBalancers"][0]
        vpc_id = alb["VpcId"]
        listener = elbv2.describe_listeners(LoadBalancerArn=alb["LoadBalancerArn"])[
            "Listeners"
        ][0]
        all_subnets = [
            s["SubnetId"]
            for s in ec2.describe_subnets(
                Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
            )["Subnets"]
        ]
        public, private = _classify_subnets(ec2, all_subnets)
        ecs_sg = _find_named_sg(ec2, vpc_id, ECS_SG_NAME) or _find_named_sg(
            ec2, vpc_id, "ecs-sg-for-"
        )
        if not ecs_sg:
            raise RuntimeError(f"No ECS security group in VPC {vpc_id}")
        if private:
            subnets, assign = private, "DISABLED"
        elif public:
            subnets, assign = public, "ENABLED"
        else:
            raise RuntimeError(f"No usable subnets in VPC {vpc_id}")
        logger.info("Using existing ALB %s (assignPublicIp=%s)", ALB_NAME, assign)
        return NetworkInfo(
            vpc_id=vpc_id,
            subnets=subnets[:4],
            security_groups=[ecs_sg],
            alb_arn=alb["LoadBalancerArn"],
            alb_dns=alb["DNSName"],
            alb_sg=(alb.get("SecurityGroups") or [""])[0],
            listener_arn=listener["ListenerArn"],
            assign_public_ip=assign,
        )
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "LoadBalancerNotFound":
            logger.warning("ALB lookup failed: %s", e)

    ensure_ecs_cluster(ecs)
    return _create_minimal_network(ec2, elbv2, region)


def ensure_shared_stack(
    *,
    cfg: dict[str, Any],
    s3,
    sm,
    iam,
    ecs,
    elbv2,
    ec2,
) -> tuple[dict[str, Any], NetworkInfo, str]:
    """Ensure config + shared AWS resources. Returns (cfg, network, origin_header)."""
    region = str(cfg["region"])
    account = str(cfg["accountId"])
    bucket = str(cfg["s3_bucket"])

    logger.info("[shared] S3 bucket")
    ensure_s3_bucket(s3, bucket, region)
    cfg["s3_arn"] = f"arn:aws:s3:::{bucket}"

    logger.info("[shared] Secrets (origin header + session signing key)")
    origin_header = ensure_origin_header_secret(sm)
    ensure_session_signing_key(sm)

    logger.info("[shared] ECS IAM roles")
    ensure_ecs_roles(iam, account, region, bucket)

    logger.info("[shared] ECS cluster")
    ensure_ecs_cluster(ecs)

    logger.info("[shared] Network (discover or create)")
    network = discover_or_create_network(ecs, elbv2, ec2, region)

    if not cfg.get("sharing_url"):
        cfg["sharing_url"] = f"http://{network.alb_dns}"
        cfg.setdefault("agentic_work_url", cfg["sharing_url"])
        logger.warning(
            "sharing_url not set — using ALB DNS %s (add CloudFront later for HTTPS)",
            network.alb_dns,
        )

    return cfg, network, origin_header
