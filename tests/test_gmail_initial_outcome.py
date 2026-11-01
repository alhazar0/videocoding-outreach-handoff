import smtplib
import unittest
from unittest.mock import MagicMock, patch
from scripts import outreach_scheduler as scheduler


class GmailInitialOutcomeTests(unittest.TestCase):
    def send(self, *, connect_error=None, login_error=None, send_error=None, close_error=None):
        context = MagicMock()
        smtp = context.__enter__.return_value
        if connect_error:
            context.__enter__.side_effect = connect_error
        smtp.login.side_effect = login_error
        smtp.send_message.side_effect = send_error
        context.__exit__.return_value = False
        if close_error:
            context.__exit__.side_effect = close_error
        with patch.object(scheduler, 'gmail_smtp_connection', return_value=context):
            result = scheduler.send_gmail_smtp('test-password', {'id': 99, 'email': 'creator@example.com'},
                'test-v1', 'test', 'test body', experiment_id='test', variant='test')
        return result, smtp

    def test_acceptance_remains_accepted_when_quit_disconnects(self):
        result, smtp = self.send(close_error=smtplib.SMTPServerDisconnected('quit failed'))
        self.assertEqual(result, (True, 'accepted'))
        smtp.send_message.assert_called_once()

    def test_lost_final_response_is_unknown_and_not_retried(self):
        result, smtp = self.send(send_error=smtplib.SMTPServerDisconnected('lost response'))
        self.assertFalse(result[0])
        self.assertTrue(result[1].startswith('DELIVERY_UNKNOWN:'))
        smtp.send_message.assert_called_once()

    def test_transport_timeout_during_submission_is_unknown(self):
        result, smtp = self.send(send_error=TimeoutError('timeout'))
        self.assertTrue(result[1].startswith('DELIVERY_UNKNOWN:'))
        smtp.send_message.assert_called_once()

    def test_connect_failure_is_definite_non_submission(self):
        result, smtp = self.send(connect_error=OSError('connect failure'))
        self.assertFalse(result[0])
        self.assertFalse(result[1].startswith('DELIVERY_UNKNOWN:'))
        smtp.send_message.assert_not_called()

    def test_login_disconnect_does_not_submit(self):
        result, smtp = self.send(login_error=smtplib.SMTPServerDisconnected('login failed'))
        self.assertFalse(result[0])
        self.assertFalse(result[1].startswith('DELIVERY_UNKNOWN:'))
        smtp.send_message.assert_not_called()

    def test_explicit_data_rejection_is_definite(self):
        result, smtp = self.send(send_error=smtplib.SMTPDataError(554, b'rejected'))
        self.assertFalse(result[0])
        self.assertFalse(result[1].startswith('DELIVERY_UNKNOWN:'))
        smtp.send_message.assert_called_once()

    def test_explicit_recipient_rejection_is_definite(self):
        result, _ = self.send(send_error=smtplib.SMTPRecipientsRefused({'creator@example.com': (550, b'unknown')}))
        self.assertFalse(result[0])
        self.assertFalse(result[1].startswith('DELIVERY_UNKNOWN:'))

    def test_explicit_sender_rejection_is_definite(self):
        result, _ = self.send(send_error=smtplib.SMTPSenderRefused(550, b'rejected', 'test@example.com'))
        self.assertFalse(result[0])
        self.assertFalse(result[1].startswith('DELIVERY_UNKNOWN:'))


if __name__ == '__main__':
    unittest.main()
