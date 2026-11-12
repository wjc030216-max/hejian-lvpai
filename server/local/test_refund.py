"""User-selected full-refund lifecycle and gateway contract tests."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from phase2.allinpay import Allinpay, AllinpayError, AllinpayResponseError, sign_params
from phase2.orchestrator import Phase2Orchestrator
from phase2.refund_service import RefundService
from phase2.store import Store


class UserRefundTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = Store(str(Path(self.folder.name) / "refunds.db"))
        self.project_id = "p2_" + "a" * 32
        self.project = {
            "project_id": self.project_id,
            "wx_openid": "wx_user",
            "template": {"video_name": "测试旅拍"},
            "template_code": "P2T-test",
            "status": "refund_pending",
            "stage": "video",
            "payment_mode": "allinpay",
            "payment_status": "paid",
            "order_no": "P2PAYTEST",
            "amount_fen": 1990,
            "amount_paid": 1990,
            "portrait_url": "https://example/portrait",
            "video_url": "",
            "roles": [],
            "attempt": 3,
            "created_at": time.time(),
            "paid_at": time.time(),
        }
        self.store.put(self.project)

    def test_refund_is_created_once_and_success_archives_project(self):
        service = RefundService(self.store)
        first = service.ensure_requested(self.project_id)
        second = service.ensure_requested(self.project_id)
        self.assertEqual(first["refund_no"], second["refund_no"])
        self.assertEqual(first["amount_fen"], 1990)

        client = Mock()
        client.refund.return_value = {
            "status": "0000", "trxstatus": "2",
            "orderNo": "REFUND-PROVIDER", "trxid": "CHANNEL-TRX",
        }
        with patch("phase2.refund_service.Allinpay", return_value=client):
            service.process(first["refund_no"])

        refund = self.store.get_refund(first["refund_no"])
        project = self.store.get(self.project_id)
        self.assertEqual(refund["status"], "succeeded")
        self.assertEqual(refund["provider_order_no"], "REFUND-PROVIDER")
        self.assertEqual((project["status"], project["refund_status"]),
                         ("refunded", "succeeded"))

    def test_unknown_gateway_result_never_creates_a_second_refund(self):
        service = RefundService(self.store)
        first = service.ensure_requested(self.project_id)
        client = Mock()
        client.refund.side_effect = AllinpayError("结果未确认")
        with patch("phase2.refund_service.Allinpay", return_value=client):
            service.process(first["refund_no"])
        second = service.ensure_requested(self.project_id)
        self.assertEqual(first["refund_no"], second["refund_no"])
        self.assertEqual(second["status"], "needs_review")
        self.assertEqual(self.store.get(self.project_id)["refund_status"], "needs_review")

    def test_final_generation_failure_waits_for_user_refund_choice(self):
        project = self.store.get(self.project_id)
        project.update(status="video_processing", attempt=3, video_task_id="task-1")
        self.store.put(project)
        worker = Phase2Orchestrator(self.store)
        self.addCleanup(worker.pool.shutdown)
        self.addCleanup(worker.photo_pool.shutdown)
        with patch.object(worker, "_json", return_value={
                "status": "failed", "error": "模型明确失败"}):
            worker._poll_once(worker.store.get(self.project_id), "video")
        project = self.store.get(self.project_id)
        self.assertEqual(project["status"], "terminated")
        self.assertEqual(project["refund_status"], "available")
        self.assertIsNone(self.store.refund_for_project(self.project_id))

    def test_explicit_gateway_rejection_marks_refund_failed(self):
        service = RefundService(self.store)
        refund = service.ensure_requested(self.project_id)
        client = Mock()
        client.refund.side_effect = AllinpayResponseError("退款被拒绝", "E100")
        with patch("phase2.refund_service.Allinpay", return_value=client):
            service.process(refund["refund_no"])
        saved = self.store.get_refund(refund["refund_no"])
        self.assertEqual((saved["status"], saved["error_code"]), ("failed", "E100"))
        self.assertEqual(self.store.get(self.project_id)["status"], "refund_failed")

    def test_processing_refund_is_requeued_and_media_cleanup_is_retried(self):
        service = RefundService(self.store)
        refund = service.ensure_requested(self.project_id)
        client = Mock()
        client.refund.return_value = {
            "status": "0000", "merchantOrderNo": refund["refund_no"],
            "orderNo": "REFUND-PROCESSING", "trxstatus": "0",
        }
        with patch("phase2.refund_service.Allinpay", return_value=client):
            service.process(refund["refund_no"])
        saved = self.store.get_refund(refund["refund_no"])
        self.assertEqual(saved["status"], "processing")
        saved["next_run"] = 0
        self.store.put_refund(saved)
        self.assertIn(refund["refund_no"],
                      [item["refund_no"] for item in self.store.due_refunds()])

        client.refund.side_effect = AllinpayResponseError("原退款单已被受理", "DUPLICATE")
        with patch("phase2.refund_service.Allinpay", return_value=client):
            service.process(refund["refund_no"])
        self.assertEqual(self.store.get_refund(refund["refund_no"])["status"], "needs_review")
        self.assertEqual(self.store.get(self.project_id)["refund_status"], "needs_review")

        project = self.store.get(self.project_id)
        project.update(status="refunded", refund_status="succeeded",
                       portrait_asset_id="a" * 32, video_task_id="b" * 32,
                       media_cleanup="pending")
        self.store.put(project)
        with patch("common.storage_client.delete_portrait") as portrait, patch(
                "common.storage_client.delete_result") as video:
            service.cleanup_media(project)
        portrait.assert_called_once_with("a" * 32)
        video.assert_called_once_with("b" * 32)
        self.assertEqual(self.store.get(self.project_id)["media_cleanup"], "done")

    def test_completed_subscription_notice_is_retried_until_sent(self):
        project = self.store.get(self.project_id)
        project.update(status="completed", subscribe_openid="openid", subscribe_sent=False,
                       subscribe_next_at=0, completed_at=time.time())
        self.store.put(project)
        pending = self.store.pending_subscriptions()
        self.assertEqual([item["project_id"] for item in pending], [self.project_id])
        self.store.mark_subscribe_result(self.project_id, False, "temporary")
        self.assertTrue(self.store.get(self.project_id)["subscribe_next_at"] > time.time())
        saved = self.store.get(self.project_id)
        saved["subscribe_next_at"] = 0
        self.store.put(saved)
        self.store.mark_subscribed(self.project_id)
        self.assertTrue(self.store.get(self.project_id)["subscribe_sent"])


class RefundGatewayContract(unittest.TestCase):
    def test_refund_uses_original_order_full_amount_and_signed_get(self):
        env = {
            "ALLINPAY_MERCHANT_NO": "merchant",
            "ALLINPAY_SECRET_KEY": "secret",
            "ALLINPAY_REFUND_API_URL": "https://payments.example/refund",
            "ALLINPAY_REFUND_VERSION": "v1.0",
        }
        response = Mock(status_code=200)
        response.json.return_value = {
            "status": "0000", "merchantOrderNo": "RF1",
            "orderNo": "REF1", "trxid": "TRX1", "trxstatus": "2",
        }
        with patch.dict(os.environ, env, clear=False), patch(
                "phase2.allinpay.requests.get", return_value=response) as get:
            result = Allinpay().refund("P2PAYTEST", 1990, "RF1")
        params = get.call_args.kwargs["params"]
        self.assertEqual(params["version"], "v1.0")
        self.assertEqual(params["oriMerchantOrderNo"], "P2PAYTEST")
        self.assertEqual(params["merchantOrderNo"], "RF1")
        self.assertEqual(params["amount"], "1990")
        self.assertEqual(params["sign"], sign_params(params, "secret"))
        self.assertEqual(result["trxstatus"], "2")

    def test_refund_response_amount_mismatch_is_rejected(self):
        env = {
            "ALLINPAY_MERCHANT_NO": "merchant",
            "ALLINPAY_SECRET_KEY": "secret",
            "ALLINPAY_REFUND_API_URL": "https://payments.example/refund",
        }
        response = Mock(status_code=200)
        response.json.return_value = {
            "status": "0000", "merchantOrderNo": "RF1",
            "merchantNo": "merchant", "amount": "1", "trxstatus": "2",
        }
        with patch.dict(os.environ, env, clear=False), patch(
                "phase2.allinpay.requests.get", return_value=response):
            with self.assertRaises(AllinpayResponseError):
                Allinpay().refund("P2PAYTEST", 1990, "RF1")


if __name__ == "__main__":
    unittest.main()
