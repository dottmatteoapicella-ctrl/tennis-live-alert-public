"""Update original Telegram tennis alerts after a verified completed match."""
from datetime import datetime, timezone, timedelta
import requests

FINISHED = {"finished", "completed", "complete", "ended", "final", "ft"}

def result_of(match):
    if not isinstance(match, dict):
        return None
    if str(match.get("event_status") or "").strip().lower() not in FINISHED:
        return None
    score = match.get("score")
    if not isinstance(score, dict) or score.get("stale") is True:
        return None
    games = score.get("games")
    if not isinstance(games, list) or len(games) != 2:
        return None
    try:
        p1, p2 = [int(x) for x in games[0]], [int(x) for x in games[1]]
    except (TypeError, ValueError):
        return None
    if not p1 or len(p1) != len(p2) or min(p1 + p2) < 0:
        return None
    from tennis_bot_nuovo import set_winner
    winners = [set_winner(a, b) for a, b in zip(p1, p2)]
    sets = [winners.count(0), winners.count(1)]
    # No guesses for retirements, incomplete deciding sets, or tied set counts.
    if None in winners or sets[0] == sets[1] or max(sets) < 2:
        return None
    winner = 0 if sets[0] > sets[1] else 1
    return winner, sets, " ".join(f"{a}-{b}" for a, b in zip(p1, p2))

def update_from_matches(state, matches, token, chat_id):
    by_id = {str(m["id"]): m for m in matches if isinstance(m, dict) and m.get("id") is not None}
    updated = 0
    counts = dict(pending=0, missing_match=0, player_mismatch=0, unverified_result=0, invalid_record=0, edit_failed=0)
    for record in state.get("telegram_alerts", {}).values():
        if record.get("finalized") or not record.get("message_id"):
            continue
        counts["pending"] += 1
        match = by_id.get(str(record.get("match_id")))
        if not match:
            counts["missing_match"] += 1
            continue
        # Require the same player order to prevent mismatched match IDs.
        players = match.get("players") or {}
        p1 = (players.get("p1") or {}).get("name")
        p2 = (players.get("p2") or {}).get("name")
        if [p1, p2] != record.get("players"):
            counts["player_mismatch"] += 1
            continue
        result = result_of(match)
        if result is None:
            counts["unverified_result"] += 1
            continue
        winner, sets, games = result
        kind = record.get("alert_type")
        if kind == "favorito_primo_set":
            favorite = record.get("favorite_idx")
            if favorite not in (0, 1):
                counts["invalid_record"] += 1
                continue
            success = winner != favorite
        elif kind == "rimonta_terzo_set":
            first = record.get("first_set_winner_idx")
            if first not in (0, 1):
                counts["invalid_record"] += 1
                continue
            success = winner == first
        else:
            counts["invalid_record"] += 1
            continue
        marker = "✅ PRONOSTICO CONFERMATO" if success else "❌ PRONOSTICO NON CONFERMATO"
        body = (record["text"] + "\n\n" + marker +
                "\n🏁 RISULTATO FINALE: " + f"{sets[0]}-{sets[1]} set" +
                "\n🎾 Set: " + games +
                "\n🏆 Vincitore: " + record["players"][winner])
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/editMessageText",
                data={"chat_id": chat_id, "message_id": record["message_id"], "text": body},
                timeout=20,
            )
            response.raise_for_status()
            if not response.json().get("ok"):
                raise ValueError("Telegram edit not confirmed")
        except (requests.RequestException, ValueError) as exc:
            counts["edit_failed"] += 1
            print("Aggiornamento Telegram tennis fallito:", record.get("match_id"), type(exc).__name__)
            continue
        record["winner_idx"] = winner
        record["finalized"] = True
        record["result_marker"] = marker
        updated += 1
    print("Diagnostica esiti tennis:", " ".join(f"{k}={v}" for k, v in counts.items()), f"updated={updated}")
    print("Alert tennis aggiornati con finale:", updated)
    return updated


def favorite_statistics(state):
    """Count unique alerted matches with an explicitly known pre-match favorite."""
    groups = {}
    for record in state.get("telegram_alerts", {}).values():
        if record.get("alert_type") != "favorito_primo_set" or not record.get("message_id"):
            continue
        match_id = record.get("match_id")
        if match_id is None:
            continue
        groups.setdefault(str(match_id), []).append(record)
    wins = losses = pending = unknown = 0
    for records in groups.values():
        outcomes = set()
        for record in records:
            favorite = record.get("favorite_idx")
            if not record.get("finalized") or favorite not in (0, 1):
                continue
            winner = record.get("winner_idx")
            if winner in (0, 1):
                outcomes.add(winner == favorite)
            elif record.get("result_marker") == "✅ PRONOSTICO CONFERMATO":
                outcomes.add(False)
            elif record.get("result_marker") == "❌ PRONOSTICO NON CONFERMATO":
                outcomes.add(True)
        if outcomes == {True}:
            wins += 1
        elif outcomes == {False}:
            losses += 1
        elif not outcomes and all(not r.get("finalized") for r in records):
            pending += 1
        else:
            unknown += 1
    total = wins + losses
    return dict(matches=len(groups), wins=wins, losses=losses, pending=pending,
                unknown=unknown, win_pct=round(100 * wins / total, 2) if total else None)


def print_favorite_statistics(state):
    stats = favorite_statistics(state)
    print("Statistiche favoriti tennis:", " ".join(f"{k}={v}" for k, v in stats.items()))
