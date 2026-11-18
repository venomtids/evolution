"""EVO·VISION — Neon Object Storage (S3-compatible).

Bucket privado `assets` do branch `production` (NEON Object Storage).
Uso:
    NEON_STORAGE_ENABLED=true  -> Vision Monitor sobe latest.jpg a cada frame
    python storage.py          -> smoke test (upload/download/delete de um objeto de teste)

Envs (puxadas pelo `neon deploy` / `neon env pull`):
    AWS_ENDPOINT_URL_S3, AWS_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
Opcionais:
    NEON_STORAGE_BUCKET (default "assets"), NEON_STORAGE_ENABLED (default false)
"""
import os
import sys
import time

try:  # vars Neon vivem no .env (pull do `neon deploy`); não sobrescreve o ambiente do shell
    from dotenv import load_dotenv

    load_dotenv(override=False)
except Exception:
    pass

BUCKET = os.getenv("NEON_STORAGE_BUCKET", "assets")


def enabled() -> bool:
    return os.getenv("NEON_STORAGE_ENABLED", "false").lower() == "true"


def _client():
    import boto3
    from botocore.config import Config

    endpoint = os.getenv("AWS_ENDPOINT_URL_S3")
    if not endpoint:
        raise RuntimeError("AWS_ENDPOINT_URL_S3 ausente — rode `neon deploy` ou `neon env pull`.")
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        config=Config(signature_version="s3v4"),
    )


def upload_bytes(data: bytes, key: str, content_type: str = "application/octet-stream") -> str:
    _client().put_object(Bucket=BUCKET, Key=key, Body=data, ContentType=content_type)
    return f"s3://{BUCKET}/{key}"


def upload_file(local_path: str, key: str, content_type: str = "image/jpeg") -> str:
    with open(local_path, "rb") as fh:
        return upload_bytes(fh.read(), key, content_type)


def download_bytes(key: str) -> bytes:
    return _client().get_object(Bucket=BUCKET, Key=key)["Body"].read()


def head(key: str) -> dict:
    return _client().head_object(Bucket=BUCKET, Key=key)


def delete(key: str) -> None:
    _client().delete_object(Bucket=BUCKET, Key=key)


def _smoke() -> int:
    """upload -> head -> download (bytes iguais) -> delete. Retorna exit code."""
    key = f"smoke-tests/evo-vision-{int(time.time())}.txt"
    payload = b"evo-vision neon object storage smoke test\n"
    try:
        uri = upload_bytes(payload, key, content_type="text/plain")
        meta = head(key)
        roundtrip = download_bytes(key)
        delete(key)
        ok = roundtrip == payload and meta["ContentLength"] == len(payload)
        print(f"upload : {uri}")
        print(f"head   : {meta['ContentLength']} bytes, modified {meta['LastModified']}")
        print(f"download == upload: {roundtrip == payload}")
        print(f"delete : ok")
        print("SMOKE PASS" if ok else "SMOKE FAIL (conteúdo divergente)")
        return 0 if ok else 1
    except Exception as e:
        print(f"SMOKE FAIL: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(_smoke())
