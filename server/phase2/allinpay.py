"""Allinpay W06S adapter, following the separately verified cashier demo."""
import hashlib
import hmac
import json
import os
import re
from urllib.parse import parse_qsl, unquote, urlsplit

import requests


class AllinpayError(RuntimeError):
    pass


class AllinpayResponseError(AllinpayError):
    """The gateway returned a definitive rejection or mismatched response."""

    def __init__(self, message, code=''):
        super().__init__(message)
        self.code = str(code or '')


def sign_params(params, secret):
    pairs = sorted((key, value) for key, value in params.items()
                   if key != 'sign' and value not in (None, ''))
    source = ''.join(str(key) + str(value) for key, value in pairs) + secret
    return hashlib.md5(source.encode('utf-8')).hexdigest().upper()


def pick(data, *names):
    lowered = {str(key).lower(): value for key, value in data.items()}
    return next((lowered[name.lower()] for name in names if name.lower() in lowered), None)


def parse_payinfo(value):
    if isinstance(value, str) and value.strip():
        try:
            value = json.loads(value)
        except ValueError:
            decoded = unquote(value)
            try:
                value = json.loads(decoded)
            except ValueError:
                value = dict(parse_qsl(decoded, keep_blank_values=True))
    if not isinstance(value, dict) or not value or not value.get('sign'):
        raise AllinpayError('支付收银台参数无效，请稍后重试')
    # Preserve the signed cashier fields exactly as in the verified demo.
    return {str(key): str(item) for key, item in value.items() if item not in (None, '')}


def response_json(response):
    try:
        return response.json()
    except ValueError:
        for encoding in ('utf-8', 'gb18030'):
            try:
                result = json.loads(response.content.decode(encoding))
                if isinstance(result, dict):
                    return result
            except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
                continue
    raise AllinpayError('支付网关返回了无法识别的数据')


ALLINPAY_HOSTS = {
    'test': 'https://interfacetest.allinpaygx.com',
    'production': 'https://interface.allinpaygx.com',
}
API_PATH = '/api/access/payInterface/unifiedPay'
REFUND_PATH = '/api/access/payInterface/refundsuhaimei'


def payment_environment():
    value = os.getenv('ALLINPAY_ENV', 'production').strip().lower() or 'production'
    if value not in ALLINPAY_HOSTS:
        raise AllinpayError('ALLINPAY_ENV 只能是 test 或 production')
    return value


