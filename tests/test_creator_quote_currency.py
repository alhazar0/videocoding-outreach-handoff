import sqlite3
import unittest
from scripts import creator_data_agent as agent


class QuoteCurrencyTests(unittest.TestCase):
    def test_bare_dollar_amount_is_not_assumed_usd(self):
        for text in ('I charge $89 for a dedicated video.', 'Dedicated video: $89',
                     '$485 dedicated video, payable through PayPal.'):
            with self.subTest(text=text):
                amount, currency = agent.money_near(text, ('dedicated',))
                self.assertIsNotNone(amount)
                self.assertIsNone(currency)

    def test_explicit_prefix_and_iso_codes_are_preserved(self):
        for text, expected in [('US$90 dedicated video', (90, 'USD')),
                               ('USD 2,500 dedicated video', (2500, 'USD')),
                               ('CAD 89 dedicated video', (89, 'CAD')),
                               ('CA$89 dedicated video', (89, 'CAD')),
                               ('AU$89 dedicated video', (89, 'AUD')),
                               ('EUR 650 dedicated video', (650, 'EUR')),
                               ('£90 dedicated video', (90, 'GBP')),
                               ('€90 dedicated video', (90, 'EUR')),
                               ('₹900 dedicated video', (900, 'INR'))]:
            with self.subTest(text=text):
                self.assertEqual(agent.money_near(text, ('dedicated',)), expected)

    def test_explicit_suffix_belongs_to_same_amount(self):
        for text, currency in [('$89 USD for a dedicated video', 'USD'),
                               ('Dedicated video: $89 (CAD)', 'CAD'),
                               ('$89 dedicated video; another package is USD 120', None),
                               ('$89 dedicated video and another package is USD 120', None)]:
            with self.subTest(text=text):
                self.assertEqual(agent.money_near(text, ('dedicated',)), (89, currency))

    def test_unrecognized_prefixed_dollar_is_not_relabelled(self):
        self.assertEqual(agent.money_near('R$89 dedicated video', ('dedicated',)), (None, None))

    def test_package_is_not_a_single_short_price(self):
        for text in ('USD 1,500 package: three 30-second Shorts plus a long-form segment.',
                     'Three Shorts package: $1,500 USD.',
                     'Shorts: $1,500 for the complete package.',
                     '$500 for two short videos including a follow-up.'):
            with self.subTest(text=text):
                self.assertEqual(agent.money_near(text, ('short', 'shorts')), (None, None))

    def test_bundle_does_not_hide_separate_unit_offer(self):
        text = 'One short: USD300; two shorts package: USD500.'
        self.assertEqual(agent.money_near(text, ('short', 'shorts')), (300, 'USD'))

    def test_mentioned_budget_is_not_an_integration_quote(self):
        text = ("For a dedicated video, my proposed rate is $150 USD.\n"
                "You mentioned a general planning range of under $5,000 per collaboration.")
        self.assertEqual(agent.money_near(text, ('integration', 'mention')), (None, None))
        self.assertEqual(agent.money_near(text, ('dedicated',)), (150, 'USD'))

    def test_mention_as_a_complete_format_still_parses(self):
        self.assertEqual(agent.money_near('A natural mention: USD350.',
                                         ('integration', 'mention', 'natural mention')), (350, 'USD'))

    def test_repeated_refresh_does_not_turn_mentioned_budget_into_rate(self):
        db = self.promotion_db('Dedicated video: $150 USD.\nYou mentioned a general planning range under $5,000.')
        db.execute('UPDATE creator_profiles SET price_dedicated=NULL,price_integration=NULL')
        for _ in range(2):
            agent.refresh_promotion_creator_list(db)
            self.assertEqual(tuple(db.execute('SELECT price_dedicated,price_integration FROM creator_profiles').fetchone()), (150, None))
            self.assertEqual(tuple(db.execute('SELECT price_dedicated,price_integration FROM promotion_creator_list').fetchone()), (150, None))

    def test_repeated_refresh_does_not_repopulate_cleared_package_price(self):
        db=self.promotion_db('USD 1,500 package: three Shorts plus a long-form segment.')
        db.execute('UPDATE creator_profiles SET price_dedicated=NULL,price_shorts=NULL')
        for _ in range(2):
            agent.refresh_promotion_creator_list(db)
            self.assertIsNone(db.execute('SELECT price_shorts FROM creator_profiles').fetchone()[0])
            self.assertIsNone(db.execute('SELECT price_shorts FROM promotion_creator_list').fetchone()[0])

    def promotion_db(self, quote, currency=None):
        db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;self.addCleanup(db.close)
        db.executescript('''
          CREATE TABLE contacts(id INTEGER PRIMARY KEY,email,channel_name,channel_url,subscribers,
            category,country,language,rate_info,reply_status,do_not_contact);
          CREATE TABLE creator_profiles(contact_id INTEGER PRIMARY KEY,registered_email,
            usage_verification_status,usage_completed_tasks,marketing_status,updated_at,
            price_dedicated,price_integration,price_shorts,currency,turnaround,usage_verified_at);
          CREATE TABLE outreach_work_items(contact_id,kind);
          CREATE TABLE inbox_events(contact_id,body_text,body_preview,received_at);
          CREATE TABLE outreach_settings(key TEXT PRIMARY KEY,value,updated_at);
          INSERT INTO contacts VALUES(1,'creator@example.com','Creator',NULL,100,NULL,NULL,NULL,
            NULL,'interested',0);
          INSERT INTO creator_profiles(contact_id,registered_email,usage_verification_status,
            usage_completed_tasks,marketing_status,updated_at,price_dedicated)
            VALUES(1,'creator@example.com','completed',1,'listed','2026-10-19',89);
        ''')
        db.execute('UPDATE creator_profiles SET currency=?',(currency,))
        db.execute("INSERT INTO inbox_events VALUES(1,?,'','2026-10-19')",(quote,))
        agent.ensure_agent_schema(db)
        return db

    def test_promotion_refresh_keeps_reviewed_unknown_currency_unknown(self):
        db=self.promotion_db('I will charge $89 for a dedicated video.')
        for _ in range(2):
            self.assertEqual(agent.refresh_promotion_creator_list(db),1)
            self.assertEqual(tuple(db.execute('SELECT price_dedicated,currency FROM creator_profiles').fetchone()),(89,None))
            self.assertEqual(tuple(db.execute('SELECT price_dedicated,currency FROM promotion_creator_list').fetchone()),(89,None))

    def test_missing_currency_has_no_default(self):
        db=self.promotion_db('I offer a dedicated video.')
        agent.refresh_promotion_creator_list(db)
        self.assertIsNone(db.execute('SELECT currency FROM creator_profiles').fetchone()[0])
        self.assertIsNone(db.execute('SELECT currency FROM promotion_creator_list').fetchone()[0])

    def test_explicit_currency_persists_through_promotion_refresh(self):
        db=self.promotion_db('USD 89 for a dedicated video.')
        agent.refresh_promotion_creator_list(db)
        self.assertEqual(db.execute('SELECT currency FROM creator_profiles').fetchone()[0],'USD')
        self.assertEqual(db.execute('SELECT currency FROM promotion_creator_list').fetchone()[0],'USD')

    def test_known_reviewed_currency_is_not_replaced_by_ambiguous_dollar(self):
        db=self.promotion_db('$89 for a dedicated video.', 'CAD')
        agent.refresh_promotion_creator_list(db)
        self.assertEqual(db.execute('SELECT currency FROM promotion_creator_list').fetchone()[0],'CAD')


if __name__=='__main__':
    unittest.main()
