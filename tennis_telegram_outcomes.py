"""Update original Telegram tennis alerts after a verified completed match."""
from datetime import datetime, timezone, timedelta
import requests

FINISHED = {"finished", "completed", "complete", "ended", "final", "ft"}

def result_with_reason(match):
    if not isinstance(match, dict):
        return None, "sets_incomplete", "games_inconsistent", "games_invalid", "games_shape", "score_missing_or_stale", "no_final_indicator", "event_status_not_final", "status_not_final", "outcome_not_completed", "invalid_match"
    outcome = str(match.get("outcome") or "").strip().lower()
    status = str(match.get("status") or "").strip().lower()
    event_status = str(match.get("event_status") or "").strip().lower()
    if outcome and outcome != "completed":
        return None
    if status and status not in FINISHED:
        return None
    if event_status and event_status not in FINISHED:
        return None
    if not (outcome == "completed" or status in FINISHED or event_status in FINISHED):
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
    return (winner, sets, " ".join(f"{a}-{b}" for a, b in zip(p1, p2))), "verified"


def result_of(match):
    return result_with_reason(match)[0]


def break_result(record, match):
    from tennis_bot_nuovo import score_of, set_winner
    score = match.get("score") or {}
    if not isinstance(score, dict) or score.get("stale") is True:
        return None
    games = score_of(match)
    idx, side = record.get("set_idx"), record.get("favorite_idx")
    if not games or not isinstance(idx, int) or idx < 0 or side not in (0,1) or idx >= len(games[0]):
        return None
    a,b = games[side][idx], games[1-side][idx]
    old = record.get("break_score")
    if not isinstance(old,list) or len(old)!=2 or a<old[0] or b<old[1]:
        return None
    # Equal games prove equal net breaks; a set win also proves recovery.
    if a == b or a > b:
        return "success", a, b
    # With an odd game count, server order is unknown. Do not infer recovery.
    if set_winner(a,b) is None:
        return None
    # If the favorite never won another game, recovery was impossible.
    if a == old[0]:
        return "failure", a, b
    # A skipped intermediate score may have contained a recovery.
    return "unknown", a, b


def update_break(record, match, token, chat_id):
    outcome = break_result(record, match)
    if outcome is None:
        return False
    status,a,b = outcome
    marker = {"success":"✅ BREAK RECUPERATO", "failure":"❌ BREAK NON RECUPERATO",
              "unknown":"⚪ RECUPERO NON VERIFICABILE"}[status]
    response = requests.post(f"https://api.telegram.org/bot{token}/editMessageText",
        data={"chat_id":chat_id,"message_id":record["message_id"],
              "text":record["text"]+f"\n\n{marker}\nSet {record['set_idx']+1}: {a}-{b} (favorito-avversario)"},
        timeout=20)
    response.raise_for_status()
    if not response.json().get("ok"):
        raise ValueError("Telegram edit not confirmed")
    record.update(finalized=True, break_outcome=status, result_marker=marker)
    return True


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
        if record.get("alert_type") == "break_recovery":
            try:
                if update_break(record, match, token, chat_id):
                    updated += 1
                else:
                    counts["unverified_result"] += 1
                    print("Tennis final validation: break_recovery_unverified")
            except (requests.RequestException, ValueError):
                counts["edit_failed"] += 1
            continue
        result, reason = result_with_reason(match)
        if result is None:
            print("Tennis final validation:", reason)
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

    records = [r for r in state.get("telegram_alerts", {}).values() if r.get("alert_type") == "break_recovery"]
    totals = {k: sum(r.get("break_outcome", "pending") == k for r in records)
              for k in ("success", "failure", "unknown", "pending")}
    verified = totals["success"] + totals["failure"]
    print("Statistiche Break Recovery:", totals, "success_pct=", round(100*totals["success"]/verified,2) if verified else None)
