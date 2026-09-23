# Copyright 2026 Amazon.com, Inc. or its affiliates
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import logging
import os
import time
import urllib.error
import urllib.request

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

logger = logging.getLogger(__name__)

OPTED_IN_REGIONS: set[str] = set()
FABLE_RETENTION_ENSURED = False
BEDROCK_DATA_RETENTION_URL = "https://bedrock.{region}.amazonaws.com/data-retention"
# bedrock-mantle is the Amazon Bedrock OpenAI-compatible endpoint (not a separate AWS
# service): https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-mantle.html
MANTLE_DATA_RETENTION_URL = "https://bedrock-mantle.{region}.api.aws/v1/data_retention"
OPT_IN_MODE = "provider_data_share"
DEFAULT_REGION = "us-east-1"
FABLE_BEDROCK_REGIONS = ("us-west-2", "us-east-1", "us-east-2")
CONFIG_KEY = "fable_data_retention_opt_in"
HTTP_MAX_ATTEMPTS = 3
HTTP_RETRY_BACKOFF_SECONDS = 1.0
HTTP_TIMEOUT_SECONDS = 30


def _urlopen_with_retry(
    request: urllib.request.Request, timeout: int = HTTP_TIMEOUT_SECONDS
):
    """Open an HTTP request with simple retries for transient failures."""
    # Reject non-HTTPS schemes (e.g. file:/ftp:) before opening the URL.
    if request.type != "https":
        raise ValueError(f"Refusing to open non-HTTPS URL scheme: {request.type}")
    last_error: Exception | None = None
    for attempt in range(1, HTTP_MAX_ATTEMPTS + 1):
        try:
            return urllib.request.urlopen(request, timeout=timeout)  # nosec B310 — scheme validated above
        except urllib.error.HTTPError as error:
            # Retry only transient server / rate-limit responses.
            if error.code not in (429, 500, 502, 503, 504) or attempt >= HTTP_MAX_ATTEMPTS:
                raise
            last_error = error
            time.sleep(HTTP_RETRY_BACKOFF_SECONDS * attempt)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last_error = error
            if attempt >= HTTP_MAX_ATTEMPTS:
                raise
            time.sleep(HTTP_RETRY_BACKOFF_SECONDS * attempt)
    assert last_error is not None
    raise last_error


def _get_account_id() -> str:
    import utils

    config = utils.load_config()
    account_id = config.get("accountId")
    if account_id:
        return str(account_id)

    sts = boto3.client("sts")
    account_id = sts.get_caller_identity()["Account"]
    config["accountId"] = account_id
    with open(utils.config_path, "w", encoding="utf-8") as config_file:
        json.dump(config, config_file, indent=2, ensure_ascii=False)
    return str(account_id)


def _is_fable_opt_in_recorded(account_id: str) -> bool:
    import utils

    config = utils.load_config()
    recorded = config.get(CONFIG_KEY)
    if isinstance(recorded, dict):
        return (
            recorded.get("completed") is True
            and str(recorded.get("account_id", "")) == account_id
        )
    return recorded is True


def _record_fable_opt_in(account_id: str) -> None:
    import utils

    config = utils.load_config()
    config[CONFIG_KEY] = {
        "completed": True,
        "account_id": account_id,
    }
    try:
        with open(utils.config_path, "w", encoding="utf-8") as config_file:
            json.dump(config, config_file, indent=2, ensure_ascii=False)
        logger.info(
            "Recorded Fable data retention opt-in in config.json for account %s",
            account_id,
        )
    except Exception as error:
        logger.warning("Failed to record Fable opt-in in config.json: %s", error)


def _get_bearer_token(region: str) -> str:
    token = os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
    if token:
        return token

    from aws_bedrock_token_generator import provide_token

    return provide_token(region=region)


def get_bedrock_bearer_token(region: str) -> str:
    return _get_bearer_token(region)


