import unittest
from tennis_telegram_outcomes import favorite_statistics

class FavoriteStatisticsTests(unittest.TestCase):
    def test_history(self):
        base = dict(alert_type="favorito_primo_set", message_id=1, favorite_idx=0, finalized=True)
        records = {
            "a": dict(base, match_id=1, result_marker="❌ PRONOSTICO NON CONFERMATO"),
            "duplicate": dict(base, match_id=1, winner_idx=0),
            "b": dict(base, match_id=2, result_marker="✅ PRONOSTICO CONFERMATO"),
            "c": dict(base, match_id=3, finalized=False),
            "d": dict(base, match_id=4),
            "e": dict(base, match_id=5, alert_type="rimonta_terzo_set"),
        }
        self.assertEqual(favorite_statistics({"telegram_alerts": records}),
                         dict(matches=4, wins=1, losses=1, pending=1, unknown=1, neutral=0, win_pct=50.0))

    def test_empty(self):
        self.assertIsNone(favorite_statistics({})["win_pct"])
