
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import api_monitor


LIVE_URL = "https://api.livetennisapi.com/api/public/v1/matches"
ODDS_URL = "https://moviodds.com/odds"

STATE = Path(__file__).with_name("tennis_stato_nuovo.json")
ALLOWED = {"atp", "wta", "challenger"}
UTC = timezone.utc


# ==========================================
# NOMI GIOCATORI
# ==========================================

def normalized(name):
    s = unicodedata.normalize("NFKD", str(name or ""))
    s = "".join(
        c for c in s
        if not unicodedata.combining(c)
    ).casefold()

    return " ".join(re.findall(r"[a-z0-9]+", s))


def pair_key(a, b):
    names = sorted([
        normalized(a),
        normalized(b)
    ])

    if not all(names) or names[0] == names[1]:
        return None

    return "|".join(names)


def name_matches(a, b):
    a, b = normalized(a).split(), normalized(b).split()
    return bool(a and b and sorted(a) == sorted(b))


def resolve_favorite(names, favorites):
    # Match both players, allowing reordered full names only.
    # Reject ambiguous candidates and remap odds to live names.
    matches = []
    for key, info in favorites.items():
        parts = key.split("|")
        if len(parts) != 2 or not isinstance(info, dict):
            continue
        for a, b in (parts, parts[::-1]):
            if name_matches(names[0], a) and name_matches(names[1], b):
                odds = info.get("odds", {})
                if a not in odds or b not in odds or info.get("favorite") not in (a, b):
                    continue
                mapped = dict(info)
                mapped["odds"] = {normalized(names[0]): odds[a], normalized(names[1]): odds[b]}
                mapped["favorite"] = normalized(names[0] if info["favorite"] == a else names[1])
                matches.append(mapped)
    return (matches[0], "associato") if len(matches) == 1 else (None, "ambiguo" if matches else "assente")


def players_of(match):
    players = match.get("players") or {}

    if not isinstance(players, dict):
        return None

    p1 = players.get("p1")
    p2 = players.get("p2")

    if not isinstance(p1, dict) or not isinstance(p2, dict):
        return None

    a = p1.get("name")
    b = p2.get("name")

    if not a or not b:
        return None

    return str(a), str(b)


# ==========================================
# FILTRI ATP / WTA / CHALLENGER
# ==========================================

def allowed_match(match):
    tour = str(match.get("tour") or "").lower()

    if tour not in ALLOWED:
        return False

    if (
        match.get("is_doubles") is True
        or str(match.get("draw") or "").lower() == "doubles"
    ):
        return False

    score = match.get("score") or {}

    if isinstance(score, dict) and score.get("stale") is True:
        return False

    status = str(match.get("event_status") or "").lower()

    if status in ("finished", "cancelled", "canceled"):
        return False

    return players_of(match) is not None


# ==========================================
# PUNTEGGI
# ==========================================

def score_of(match):
    score = match.get("score") or {}

    if not isinstance(score, dict):
        return None

    games = score.get("games")

    if not isinstance(games, list) or len(games) != 2:
        return None

    try:
        p1 = [int(x) for x in games[0]]
        p2 = [int(x) for x in games[1]]
    except (TypeError, ValueError):
        return None

    if (
        len(p1) != len(p2)
        or not p1
        or any(x < 0 for x in p1 + p2)
    ):
        return None

    return p1, p2


def set_winner(a, b):
    if (a >= 6 and a - b >= 2) or (a == 7 and b == 6):
        return 0

    if (b >= 6 and b - a >= 2) or (b == 7 and a == 6):
        return 1

    return None


def third_started(match, p1, p2):
    if len(p1) < 3:
        return False

    return p1[2] > 0 or p2[2] > 0


def score_text(p1, p2):
    return " ".join(
        f"{a}-{b}"
        for a, b in zip(p1, p2)
    )


# ==========================================
# CHIAMATE API
# ==========================================

def request_json(url, key, params):
    response = requests.get(
        url,
        headers={"X-API-Key": key},
        params=params,
        timeout=30
    )

    response.raise_for_status()
    return response.json()


