"""Private, local-development TOS result storage; never reuses Ark credentials."""
import os
import re
from dataclasses import dataclass
from pathlib import Path

import tos
from common.local_guard import require_cloud_enabled


@dataclass(frozen=True)
class StorageConfig:
    access_key: str
    secret_key: str
    security_token: str
    bucket: str
    endpoint: str
    region: str
    prefix: str
    expires: int

    @classmethod
    def from_env(cls):
        values = {key: os.getenv(key, "").strip() for key in (
            "TOS_ACCESS_KEY_ID", "TOS_ACCESS_KEY_SECRET", "TOS_SECURITY_TOKEN",
            "TOS_BUCKET", "TOS_ENDPOINT", "TOS_REGION", "TOS_KEY_PREFIX")}
        for key, value in values.items():
            if key != "TOS_SECURITY_TOKEN" and not value:
                raise ValueError("Missing dedicated TOS setting: " + key)
        # Use the configured existing bucket; account permissions provide isolation.
        region = values['TOS_REGION']
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)+", region):
            raise ValueError("Invalid TOS_REGION")
        if values['TOS_ENDPOINT'] != f"https://tos-{region}.volces.com":
            raise ValueError("Use the public HTTPS TOS endpoint matching TOS_REGION")
        prefix = values['TOS_KEY_PREFIX']
        namespace={
            'staging': 'phase2-test',
            'production': 'phase2-prod',
        }.get(os.getenv('APP_ENV'), 'local-real')
        if not re.fullmatch(namespace+r'/(?:[a-zA-Z0-9_-]+/)*', prefix):
            raise ValueError('TOS_KEY_PREFIX must stay under '+namespace+'/ and end with /')
        expires = int(os.getenv('TOS_SIGNED_URL_TTL_SECONDS', '3600'))
        if not 300 <= expires <= 86400:
            raise ValueError("TOS signed URL TTL must be 300..86400 seconds")
        return cls(values['TOS_ACCESS_KEY_ID'], values['TOS_ACCESS_KEY_SECRET'],
                   values['TOS_SECURITY_TOKEN'], values['TOS_BUCKET'],
                   values['TOS_ENDPOINT'], region, prefix, expires)


class ResultStore:
    def __init__(self, config):
        self.config = config
        self.client = tos.TosClientV2(
            config.access_key, config.secret_key, config.endpoint, config.region,
            security_token=config.security_token or None,
            connection_time=10, socket_timeout=120, max_retry_count=2)

    def object_key(self, result_id):
        if not re.fullmatch(r"[0-9a-f]{32}", result_id):
            raise ValueError("Invalid result ID")
        return self.config.prefix + 'videos/' + result_id + '.mp4'

    def portrait_key(self, result_id):
        if not re.fullmatch(r"[0-9a-f]{32}", result_id):
            raise ValueError("Invalid portrait ID")
        return self.config.prefix + 'portraits/' + result_id + '.jpg'

    def asset_key(self, asset_id, suffix):
        if not re.fullmatch(r"(?:asset|file)_[0-9a-f]{32}", asset_id):
            raise ValueError("Invalid asset ID")
        if suffix not in ('.jpg', '.jpeg', '.png', '.webp', '.mp4', '.mp3', '.wav'):
            raise ValueError("Invalid asset suffix")
        return self.config.prefix + 'assets/' + asset_id + suffix

    def put(self, result_id, path):
        require_cloud_enabled()
        key = self.object_key(result_id)
        self.client.put_object_from_file(
            self.config.bucket, key, str(Path(path)), content_type='video/mp4',
            acl=tos.ACLType.ACL_Private)
        return key

    def sign(self, result_id):
        require_cloud_enabled()
        result = self.client.pre_signed_url(
            tos.HttpMethodType.Http_Method_Get, bucket=self.config.bucket,
            key=self.object_key(result_id), expires=self.config.expires)
        return result.signed_url

    def put_portrait(self, result_id, path):
        require_cloud_enabled()
        key = self.portrait_key(result_id)
        self.client.put_object_from_file(self.config.bucket, key, str(Path(path)),
                                         content_type='image/jpeg', acl=tos.ACLType.ACL_Private)
        return key

    def put_asset(self, asset_id, suffix, path, content_type):
        require_cloud_enabled()
        key = self.asset_key(asset_id, suffix)
        self.client.put_object_from_file(
            self.config.bucket, key, str(Path(path)),
            content_type=content_type, acl=tos.ACLType.ACL_Private)
        return key

    def sign_portrait(self, result_id):
        require_cloud_enabled()
        result = self.client.pre_signed_url(tos.HttpMethodType.Http_Method_Get,
            bucket=self.config.bucket, key=self.portrait_key(result_id), expires=self.config.expires)
        return result.signed_url

    def sign_key(self, key):
        require_cloud_enabled()
        result = self.client.pre_signed_url(
            tos.HttpMethodType.Http_Method_Get, bucket=self.config.bucket,
            key=key, expires=self.config.expires)
        return result.signed_url

    def delete(self, result_id):
        require_cloud_enabled()
        self.client.delete_object(self.config.bucket, self.object_key(result_id))

    def delete_portrait(self, result_id):
        require_cloud_enabled()
        self.client.delete_object(self.config.bucket, self.portrait_key(result_id))

    def delete_key(self, key):
        require_cloud_enabled()
        self.client.delete_object(self.config.bucket, key)
