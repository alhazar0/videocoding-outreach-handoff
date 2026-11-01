import copy
import unittest
from unittest.mock import MagicMock
from scripts import send_reviewed_simple_trial as s


class RepresentativeContextTests(unittest.TestCase):
    def setUp(self):
        self.row = dict(contact_id=1, conversation_id='target', sender_email='agent@example.com')
        self.message = dict(id='foreign', conversationId='other-thread', receivedDateTime='2026-10-01T12:00:00Z',
                            body={'content': 'Another named creator declines this campaign.'})
        self.known = dict(contact_id=2, conversation_id='other-thread', sender_email='agent@example.com')
        self.db = MagicMock()
        self.db.execute.return_value.fetchone.return_value = self.known
        self.review = dict(context_digest='current', template_digest='approved', review_note='All histories read',
                           questions_answered=['Budget'], reviewed_other_creator_inbound={
                               'foreign': dict(contact_id=2, message_digest=s.other_creator_message_digest(self.message),
                                               review_note='Explicitly names a different creator; not a global opt-out.')})

    def test_exact_individually_reviewed_foreign_creator_is_allowed(self):
        self.assertTrue(s.reviewed_other_creator_message(self.db, self.row, self.message, self.review))

    def test_changed_message_or_unreviewed_message_is_held(self):
        for changes in ({'id': 'new'}, {'body': {'content': 'Stop contacting all our creators.'}},
                        {'receivedDateTime': '2026-10-02T12:00:00Z'}, {'conversationId': 'changed'}):
            self.assertFalse(s.reviewed_other_creator_message(self.db, self.row, {**self.message, **changes}, self.review))

    def test_same_creator_same_conversation_and_unknown_identity_are_not_exempt(self):
        for known in (None, {**self.known, 'contact_id': 1}, {**self.known, 'contact_id': None},
                      {**self.known, 'conversation_id': 'target'}, {**self.known, 'sender_email': 'stranger@example.com'}):
            self.db.execute.return_value.fetchone.return_value = known
            self.assertFalse(s.reviewed_other_creator_message(self.db, self.row, self.message, self.review))

    def test_incomplete_operator_review_is_not_an_exemption(self):
        review = copy.deepcopy(self.review)
        review.pop('context_digest')
        self.assertFalse(s.reviewed_other_creator_message(self.db, self.row, self.message, review))

    def test_foreign_sent_coupon_requires_separate_review_and_verified_recipient(self):
        message = dict(id='foreign-sent', conversationId='other-thread', sentDateTime='2026-10-01T12:00:00Z',
                       body={'content': 'A coupon for another creator: FREETEST100'})
        self.assertFalse(s.reviewed_other_creator_message(self.db, self.row, message, self.review, 'sentitems'))
        self.review['reviewed_other_creator_outbound'] = {
            'foreign-sent': dict(contact_id=2, message_digest=s.other_creator_message_digest(message),
                                 review_note='Read the exact delivered body and confirmed its other creator conversation.')}
        self.assertTrue(s.reviewed_other_creator_message(self.db, self.row, message, self.review, 'sentitems'))
        self.assertIn('outreach_sent_messages', self.db.execute.call_args.args[0])
        self.assertFalse(s.reviewed_other_creator_message(self.db, self.row,
                         {**message, 'sentDateTime': '2026-10-02T12:00:00Z'}, self.review, 'sentitems'))


if __name__ == '__main__':
    unittest.main()