def get_live(key):
    payload = request_json(
        LIVE_URL,
        key,
        {"status": "live"}
    )

    data = (
        payload.get("data")
        if isinstance(payload, dict)
        else None
    )

    if not isinstance(data, list):
        raise ValueError(
            "Risposta LiveTennisAPI senza elenco data"
        )

    return [
        match
        for match in data
        if isinstance(match, dict) and allowed_match(match)
    ]


# ==========================================
# QUOTE PRE-MATCH MOVIODDS
# ==========================================

def get_odds_matches(key):
    for attempt in range(2):
        try:
            response = requests.get(
                ODDS_URL,
                headers={"X-API-Key": key, "Accept": "application/json",
                         "Accept-Encoding": "gzip"},
                params={"sport": "tennis", "groups": "83"},
                timeout=30,
                allow_redirects=False,
            )
            content_type = response.headers.get("Content-Type", "").lower()
            kind = ("JSON" if "json" in content_type else
                    "HTML" if "html" in content_type else "altro")
            print("Diagnostica MoviOdds: HTTP", response.status_code, kind)
            if 300 <= response.status_code < 400:
                print("Diagnostica MoviOdds: redirect non seguito")
                return []
            response.raise_for_status()
            print("Diagnostica MoviOdds: bytes", len(response.content))
            try:
                payload = response.json()
            except ValueError:
                body = response.content.lstrip()
                shape = ("vuoto" if not body else "HTML" if body.startswith(b"<")
                         else "JSON" if body.startswith((b"{", b"["))
                         else "altro")
                print("Diagnostica MoviOdds: corpo", shape)
                return []
            matches = payload.get("matches") if isinstance(payload, dict) else None
            if not isinstance(matches, list):
                print("Diagnostica MoviOdds: formato inatteso")
                return []
            print("Diagnostica MoviOdds: autenticazione e risposta OK")
            return matches
        except requests.RequestException as exc:
            response = exc.response
            status = response.status_code if response is not None else None
            reason = ("HTTP " + str(status) if isinstance(status, int)
                      else type(exc).__name__)
            print("Diagnostica MoviOdds:", reason)
            if attempt == 0 and (isinstance(exc, (requests.ConnectionError, requests.Timeout))
                                 or status in (502, 503, 504)):
                continue
            break
        except ValueError:
            print("Diagnostica MoviOdds: JSON non valido")
            break
    print("Diagnostica MoviOdds: quote in cache soltanto")
    return []


def winner_quotes(match):
    # Accetta esclusivamente mercati pre-match.
    if match.get("in_play") is not False:
        return None

    a = match.get("home")
    b = match.get("away")

    key = pair_key(a, b)

    if not key:
        return None

    league = str(match.get("league") or "").lower()

    if any(
        x in league
        for x in (
            "utr", "itf",
            " m15", " m25",
            " w15", " w25", " w35",
            " w50", " w75", " w100"
        )
    ):
        return None

    quotes = match.get("quotes") or []
    prices = {}

    for quote in quotes:
        if not isinstance(quote, dict):
            continue

        if (
            str(quote.get("group_name") or "")
            .strip()
            .lower()
            != "to win match"
        ):
            continue

        if str(quote.get("phase") or "").lower() == "inplay":
            continue

        label = normalized(
            quote.get("label") or quote.get("side")
        )

        matching = [normalized(n) for n in (a, b) if name_matches(label, n)]
        if len(matching) != 1:
            continue
        label = matching[0]

        try:
            odds = float(quote.get("odds_dec"))
        except (TypeError, ValueError):
            continue

        if odds <= 1:
            continue

        prices.setdefault(label, odds)

    na = normalized(a)
    nb = normalized(b)

    if (
        na not in prices
        or nb not in prices
        or prices[na] == prices[nb]
    ):
        return None

    favorite = a if prices[na] < prices[nb] else b

    return key, {
        "favorite": normalized(favorite),
        "favorite_name": favorite,
        "odds": {
            na: prices[na],
            nb: prices[nb]
        },
        "recorded_at": datetime.now(UTC).isoformat(),
        "league": match.get("league")
    }


