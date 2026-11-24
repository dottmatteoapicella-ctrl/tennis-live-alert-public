import unittest
from tennis_bot_nuovo import resolve_favorite, name_matches, winner_quotes

class FavoriteTests(unittest.TestCase):
    def test_reordered_names(self):
        info = {"favorite": "orlov vladyslav", "odds": {"orlov vladyslav": 1.5, "manzano juan cruz martin": 2.5}}
        result, reason = resolve_favorite(["Juan Cruz Martin Manzano", "Vladyslav Orlov"], {"manzano juan cruz martin|orlov vladyslav": info})
        self.assertEqual(reason, "associato")
        self.assertEqual(result["favorite"], "vladyslav orlov")
        self.assertEqual(result["odds"]["juan cruz martin manzano"], 2.5)

    def test_ambiguous(self):
        info = {"favorite": "a one", "odds": {"a one": 1.5, "b two": 2.5}}
        other = {"favorite": "one a", "odds": {"one a": 1.5, "two b": 2.5}}
        self.assertEqual(resolve_favorite(["A One", "B Two"], {"a one|b two": info, "one a|two b": other}), (None, "ambiguo"))

    def test_no_partial_name(self):
        self.assertFalse(name_matches("J Martin", "Juan Cruz Martin Manzano"))

    def test_no_live_odds(self):
        self.assertIsNone(winner_quotes({"in_play": True}))
