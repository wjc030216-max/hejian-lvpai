"""WeChat Pay V3 (JSAPI) client for the phase-two mini program.

Implemented directly on top of ``requests`` + ``cryptography`` so the service
does not depend on a third-party SDK wheel. Merchant credentials are read from
the environment only; private keys are never embedded in source code.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import secrets
import time
from decimal import Decimal, ROUND_HALF_UP

import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag


WXPAY_HOST = "https://api.mch.weixin.qq.com"
JSAPI_PATH = "/v3/pay/transactions/jsapi"


class WeChatPayError(RuntimeError):
    """Payment failure that is safe to surface to the caller."""


def amount_to_fen(amount) -> int:
    """Convert yuan (str/Decimal) to fen without floating point drift."""
    value = Decimal(str(amount or "0"))
    return int((value * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def generate_order_no() -> str:
    """Business order number accepted by WeChat (6-32 alphanumeric)."""
    return "P2" + time.strftime("%Y%m%d%H%M%S") + secrets.token_hex(6).upper()


def _load_private_key() -> bytes:
    path = os.getenv("WECHAT_PAY_PRIVATE_KEY_PATH", "").strip()
    inline = os.getenv("WECHAT_PAY_PRIVATE_KEY", "").strip()
    if inline:
        return inline
    if not path:
        raise WeChatPayError("未配置商户私钥 WECHAT_PAY_PRIVATE_KEY_PATH")
    try:
        return open(path, "r", encoding="utf-8").read()
    except OSError as exc:
        raise WeChatPayError(f"无法读取商户私钥文件: {exc}") from exc


def _load_platform_public_key() -> str:
    path = os.getenv("WECHAT_PAY_PLATFORM_PUB_KEY_PATH", "").strip()
    inline = os.getenv("WECHAT_PAY_PLATFORM_PUB_KEY", "").strip()
    if inline:
        return inline
    if not path:
        raise WeChatPayError("未配置微信支付平台公钥 WECHAT_PAY_PLATFORM_PUB_KEY_PATH")
    try:
        return open(path, "r", encoding="utf-8").read()
    except OSError as exc:
        raise WeChatPayError(f"无法读取微信支付平台公钥文件: {exc}") from exc


def _cert_serial_number() -> str:
    explicit = os.getenv("WECHAT_PAY_CERT_SERIAL_NO", "").strip()
    if explicit:
        return explicit
    cert_path = os.getenv("WECHAT_PAY_CERT_PATH", "").strip()
    cert_inline = os.getenv("WECHAT_PAY_CERT", "").strip()
    if cert_inline or cert_path:
        try:
            data = cert_inline or open(cert_path, "r", encoding="utf-8").read()
            cert = x509.load_pem_x509_certificate(data.encode("utf-8"))
            return format(cert.serial_number, "X")
        except (OSError, ValueError) as exc:
            raise WeChatPayError(f"无法解析商户证书: {exc}") from exc
    raise WeChatPayError("未配置商户证书序列号 WECHAT_PAY_CERT_SERIAL_NO")


def _rsa_sign(private_key_pem: str, message: str) -> str:
    key = serialization.load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
    signature = key.sign(message.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(signature).decode("ascii")


def _rsa_verify(public_key_pem: str, message: str, signature: str) -> bool:
    key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
    try:
        key.verify(base64.b64decode(signature), message.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
        return True
    except Exception:
        return False


class WeChatPay:
    def __init__(self):
        self.appid = os.getenv("WECHAT_PAY_APPID", os.getenv("AITRAVEL_WX_APP_ID", "")).strip()
        self.mchid = os.getenv("WECHAT_PAY_MCHID", os.getenv("AITRAVEL_WX_MCHID", "")).strip()
        self.apiv3_key = os.getenv("WECHAT_PAY_APIV3_KEY", os.getenv("AITRAVEL_WX_API_V3_KEY", "")).strip()
        self.notify_url = os.getenv("WECHAT_PAY_NOTIFY_URL", os.getenv("AITRAVEL_WX_NOTIFY_URL", "")).strip()
        self.private_key = _load_private_key()
        self.platform_public_key = _load_platform_public_key()
        self.serial_no = _cert_serial_number()
    def validate(self) -> None:
        missing = [
            name
            for name, value in (
                ("WECHAT_PAY_APPID", self.appid),
                ("WECHAT_PAY_MCHID", self.mchid),
                ("WECHAT_PAY_APIV3_KEY", self.apiv3_key),
                ("WECHAT_PAY_NOTIFY_URL", self.notify_url),
            )
            if not value
        ]
        if missing:
            raise WeChatPayError("微信支付配置缺失: " + ", ".join(missing))
    def _authorization(self, method: str, url_path: str, body: str) -> str:
        timestamp = str(int(time.time()))
        nonce = secrets.token_hex(16)
        message = f"{method}\n{url_path}\n{timestamp}\n{nonce}\n{body}\n"
        signature = _rsa_sign(self.private_key, message)
        return (
            'WECHATPAY2-SHA256-RSA2048 '
            f'mchid="{self.mchid}",nonce_str="{nonce}",signature="{signature}",'
            f'timestamp="{timestamp}",serial_no="{self.serial_no}"'
        )

    def prepay(self, out_trade_no: str, amount_fen: int, openid: str, description: str) -> str:
        try:
            amount_fen = int(amount_fen)
        except (TypeError, ValueError):
            raise WeChatPayError("订单金额必须是正整数分") from None
        if amount_fen <= 0:
            raise WeChatPayError("订单金额必须是正整数分")
        body = json.dumps(
            {
                "appid": self.appid,
                "mchid": self.mchid,
                "description": description[:127],
                "out_trade_no": out_trade_no,
                "notify_url": self.notify_url,
                "amount": {"total": amount_fen, "currency": "CNY"},
                "payer": {"openid": openid},
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        headers = {
            "Authorization": self._authorization("POST", JSAPI_PATH, body),
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "hejian-lvpai-phase2/1.0",
        }
        try:
            response = requests.post(WXPAY_HOST + JSAPI_PATH, data=body.encode("utf-8"), headers=headers, timeout=(5, 30))
        except requests.RequestException as exc:
            raise WeChatPayError("微信支付预下单网络异常，请稍后重试") from exc
        try:
            payload = response.json()
        except ValueError:
            payload = {"code": "PARSE_ERROR", "message": response.text[:200]}
        if response.status_code not in (200, 201) or not payload.get("prepay_id"):
            code = payload.get("code") or response.status_code
            message = payload.get("message") or "微信预下单失败"
            raise WeChatPayError(f"微信支付预下单失败（{code}）: {message}")
        return payload["prepay_id"]

    def client_pay_params(self, prepay_id: str) -> dict:
        timestamp = str(int(time.time()))
        nonce = secrets.token_hex(16)
        package = f"prepay_id={prepay_id}"
        message = f"{self.appid}\n{timestamp}\n{nonce}\n{package}\n"
        return {
            "timeStamp": timestamp,
            "nonceStr": nonce,
            "package": package,
            "signType": "RSA",
            "paySign": _rsa_sign(self.private_key, message),
        }

    def parse_notify(self, headers, raw_body: bytes) -> dict:
        """Verify WeChat's callback signature and decrypt the payment resource."""
        timestamp = headers.get("Wechatpay-Timestamp", "")
        nonce = headers.get("Wechatpay-Nonce", "")
        signature = headers.get("Wechatpay-Signature", "")
        if not timestamp or not nonce or not signature:
            raise WeChatPayError("微信支付回调缺少签名头")
        try:
            timestamp_value = int(timestamp)
        except (TypeError, ValueError):
            raise WeChatPayError("微信支付回调时间戳无效") from None
        max_age = int(os.getenv("WECHAT_PAY_CALLBACK_MAX_AGE_SECONDS", "300"))
        if abs(int(time.time()) - timestamp_value) > max_age:
            raise WeChatPayError("微信支付回调已过期")
        expected_serial = os.getenv("WECHAT_PAY_PLATFORM_CERT_SERIAL_NO", "").strip()
        received_serial = headers.get("Wechatpay-Serial", "").strip()
        if expected_serial and received_serial != expected_serial:
            raise WeChatPayError("微信支付平台证书序列号不匹配")
        message = f"{timestamp}\n{nonce}\n{raw_body.decode('utf-8', 'replace')}\n"
        if not _rsa_verify(self.platform_public_key, message, signature):
            raise WeChatPayError("微信支付回调验签失败")
        try:
            payload = json.loads(raw_body)
            if not isinstance(payload, dict):
                raise WeChatPayError("微信支付回调通知格式无效")
            resource = payload["resource"]
            if not isinstance(resource, dict):
                raise WeChatPayError("微信支付回调资源格式无效")
            if resource.get("algorithm") != "AEAD_AES_256_GCM":
                raise WeChatPayError("微信支付回调加密算法不受支持")
            ciphertext = base64.b64decode(resource["ciphertext"])
            plaintext = AESGCM(self.apiv3_key.encode("utf-8")).decrypt(
                resource["nonce"].encode("utf-8"),
                ciphertext,
                resource.get("associated_data", "").encode("utf-8"),
            )
            transaction = json.loads(plaintext)
            if not isinstance(transaction, dict):
                raise WeChatPayError("微信支付回调交易数据无效")
            transaction["event_type"] = str(payload.get("event_type") or "")
            transaction["notification_id"] = str(payload.get("id") or "")
            return transaction
        except (ValueError, KeyError, TypeError, binascii.Error, InvalidTag) as exc:
            raise WeChatPayError("微信支付回调解密失败") from exc


def payment_mode() -> str:
    """Resolve the active payment mode from the environment."""
    mode = os.getenv("PHASE2_PAYMENT_MODE", "").strip().lower()
    if mode in ("disabled", "off", "none"):
        return "disabled"
    if mode in ("allinpay", "w06s"):
        return "allinpay"
    if mode in ("wechat", "wxpay", "jsapi"):
        return "wechat"
    if mode == "dev-bypass" or os.getenv("PHASE2_DEV_PAYMENT_BYPASS", "false") == "true":
        return "dev-bypass"
    return "unconfigured"