# ==========================================
# ARCHIVIO FAVORITI E ALERT
# ==========================================

def load_state():
    if not STATE.exists():
        return {
            "favorites": {},
            "sent": {},
            "telegram_alerts": {}
        }

    state = json.loads(
        STATE.read_text(encoding="utf-8")
    )

    if not isinstance(state, dict):
        raise ValueError("Archivio stato non valido")

    state.setdefault("favorites", {})
    state.setdefault("sent", {})
    state.setdefault("telegram_alerts", {})

    return state


def save_state(state):
    STATE.write_text(
        json.dumps(
            state,
            indent=2,
            ensure_ascii=False,
            sort_keys=True
        ) + "\n",
        encoding="utf-8"
    )


# ==========================================
# MESSAGGI TELEGRAM
# ==========================================

def send_telegram(token, chat_id, message):
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={
            "chat_id": chat_id,
            "text": message
        },
        timeout=20
    )

    response.raise_for_status()

    payload = response.json()
    if not payload.get("ok"):
        raise ValueError(
            "Telegram non ha confermato invio"
        )
    return (payload.get("result") or {}).get("message_id")


def prematch_odds_text(names, info):
    """
    Mostra le quote pre-match dei due giocatori.
    Se non sono state memorizzate, non inventa valori.
    """
    title = "\n💰 QUOTE PRE-MATCH\n"

    if not info:
        return title + "Non disponibili"

    prices = info.get("odds") or {}

    odd1 = prices.get(normalized(names[0]))
    odd2 = prices.get(normalized(names[1]))

    if odd1 is None or odd2 is None:
        return title + "Non disponibili"

    try:
        odd1 = float(odd1)
        odd2 = float(odd2)
    except (TypeError, ValueError):
        return title + "Non disponibili"

    return (
        title
        + f"{names[0]}: {odd1:.2f}\n"
        + f"{names[1]}: {odd2:.2f}"
    )


# ==========================================
# PROGRAMMA PRINCIPALE
# ==========================================

def reserve_tennis_request(state, kind, now):
    # UTC day is provisional until provider reset timezone is confirmed.
    day = now.astimezone(UTC).date().isoformat()
    budget = state.get("tennis_api_budget", {})
    if budget.get("day") != day:
        budget = {"day": day, "live": 0, "detail": 0, "blocked": False}
        state["tennis_api_budget"] = budget
    print(f"Budget tennis: live={budget['live']} detail={budget['detail']} results={budget.get('results', 0)} total={budget['live'] + budget['detail'] + budget.get('results', 0)} blocked={int(bool(budget.get('blocked')))}")
    if budget.get("blocked") or budget["live"] + budget["detail"] + budget.get("results", 0) >= 90:
        return False
    if kind == "detail" and budget["detail"] >= 26:
        return False
    if kind == "results" and (budget.get("results", 0) >= 30 or budget["detail"] + budget.get("results", 0) >= 30):
        return False
    budget[kind] = budget.get(kind, 0) + 1
    save_state(state)
    return True


def block_tennis_budget(state):
    state["tennis_api_budget"]["blocked"] = True
    save_state(state)



def break_recovery_candidate(match, names, info):
    # At an even number of games both players have served equally often.
    # A two-game deficit therefore proves exactly one net break down.
    score = score_of(match)
    if not score or not info:
        return None
    p1, p2 = score
    idx = len(p1) - 1
    if any(set_winner(a, b) is None for a, b in zip(p1[:idx], p2[:idx])):
        return None
    favorite = info.get("favorite")
    if favorite not in tuple(normalized(n) for n in names):
        return None
    side = 0 if favorite == normalized(names[0]) else 1
    try:
        odd = float(info["odds"][favorite])
    except (KeyError, TypeError, ValueError):
        return None
    a, b = (p1[idx], p2[idx]) if side == 0 else (p2[idx], p1[idx])
    if not 1.30 <= odd <= 1.80 or b - a != 2 or b > 5 or set_winner(a, b) is not None:
        return None
    return side, idx, a, b, odd


