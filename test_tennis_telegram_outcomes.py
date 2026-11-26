import unittest
from unittest.mock import Mock, patch
import tennis_telegram_outcomes as outcomes

def match(games, status="finished"):
    return {"id":"42","event_status":status,"players":{"p1":{"name":"A"},"p2":{"name":"B"}},"score":{"games":games}}

def record(kind, favorite=None, first=0):
    return {"match_id":"42","alert_type":kind,"message_id":123,"text":"Alert",
            "players":["A","B"],"favorite_idx":favorite,"first_set_winner_idx":first,"finalized":False}

class OutcomeTests(unittest.TestCase):
    def test_documented_completed_status(self):
        m = match([[6,6],[4,3]], "")
        m.update(status="completed", outcome="completed")
        self.assertEqual(outcomes.result_of(m)[0], 0)

    def test_unresolved_not_final(self):
        m = match([[6,6],[4,3]])
        m.update(status="completed", outcome="unresolved")
        self.assertIsNone(outcomes.result_of(m))

    def test_no_result_when_live(self):
        self.assertIsNone(outcomes.result_of(match([[6,6],[4,4]],"live")))

    def test_no_result_when_incomplete(self):
        self.assertIsNone(outcomes.result_of(match([[6,2,1],[4,6,2]])))

    @patch("tennis_telegram_outcomes.requests.post")
    def test_favorite_loses_green(self, post):
        post.return_value=Mock()
        post.return_value.json.return_value={"ok":True}
        state={"telegram_alerts":{"x":record("favorito_primo_set",favorite=1)}}
        self.assertEqual(outcomes.update_from_matches(state,[match([[6,6],[4,3]])],"t","c"),1)
        self.assertIn("✅",post.call_args.kwargs["data"]["text"])
        self.assertIn("RISULTATO FINALE: 2-0",post.call_args.kwargs["data"]["text"])
        self.assertEqual(outcomes.update_from_matches(state,[match([[6,6],[4,3]])],"t","c"),0)

    @patch("tennis_telegram_outcomes.requests.post")
    def test_first_set_winner_loses_red(self, post):
        post.return_value=Mock()
        post.return_value.json.return_value={"ok":True}
        state={"telegram_alerts":{"x":record("rimonta_terzo_set",first=0)}}
        self.assertEqual(outcomes.update_from_matches(state,[match([[6,3,3],[4,6,6]])],"t","c"),1)
        self.assertIn("❌",post.call_args.kwargs["data"]["text"])

if __name__ == "__main__":
    unittest.main()
