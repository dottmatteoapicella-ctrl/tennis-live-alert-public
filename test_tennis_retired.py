import unittest
from unittest.mock import patch,Mock
import tennis_telegram_outcomes as t
class TestRetired(unittest.TestCase):
    def test_retired(self):
        match=dict(id=1,status="completed",outcome="retired",event_status="Retired",players={"p1":{"name":"A"},"p2":{"name":"B"}},score={"stale":False})
        row=dict(match_id=1,message_id=2,players=["A","B"],text="alert",alert_type="favorito_primo_set",favorite_idx=0)
        state={"telegram_alerts":{"a":row}}
        with patch.object(t.requests,"post") as post:
            post.return_value.json.return_value={"ok":True}
            self.assertEqual(t.update_from_matches(state,[match],"token","chat"),1)
            self.assertEqual(t.update_from_matches(state,[match],"token","chat"),0)
            self.assertEqual(post.call_count,1)
        self.assertEqual(t.favorite_statistics(state)["neutral"],1)
        self.assertIsNone(t.favorite_statistics(state)["win_pct"])
        self.assertFalse(t.retired_final(dict(match,status="live")))
