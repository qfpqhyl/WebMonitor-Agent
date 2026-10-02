"""S3 evidence uploads complete before references can enter committed snapshots."""
import asyncio
import hashlib
from functools import lru_cache
from uuid import uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from webmonitor.api.errors import DomainError
from webmonitor.config import get_settings


@lru_cache
def s3_client():
    settings = get_settings()
    return boto3.client("s3",endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key_id.get_secret_value(),
        aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
        region_name="us-east-1",config=Config(connect_timeout=5,read_timeout=20,retries={"max_attempts":1},s3={"addressing_style":"path"}))


async def initialize_bucket():
    bucket = get_settings().s3_bucket
    def ensure():
        try:
            s3_client().head_bucket(Bucket=bucket)
        except ClientError as exc:
            if exc.response.get("ResponseMetadata",{}).get("HTTPStatusCode") != 404:
                raise
            s3_client().create_bucket(Bucket=bucket)
    await asyncio.to_thread(ensure)


async def upload_evidence(*,workspace_id,monitor_id,run_id,kind:str,data:bytes) -> dict:
    bucket = get_settings().s3_bucket
    key = f"{workspace_id}/{monitor_id}/{run_id}/{uuid4()}"
    content_type = "image/png" if kind == "screenshot" else "text/plain; charset=utf-8"
    digest = hashlib.sha256(data).hexdigest()
    try:
        await asyncio.to_thread(s3_client().put_object,Bucket=bucket,Key=key,Body=data,ContentType=content_type,Metadata={"sha256":digest})
    except (BotoCoreError,ClientError,OSError):
        raise DomainError("storage_unavailable",503) from None
    return {"kind":kind,"bucket":bucket,"object_key":key,"content_type":content_type,"size_bytes":len(data),"sha256":digest}


async def stream_evidence(bucket:str,key:str):
    try:
        response = await asyncio.to_thread(s3_client().get_object,Bucket=bucket,Key=key)
    except (BotoCoreError,ClientError,OSError):
        raise DomainError("storage_unavailable",503) from None
    body = response["Body"]
    async def chunks():
        try:
            while chunk := await asyncio.to_thread(body.read,65536):
                yield chunk
        finally:
            await asyncio.to_thread(body.close)
    return chunks()