def observe_break_recovery(state, live, counts, now, dry_run, token, chat_id):
    for match in live:
        names = players_of(match)
        mid = str(match.get("id") or "")
        key = pair_key(*names)
        aid = mid + ":break_recovery"
        if not mid or aid in state["sent"] or aid in state["telegram_alerts"] or counts.get(key) != 1:
            continue
        info, _ = resolve_favorite(names, state["favorites"])
        candidate = break_recovery_candidate(match, names, info)
        if candidate is None:
            continue
        side, idx, a, b, odd = candidate
        message = (f"🎾 BREAK RECOVERY (TEST)\n\n{names[0]} vs {names[1]}\n"
                   f"Favorito: {names[side]} | Quota pre-match: {odd:.2f}\n"
                   f"Set {idx+1}: {a}-{b} (favorito-avversario)\n"
                   "Un break netto di svantaggio. Obiettivo: recuperarlo nello stesso set.")
        print("ALERT:", aid)
        if dry_run:
            continue
        try:
            message_id = send_telegram(token, chat_id, message)
        except (requests.RequestException, ValueError):
            print("Invio Break Recovery fallito")
            continue
        if message_id is None:
            continue
        state["telegram_alerts"][aid] = dict(match_id=mid, alert_type="break_recovery",
            message_id=message_id, text=message, players=list(names), favorite_idx=side,
            set_idx=idx, break_score=[a,b], created_at=now.isoformat(), finalized=False)
        state["sent"][aid] = now.isoformat()
        save_state(state)


def recover_results(state, live, live_key, token, chat_id, dry_run):
    # Check completed matches only for Telegram alerts still awaiting an outcome.
    if not dry_run:
        import tennis_telegram_outcomes
        tennis_telegram_outcomes.update_from_matches(state, live, token, chat_id)
        live_ids = {str(m.get("id")) for m in live}
        pending_ids = sorted({str(r.get("match_id")) for r in state["telegram_alerts"].values()
                              if not r.get("finalized") and r.get("message_id")
                              and str(r.get("match_id", "")).isdigit()
                              and str(r.get("match_id")) not in live_ids})
        # Rotate a bounded batch so older unavailable matches cannot block others.
        details = []
        # Batch completed results before spending credits on individual matches.
        # Spread the existing detail allowance across the operating day.
        now = datetime.now(UTC)
        local = now.astimezone(ZoneInfo("Europe/Rome"))
        minute = local.hour * 60 + local.minute
        slot = minute // 30
        slot_key = f"{local.date()}:{slot}"
        due = slot is not None and state.get("completed_results_slot") != slot_key
        print(f"Recupero tennis: pending={len(pending_ids)} due={int(due)}")
        if pending_ids and due and reserve_tennis_request(state, "results", now):
            state["completed_results_slot"] = slot_key
            save_state(state)
            try:
                # Single-match reads are available on the free plan; completed lists are not reliably available.
                cursor = state.get("results_cursor", 0)
                mid = pending_ids[cursor % len(pending_ids)]
                state["results_cursor"] = cursor + 1
                save_state(state)
                payload = request_json(f"{LIVE_URL}/{mid}", live_key, {})
                row = payload.get("data", payload) if isinstance(payload, dict) else payload
                rows = row if isinstance(row, list) else [row] if isinstance(row, dict) else []
                print("Recupero tennis individuale:", mid)
                if isinstance(rows, list):
                    details = [m for m in rows if isinstance(m, dict)
                               and str(m.get("id")) in pending_ids]
                    print(f"Recupero tennis elenco: rows={len(rows)} matched={len(details)}")
                    for match in details:
                        score = match.get("score")
                        score = score if isinstance(score, dict) else {}
                        def safe_status(value):
                            return value if isinstance(value, str) and value.lower() in {"finished","completed","complete","ended","final","ft","live","inprogress","scheduled","retired","cancelled","walkover","notstarted","interrupted","abandoned","postponed"} else type(value).__name__
                        print("Tennis result diagnostic:", json.dumps({
                            "status": safe_status(match.get("status")),
                            "outcome": safe_status(match.get("outcome")),
                            "event_status": safe_status(match.get("event_status")),
                            "score_stale": score.get("stale") is True,
                            "games_shape": [len(v) if isinstance(v, list) else -1 for v in score.get("games", [])] if isinstance(score.get("games"), list) else None,
                            "reason": tennis_telegram_outcomes.result_with_reason(match)[1]
                        }))
                    tennis_telegram_outcomes.update_from_matches(state, details, token, chat_id)
                else:
                    print("Risultati tennis conclusi: elenco non disponibile")
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else 0
                state["completed_batch_empty"] = True
                print("Risultati tennis dettaglio HTTP:", status)
                if status == 429:
                    block_tennis_budget(state)
            except (requests.RequestException, ValueError):
                print("Risultati tennis dettaglio non disponibile")
            save_state(state)


