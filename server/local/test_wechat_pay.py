"""Unit tests for the WeChat Pay V3 JSAPI client. No real network or charges."""
import base64
import json
import os
import time
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from phase2 import wechat_pay


def _make_keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


class WeChatPayTests(unittest.TestCase):
    def setUp(self):
        self.private_pem, self.public_pem = _make_keypair()
        self.apiv3_key = "FZ20260731qwertyuiolkjhgfdsAqazw"
        self.env = patch.dict(os.environ, {
            "WECHAT_PAY_APPID": "wxd1ce8e9fbba6186a",
            "WECHAT_PAY_MCHID": "1115854612",
            "WECHAT_PAY_APIV3_KEY": self.apiv3_key,
            "WECHAT_PAY_CERT_SERIAL_NO": "TESTSERIAL",
            "WECHAT_PAY_NOTIFY_URL": "https://example.com/api/v2/payments/notify",
            "WECHAT_PAY_PRIVATE_KEY": self.private_pem,
            "WECHAT_PAY_PLATFORM_PUB_KEY": self.public_pem,
            "AITRAVEL_AIVIDEO_PRICE_FEN": "1990",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_amount_to_fen(self):
        self.assertEqual(wechat_pay.amount_to_fen("19.90"), 1990)
        self.assertEqual(wechat_pay.amount_to_fen("0.01"), 1)
        self.assertEqual(wechat_pay.amount_to_fen("100"), 10000)

    def test_payment_mode_resolution(self):
        self.env.stop()
        for value, expected in (
            ("wechat", "wechat"),
            ("wxpay", "wechat"),
            ("jsapi", "wechat"),
            ("dev-bypass", "dev-bypass"),
            ("disabled", "disabled"),
            ("off", "disabled"),
            ("none", "disabled"),
        ):
            with patch.dict(os.environ, {"PHASE2_PAYMENT_MODE": value}, clear=False):
                self.assertEqual(wechat_pay.payment_mode(), expected)
        with patch.dict(os.environ, {"PHASE2_PAYMENT_MODE": ""}, clear=False):
            with patch.dict(os.environ, {"PHASE2_DEV_PAYMENT_BYPASS": "true"}, clear=False):
                self.assertEqual(wechat_pay.payment_mode(), "dev-bypass")
        self.env.start()

    def test_order_no_format(self):
        self.assertRegex(wechat_pay.generate_order_no(), r"^P2[0-9]{14}[0-9A-F]{12}$")

    def test_client_pay_params_verify(self):
        client = wechat_pay.WeChatPay()
        params = client.client_pay_params("wx_prepay_test")
        message = (
            f"{client.appid}\n{params['timeStamp']}\n{params['nonceStr']}\n{params['package']}\n"
        )
        public_key = serialization.load_pem_public_key(self.public_pem.encode())
        public_key.verify(
            base64.b64decode(params["paySign"]),
            message.encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        self.assertEqual(params["package"], "prepay_id=wx_prepay_test")
        self.assertEqual(params["signType"], "RSA")

    def test_prepay_builds_signed_request(self):
        client = wechat_pay.WeChatPay()
        response = {"prepay_id": "wx_prepay_test"}
        with patch("phase2.wechat_pay.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = response
            prepay_id = client.prepay("P2TESTORDER", 1990, "openid-1", "漓江心动之旅")
        self.assertEqual(prepay_id, "wx_prepay_test")
        args, kwargs = post.call_args
        self.assertIn("WECHATPAY2-SHA256-RSA2048", kwargs["headers"]["Authorization"])
        body = json.loads(kwargs["data"].decode("utf-8"))
        self.assertEqual(body["out_trade_no"], "P2TESTORDER")
        self.assertEqual(body["amount"], {"total": 1990, "currency": "CNY"})
        self.assertEqual(body["payer"], {"openid": "openid-1"})

    def test_parse_notify_roundtrip(self):
        client = wechat_pay.WeChatPay()
        plaintext = {
            "out_trade_no": "P2TESTORDER",
            "transaction_id": "42000000000000000001",
            "amount": {"total": 1990},
        }
        nonce = "0123456789ab"
        associated_data = "transaction"
        ciphertext = AESGCM(self.apiv3_key.encode()).encrypt(
            nonce.encode(), json.dumps(plaintext, separators=(",", ":")).encode(), associated_data.encode()
        )
        resource = {
            "algorithm": "AEAD_AES_256_GCM",
            "ciphertext": base64.b64encode(ciphertext).decode(),
            "associated_data": associated_data,
            "nonce": nonce,
        }
        payload = {"event_type": "TRANSACTION.SUCCESS", "resource": resource}
        raw_body = json.dumps(payload, separators=(",", ":")).encode()
        timestamp = str(int(time.time()))
        header_nonce = "abcdef0123456789"
        signature = self._sign(f"{timestamp}\n{header_nonce}\n{raw_body.decode()}\n")
        headers = {
            "Wechatpay-Timestamp": timestamp,
            "Wechatpay-Nonce": header_nonce,
            "Wechatpay-Signature": signature,
        }
        decoded = client.parse_notify(headers, raw_body)
        self.assertEqual(decoded["event_type"], "TRANSACTION.SUCCESS")
        self.assertEqual(decoded["out_trade_no"], "P2TESTORDER")
        self.assertEqual(decoded["amount"]["total"], 1990)

    def _sign(self, message):
        key = serialization.load_pem_private_key(self.private_pem.encode(), password=None)
        return base64.b64encode(
            key.sign(message.encode(), padding.PKCS1v15(), hashes.SHA256())
        ).decode()


if __name__ == "__main__":
    unittest.main()
