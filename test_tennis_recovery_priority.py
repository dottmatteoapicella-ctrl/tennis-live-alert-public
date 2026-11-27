import unittest
from datetime import datetime, timezone, timedelta
from tennis_bot_nuovo import choose_result_id
class RecoveryPriorityTests(unittest.TestCase):
    def test_rotation(self):
        now=datetime.now(timezone.utc)
        state={"telegram_alerts":{"a":{"match_id":"1","message_id":1,"created_at":"2026-11-25"},"b":{"match_id":"2","message_id":2,"created_at":"2026-11-26"}}}
        self.assertEqual(choose_result_id(state,["1","2"],now),"1")
        self.assertEqual(choose_result_id(state,["1","2"],now+timedelta(minutes=30)),"2")
        self.assertEqual(choose_result_id(state,["1","2"],now+timedelta(hours=1)),"1")
        self.assertEqual(choose_result_id(state,["2"],now+timedelta(hours=2)),"2")
        self.assertNotIn("1",state["result_attempts"])
