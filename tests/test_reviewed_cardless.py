import unittest
from datetime import datetime,timedelta,timezone
from scripts.execute_reviewed_cardless import eligible_account,confirmed_access,confirmed_missing,MANUAL_NINE

class CardlessSafetyTests(unittest.TestCase):
 def test_missing_requires_explicit_successful_absence(self):
  self.assertTrue(confirmed_missing(200,{'success':True,'found':False}))
  for status,p in [(500,{'success':True,'found':False}),(200,{'success':False,'found':False}),(200,{'success':True}),(200,{'success':True,'found':True})]:
   self.assertFalse(confirmed_missing(status,p))
 def test_new_manual_handoff_is_protected(self):
  self.assertTrue({20054,18240,17994,17861,15440,19256}.issubset(MANUAL_NINE))
 def payload(self):
  return {'success':True,'found':True,'creator':{'email':'test@example.com','emailVerified':True,'subscription':{'status':'none','manualGrant':False,'stripeBacked':False},'activity':{'totalTasks':0}}}
 def test_exact_verified_unused(self):self.assertTrue(eligible_account(200,self.payload(),'test@example.com'))
 def test_reject_other_email(self):self.assertFalse(eligible_account(200,self.payload(),'other@example.com'))
 def test_reject_used(self):
  p=self.payload();p['creator']['activity']['totalTasks']=1;self.assertFalse(eligible_account(200,p,'test@example.com'))
 def test_reject_unverified(self):
  p=self.payload();p['creator']['emailVerified']=False;self.assertFalse(eligible_account(200,p,'test@example.com'))
 def test_reject_stripe(self):
  p=self.payload();p['creator']['subscription']['stripeBacked']=True;self.assertFalse(eligible_account(200,p,'test@example.com'))
 def test_confirm_month_and_no_billing(self):
  p=self.payload();s={'status':'active','manualGrant':True,'stripeBacked':False,'currentPeriodEnd':(datetime.now(timezone.utc)+timedelta(days=30)).isoformat()};p['creator']['subscription']=s
  self.assertTrue(confirmed_access(200,p,'test@example.com'))
  s['stripeBacked']=True;self.assertFalse(confirmed_access(200,p,'test@example.com'))
 def test_no_expiry_no_confirmation(self):
  p=self.payload();p['creator']['subscription']={'status':'active','manualGrant':True,'stripeBacked':False};self.assertFalse(confirmed_access(200,p,'test@example.com'))

if __name__=='__main__':unittest.main()