def _request_bedrock_control_plane(
    method: str, region: str, body: dict | None = None
) -> tuple[int, str]:
    credentials = boto3.Session().get_credentials().get_frozen_credentials()
    url = BEDROCK_DATA_RETENTION_URL.format(region=region)
    payload = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if payload else {}
    request = AWSRequest(method=method, url=url, data=payload, headers=headers)
    SigV4Auth(credentials, "bedrock", region).add_auth(request)
    prepared = request.prepare()
    http_request = urllib.request.Request(
        prepared.url,
        data=payload,
        method=method,
        headers=dict(prepared.headers),
    )
    with _urlopen_with_retry(http_request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return response.status, response.read().decode()


def _request_mantle(method: str, region: str, body: dict | None = None) -> tuple[int, str]:
    token = _get_bearer_token(region)
    url = MANTLE_DATA_RETENTION_URL.format(region=region)
    payload = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        url,
        data=payload,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with _urlopen_with_retry(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return response.status, response.read().decode()


def get_data_retention_mode(region: str = DEFAULT_REGION) -> tuple[bool, str]:
    try:
        status, body = _request_bedrock_control_plane("GET", region)
        logger.info(
            "data retention mode GET ok region=%s status=%s body=%s",
            region,
            status,
            body,
        )
        return True, f"HTTP {status}"
    except urllib.error.HTTPError as error:
        body = error.read().decode(errors="replace")
        logger.error(
            "Failed to retrieve data retention mode region=%s HTTP %s: %s",
            region,
            error.code,
            body,
            exc_info=True,
        )
        return False, "Failed to retrieve data retention mode"
    except Exception as error:
        logger.error(
            "Failed to retrieve data retention mode region=%s (%s)",
            region,
            type(error).__name__,
            exc_info=True,
        )
        return False, "Failed to retrieve data retention mode"


def opt_in_provider_data_share(region: str = DEFAULT_REGION) -> tuple[bool, str]:
    if region in OPTED_IN_REGIONS:
        return True, ""

    control_plane_detail = ""
    try:
        status, body = _request_bedrock_control_plane(
            "PUT", region, {"mode": OPT_IN_MODE}
        )
        OPTED_IN_REGIONS.add(region)
        logger.info(
            "bedrock control plane opt-in ok region=%s status=%s body=%s",
            region,
            status,
            body or OPT_IN_MODE,
        )
        return True, f"bedrock control plane ({region}) HTTP {status}"
    except urllib.error.HTTPError as control_plane_error:
        control_plane_detail = (
            f"HTTP {control_plane_error.code}: "
            f"{control_plane_error.read().decode(errors='replace')}"
        )
        logger.warning(
            "bedrock control plane opt-in failed region=%s: %s",
            region,
            control_plane_detail,
        )
    except Exception as control_plane_error:
        control_plane_detail = (
            f"{type(control_plane_error).__name__}: {control_plane_error}"
        )
        logger.warning(
            "bedrock control plane opt-in failed region=%s: %s",
            region,
            control_plane_detail,
            exc_info=True,
        )

    try:
        status, body = _request_mantle("PUT", region, {"mode": OPT_IN_MODE})
        OPTED_IN_REGIONS.add(region)
        logger.info(
            "bedrock-mantle opt-in ok region=%s status=%s body=%s",
            region,
            status,
            body or OPT_IN_MODE,
        )
        return True, f"bedrock-mantle ({region}) HTTP {status}"
    except urllib.error.HTTPError as mantle_error:
        mantle_detail = (
            f"HTTP {mantle_error.code}: "
            f"{mantle_error.read().decode(errors='replace')}"
        )
        logger.error(
            "Failed to opt in for provider data share region=%s "
            "control_plane=%s mantle=%s",
            region,
            control_plane_detail,
            mantle_detail,
            exc_info=True,
        )
        return False, f"Failed to opt in for provider data share in {region}"
    except Exception as mantle_error:
        logger.error(
            "Failed to opt in for provider data share region=%s "
            "control_plane=%s mantle=%s",
            region,
            control_plane_detail,
            f"{type(mantle_error).__name__}: {mantle_error}",
            exc_info=True,
        )
        return False, f"Failed to opt in for provider data share in {region}"


def ensure_fable_data_retention(
    model_id: str,
    bedrock_region: str = DEFAULT_REGION,
) -> bool:
    global FABLE_RETENTION_ENSURED

    if "fable" not in model_id.lower():
        return True

    if FABLE_RETENTION_ENSURED:
        return True

    account_id = _get_account_id()
    if _is_fable_opt_in_recorded(account_id):
        FABLE_RETENTION_ENSURED = True
        OPTED_IN_REGIONS.update(FABLE_BEDROCK_REGIONS)
        if bedrock_region:
            OPTED_IN_REGIONS.add(bedrock_region)
        return True

    regions = []
    for region in (bedrock_region, *FABLE_BEDROCK_REGIONS):
        if region not in regions:
            regions.append(region)

    all_success = True
    for region in regions:
        success, message = opt_in_provider_data_share(region=region)
        if success:
            if message:
                logger.info("Bedrock data retention opt-in: %s", message)
        else:
            logger.warning("Bedrock data retention opt-in failed: %s", message)
            all_success = False

    if all_success:
        FABLE_RETENTION_ENSURED = True
        _record_fable_opt_in(account_id)

    return all_success