def main():
    live_key = os.environ.get(
        "TENNIS_API_KEY", ""
    ).strip()

    odds_key = os.environ.get(
        "MOVIODDS_API_KEY", ""
    ).strip()

    token = os.environ.get(
        "TELEGRAM_BOT_TOKEN", ""
    ).strip()

    chat_id = os.environ.get(
        "TELEGRAM_CHAT_ID", ""
    ).strip()

    missing = [
        name
        for name, value in (
            ("TENNIS_API_KEY", live_key),
            ("MOVIODDS_API_KEY", odds_key),
            ("TELEGRAM_BOT_TOKEN", token),
            ("TELEGRAM_CHAT_ID", chat_id)
        )
        if not value
    ]

    if missing:
        raise ValueError(
            "Segreti GitHub mancanti: "
            + ", ".join(missing)
        )

    state = load_state()

    local = datetime.now(UTC).astimezone(ZoneInfo("Europe/Rome"))
    if not 8 <= local.hour < 23:
        recover_results(state, [], live_key, token, chat_id, os.environ.get("TENNIS_DRY_RUN", "1").strip() != "0")
        save_state(state)
        return
    if not reserve_tennis_request(state, "live", datetime.now(UTC)):
        print("Budget LiveTennisAPI: sospeso fino al prossimo giorno UTC")
        return
    try:
        live = get_live(live_key)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 429:
            block_tennis_budget(state)
            print("Budget LiveTennisAPI: HTTP 429, sospeso")
            return
        raise
    print("Partite live valide:", len(live))

    odds_matches = get_odds_matches(odds_key)

    print("Partite MoviOdds:", len(odds_matches))

    # Conta le coppie di giocatori live,
    # evitando associazioni ambigue.
    counts = {}

    for match in live:
        names = players_of(match)
        key = pair_key(*names)

        if key:
            counts[key] = counts.get(key, 0) + 1

    # Memorizza le quote pre-match.
    odds_candidates = {}

    for match in odds_matches:
        if not isinstance(match, dict):
            continue

        item = winner_quotes(match)

        if item:
            key, info = item
            odds_candidates.setdefault(
                key, []
            ).append(info)

    print("Coppie quote pre-match valide:", len(odds_candidates))
    print("Coppie quote pre-match ambigue:", sum(len(v) != 1 for v in odds_candidates.values()))
    now = datetime.now(UTC)

    for key, candidates in odds_candidates.items():
        if len(candidates) != 1:
            continue

        # Non sostituisce il favorito già memorizzato.
        if key not in state["favorites"]:
            state["favorites"][key] = candidates[0]

    # Elimina quote memorizzate da oltre 3 giorni.
    for key, info in list(
        state["favorites"].items()
    ):
        try:
            age = now - datetime.fromisoformat(
                info["recorded_at"]
            )

            if age > timedelta(days=3):
                del state["favorites"][key]

        except (KeyError, ValueError, TypeError):
            del state["favorites"][key]

    # Elimina identificativi alert oltre 30 giorni.
    for key, timestamp in list(
        state["sent"].items()
    ):
        try:
            age = now - datetime.fromisoformat(
                timestamp
            )

            if age > timedelta(days=30):
                del state["sent"][key]

        except (ValueError, TypeError):
            del state["sent"][key]

    dry_run = (
        os.environ.get("TENNIS_DRY_RUN", "1").strip()
        != "0"
    )

    print(
        "Modalita:",
        "TEST - nessun messaggio Telegram"
        if dry_run
        else "INVIO REALE"
    )

    recover_results(state, live, live_key, token, chat_id, dry_run)
    import tennis_telegram_outcomes
    observe_break_recovery(state, live, counts, now, dry_run, token, chat_id)
    alerts = 0

    for match in live:
        names = players_of(match)
        score = score_of(match)

        if not names or not score:
            continue

        p1, p2 = score

        if len(p1) < 2:
            continue

        first = set_winner(p1[0], p2[0])

        if first is None:
            continue

        match_id = str(
            match.get("id") or ""
        ).strip()

        if not match_id:
            continue

        key = pair_key(*names)

        tournament = (
            match.get("tournament")
            or "Torneo non indicato"
        )

        # Quote disponibili per questa coppia.
        info, reason = (resolve_favorite(names, state["favorites"])
                        if key and counts.get(key) == 1 else (None, "ambiguo"))
        print("Diagnostica favorito:", reason)

        # Il testo base viene usato in entrambi gli alert.
        base = (
            f"🏆 {tournament}\n"
            f"🎾 {names[0]} vs {names[1]}\n"
            f"📊 {score_text(p1, p2)}\n"
            f"{prematch_odds_text(names, info)}"
        )

        possible = []

        # ======================================
        # ALERT 1: FAVORITO IN DIFFICOLTÀ
        # ======================================

        if (
            info
            and info.get("favorite") in (
                normalized(names[0]),
                normalized(names[1])
            )
        ):
            fav_idx = (
                0
                if info["favorite"] == normalized(names[0])
                else 1
            )

            if fav_idx != first:
                possible.append((
                    "favorito_primo_set",
                    (
                        "🔴 FAVORITO IN DIFFICOLTÀ\n\n"
                        f"{base}\n\n"
                        f"⭐ {names[fav_idx]} ha perso "
                        "il primo set "
                        "(favorito pre-match)."
                    )
                ))

        # ======================================
        # ALERT 2: RIMONTA NEL TERZO SET
        # ======================================

        if (
            len(p1) >= 3
            and third_started(match, p1, p2)
        ):
            second = set_winner(
                p1[1],
                p2[1]
            )

            if second is not None and second != first:
                comeback_idx = second

                possible.append((
                    "rimonta_terzo_set",
                    (
                        "🔥 RIMONTA NEL TERZO SET\n\n"
                        f"{base}\n\n"
                        f"👤 {names[comeback_idx]} "
                        "ha perso il primo set, "
                        "vinto il secondo e "
                        "iniziato il terzo."
                    )
                ))

        # ======================================
        # INVIO E BLOCCO DUPLICATI
        # ======================================

        for alert_type, message in possible:
            alert_id = (
                f"{match_id}:{alert_type}"
            )

            if alert_id in state["sent"]:
                continue

            print(
                "ALERT:",
                alert_id,
                names
            )

            if not dry_run:
                message_id = send_telegram(
                    token,
                    chat_id,
                    message
                )
                if message_id is not None:
                    state["telegram_alerts"][alert_id] = {
                        "match_id": match_id,
                        "alert_type": alert_type,
                        "message_id": message_id,
                        "text": message,
                        "players": list(names),
                        "favorite_idx": fav_idx if alert_type == "favorito_primo_set" else None,
                        "first_set_winner_idx": first,
                        "created_at": now.isoformat(),
                        "finalized": False,
                    }

                state["sent"][alert_id] = (
                    now.isoformat()
                )

                save_state(state)

            alerts += 1

    save_state(state)

    if not dry_run:
        tennis_telegram_outcomes.print_favorite_statistics(state)

    print("Alert rilevati:", alerts)
    print(
        "Favoriti memorizzati:",
        len(state["favorites"])
    )


if __name__ == "__main__":
    api_monitor.install()
    try:
        main()

    except (
        requests.RequestException,
        ValueError,
        OSError,
        TypeError,
        KeyError
    ) as exc:
        print(
            "ERRORE:",
            exc,
            file=sys.stderr
        )
        sys.exit(1)