class Allinpay:
    def __init__(self):
        self.environment = payment_environment()
        base = ALLINPAY_HOSTS[self.environment]
        self.api_url = os.getenv('ALLINPAY_API_URL', '').strip() or base + API_PATH
        self.refund_api_url = os.getenv('ALLINPAY_REFUND_API_URL', '').strip() or base + REFUND_PATH
        self.refund_version = os.getenv('ALLINPAY_REFUND_VERSION', 'v1.0').strip()
        self.merchant_no = os.getenv('ALLINPAY_MERCHANT_NO', '').strip()
        self.secret = os.getenv('ALLINPAY_SECRET_KEY', '').strip()
        self.cashier_appid = os.getenv('ALLINPAY_CASHIER_APPID', 'wxef277996acc166c3').strip()
        self.notify_url = os.getenv('ALLINPAY_NOTIFY_URL', '').strip() or (
            os.getenv('PUBLIC_API_BASE_URL', '').rstrip('/') + '/api/v2/payments/allinpay/notify')
    def validate(self):
        if not self.merchant_no or not self.secret:
            raise AllinpayError('尚未配置通联商户号或支付密钥')
        for url in (self.api_url, self.notify_url):
            parsed = urlsplit(url)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.fragment:
                raise AllinpayError('通联支付接口和通知地址必须为 HTTPS')
        if not re.fullmatch(r'wx[0-9a-zA-Z]{16}', self.cashier_appid):
            raise AllinpayError('通联收银台 AppID 无效')
        expected_host = urlsplit(ALLINPAY_HOSTS[self.environment]).hostname
        if urlsplit(self.api_url).hostname != expected_host:
            raise AllinpayError('ALLINPAY_API_URL 与 ALLINPAY_ENV=%s 不匹配' % self.environment)
    def validate_refund(self):
        if not self.merchant_no or not self.secret:
            raise AllinpayError('尚未配置通联商户号或支付密钥')
        parsed = urlsplit(self.refund_api_url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.fragment:
            raise AllinpayError('通联退款接口必须为 HTTPS')
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', self.refund_version):
            raise AllinpayError('通联退款版本号无效')
        expected_host = urlsplit(ALLINPAY_HOSTS[self.environment]).hostname
        if urlsplit(self.refund_api_url).hostname != expected_host:
            raise AllinpayError('ALLINPAY_REFUND_API_URL 与 ALLINPAY_ENV=%s 不匹配' % self.environment)

    def prepay(self, order_no, amount_fen, description):
        try:
            amount_fen = int(amount_fen)
        except (TypeError, ValueError):
            raise AllinpayError('订单价格必须为正整数分') from None
        if amount_fen <= 0:
            raise AllinpayError('订单价格必须为正整数分')
        params = dict(merchantNo=self.merchant_no, merchantOrderNo=order_no,
                      amount=str(amount_fen), notifyUrl=self.notify_url,
                      orderName=description[:128], payType='W06S',
                      note=json.dumps({'merchant_order_no': order_no}, ensure_ascii=False, separators=(',', ':')))
        params['sign'] = sign_params(params, self.secret)
        try:
            response = requests.get(self.api_url, params=params, timeout=(5, 20), allow_redirects=False)
            response.raise_for_status()
            try:
                result = response.json()
            except ValueError:
                result = json.loads(response.content.decode('gb18030'))
        except (requests.RequestException, ValueError, UnicodeError):
            # Transport errors may contain the signed request URL.
            raise AllinpayError('支付预下单暂未确认，请稍后查询或继续支付') from None
        if response.status_code != 200 or not isinstance(result, dict) or str(result.get('status')) != '0000':
            raise AllinpayError('通联预下单未成功，请稍后重试')
        return dict(order_no=order_no, merchant_order_no=order_no, trade_order_no=result.get('orderNo'),
                    amount_fen=amount_fen, payment_mode='allinpay', pay_mode='w06s',
                    cashier_appid=self.cashier_appid, extra_data=parse_payinfo(result.get('payinfo')))

    def parse_notify(self, data):
        if not self.secret or not isinstance(data, dict):
            raise AllinpayError('支付通知无效')
        if any(not isinstance(v, (str, int, float)) for v in data.values()):
            raise AllinpayError('支付通知字段无效')
        received = str(data.get('sign', '')).upper()
        if not re.fullmatch('[0-9A-F]{32}', received) or not hmac.compare_digest(received, sign_params(data, self.secret)):
            raise AllinpayError('支付通知验签失败')
        merchant = pick(data, 'merchantNo', 'merchant_no')
        if merchant is not None and str(merchant) != self.merchant_no:
            raise AllinpayError('支付通知商户不匹配')
        order_no = pick(data, 'merchantOrderNo', 'merchant_order_no')
        if not isinstance(order_no, str) or not order_no:
            raise AllinpayError('支付通知缺少订单号')
        success = str(pick(data, 'trxstatus', 'status') or '').strip().upper() in ('2', '0000', 'SUCCESS', 'TRX_SUCCESS')
        amount = None
        if success:
            raw = str(pick(data, 'trxamt', 'amount') or '')
            if not re.fullmatch('[0-9]+', raw) or int(raw) <= 0:
                raise AllinpayError('支付通知金额无效')
            amount = int(raw)
        return dict(order_no=order_no, success=success, amount_fen=amount,
                    transaction_id=pick(data, 'chnltrxid', 'transactionId') or '')

    def refund(self, original_order_no, amount_fen, refund_no, remark=''):
        """Submit one full refund using the merchant's original payment order."""
        self.validate_refund()
        if not isinstance(original_order_no, str) or not original_order_no:
            raise AllinpayError('缺少原支付订单号')
        if not isinstance(refund_no, str) or not refund_no:
            raise AllinpayError('缺少商户退款订单号')
        try:
            amount_fen = int(amount_fen)
        except (TypeError, ValueError):
            raise AllinpayError('退款金额无效') from None
        if amount_fen <= 0:
            raise AllinpayError('退款金额必须大于 0')
        params = dict(
            version=self.refund_version,
            merchantNo=self.merchant_no,
            amount=str(amount_fen),
            oriMerchantOrderNo=original_order_no,
            merchantOrderNo=refund_no,
            remark=str(remark or '')[:200],
        )
        params = {key: value for key, value in params.items() if value not in (None, '')}
        params['sign'] = sign_params(params, self.secret)
        try:
            response = requests.get(
                self.refund_api_url,
                params=params,
                timeout=(5, 30),
                allow_redirects=False,
            )
            response.raise_for_status()
            result = response_json(response)
        except requests.RequestException:
            # The request may have reached Allinpay. The caller must keep the
            # same refund number and require reconciliation, never mint another.
            raise AllinpayError('通联退款请求结果未确认，请核对原退款单') from None
        if str(result.get('status')) != '0000':
            code = str(result.get('errorCode') or '')
            message = str(result.get('errorMessage') or '通联退款未受理')
            raise AllinpayResponseError((code + '：' if code else '') + message, code)
        if str(result.get('merchantOrderNo') or refund_no) != refund_no:
            raise AllinpayResponseError('通联退款响应订单号不匹配')
        response_merchant = pick(result, 'merchantNo', 'merchant_no')
        if response_merchant not in (None, '') and str(response_merchant) != self.merchant_no:
            raise AllinpayResponseError('通联退款响应商户号不匹配')
        response_amount = pick(result, 'amount', 'refundAmount', 'refund_amount')
        if response_amount not in (None, ''):
            try:
                if int(response_amount) != amount_fen:
                    raise AllinpayResponseError('通联退款响应金额不匹配')
            except (TypeError, ValueError):
                raise AllinpayResponseError('通联退款响应金额无效') from None
        return result
