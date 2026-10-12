import unittest
from unittest.mock import Mock, patch

from scripts import fetch_and_push_base as app
from scripts import run_resilient as resilient


class BillingFailureTests(unittest.TestCase):
    def test_billing_failure_is_not_retried(self):
        response = Mock(status_code=402)
        with patch.object(app, "DEEPSEEK_API_KEY", "test-key"), patch.object(
            app.requests, "post", return_value=response
        ) as post, patch.object(app.time, "sleep") as sleep:
            with self.assertRaises(app.ProviderBillingError):
                app._call_deepseek_json("test")
        self.assertEqual(post.call_count, 1)
        sleep.assert_not_called()

    def test_notification_failure_does_not_mask_billing_error(self):
        with patch.object(resilient, "install_resilience"), patch.object(
            app, "main", side_effect=app.ProviderBillingError("billing")
        ), patch.object(app, "notify_billing_failure", side_effect=RuntimeError("failed")):
            with self.assertRaises(app.ProviderBillingError):
                resilient.main()

    def test_notification_checks_feishu_business_error(self):
        response = Mock()
        response.json.return_value = {"code": 19001}
        with patch.object(app, "FEISHU_WEBHOOK", "https://example.com/hook"), patch.object(
            app.requests, "post", return_value=response
        ):
            with self.assertRaises(app.PipelineError):
                app.notify_billing_failure()
