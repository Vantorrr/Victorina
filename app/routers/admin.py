from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi import HTTPException

from app.routers.hall import broadcast_to_hall
from app.db import get_connection
from app.fixtures import build_default_fixture
import json
import csv
from io import StringIO
from fastapi import Depends
from fastapi import Request as FastAPIRequest
from app.bot import send_question_to_captains


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    return templates.TemplateResponse("admin.html", {"request": request})


@router.post("/admin/broadcast")
async def admin_broadcast(payload: dict):
    # Простая заглушка для рассылки на экран зала
    await broadcast_to_hall(payload)
    return {"ok": True}


@router.post("/admin/show-final-results")
async def admin_show_final_results():
    """Показать финальные результаты на экране зала."""
    data = await admin_final_results()
    results_text = "\n".join([
        f"{r['team']}: {r['total']} очков — {r['level']}"
        for r in data["results"]
    ])
    await broadcast_to_hall({"type": "results", "text": f"ИТОГИ ВИКТОРИНЫ\n\n{results_text}"})
    return {"ok": True}


@router.post("/admin/load-fixtures")
async def load_fixtures(payload: dict):
    """Загрузка фикстур вопросов. Ожидает структуру: { game_name, round: 1|2, questions: [...] }"""
    game_name = payload.get("game_name")
    round_number = payload.get("round")
    questions = payload.get("questions", [])
    if not game_name or not round_number or not questions:
        raise HTTPException(status_code=400, detail="game_name, round, questions обязательны")

    with get_connection() as conn:
        cur = conn.execute("INSERT INTO games(name, status, current_round) VALUES (?, 'active', ?)", (game_name, round_number))
        game_id = cur.lastrowid
        cur = conn.execute("INSERT INTO rounds(game_id, number, status) VALUES (?, ?, 'active')", (game_id, round_number))
        round_id = cur.lastrowid

        order_index = 1
        for q in questions:
            q_text = q["text"]
            options = q["options"]
            q_type = q.get("type", "single")  # single|multi|case
            correct_index = q.get("correct_index", 0)
            correct_indices = q.get("correct_indices")
            scoring = q.get("scoring")  # dict code->weight
            conn.execute(
                """
                INSERT INTO questions(round_id, order_index, text, options_json, correct_index, type, correct_indices_json, scoring_weights_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    round_id,
                    order_index,
                    q_text,
                    json.dumps(options, ensure_ascii=False),
                    int(correct_index),
                    q_type,
                    json.dumps(correct_indices, ensure_ascii=False) if correct_indices else None,
                    json.dumps(scoring, ensure_ascii=False) if scoring else None,
                ),
            )
            order_index += 1
        conn.commit()

    return {"ok": True, "game_id": game_id, "round_id": round_id, "count": len(questions)}


@router.post("/admin/load-default")
async def load_default():
    data = build_default_fixture()
    with get_connection() as conn:
        cur = conn.execute("INSERT INTO games(name, status, current_round) VALUES (?, 'active', ?)", (data["game_name"], 1))
        game_id = cur.lastrowid
        for rnd in data["rounds"]:
            cur = conn.execute("INSERT INTO rounds(game_id, number, status) VALUES (?, ?, 'active')", (game_id, rnd["number"]))
            round_id = cur.lastrowid
            order_index = 1
            for q in rnd["questions"]:
                conn.execute(
                    """
                    INSERT INTO questions(round_id, order_index, text, options_json, correct_index, type, correct_indices_json, scoring_weights_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        round_id,
                        order_index,
                        q["text"],
                        json.dumps(q["options"], ensure_ascii=False),
                        int(q.get("correct_index", 0)),
                        q.get("type", "single"),
                        json.dumps(q.get("correct_indices"), ensure_ascii=False) if q.get("correct_indices") else None,
                        json.dumps(q.get("scoring"), ensure_ascii=False) if q.get("scoring") else None,
                    ),
                )
                order_index += 1
        conn.commit()
    return {"ok": True, "game_id": game_id}


@router.get("/admin/score")
async def admin_score():
    # Подсчёт: single — 1 балл за правильный; case — сумма весов по выбранным вариантам
    with get_connection() as conn:
        rows = conn.execute(
            """
            WITH case_points AS (
                SELECT a.team_id,
                       SUM(
                         json_extract(q.scoring_weights_json, '$.' || substr(upper(printf('%c', 65 + json_each.value)), 1))
                       ) AS pts
                FROM answers a
                JOIN questions q ON q.id = a.question_id AND q.type IN ('case','multi')
                JOIN json_each(COALESCE(a.option_indices_json, '[]'))
                GROUP BY a.team_id
            ),
            single_points AS (
                SELECT a.team_id,
                       SUM(CASE WHEN q.type='single' AND a.option_index = q.correct_index THEN 1 ELSE 0 END) AS pts
                FROM answers a
                JOIN questions q ON q.id = a.question_id
                GROUP BY a.team_id
            )
            SELECT t.name AS team,
                   COALESCE(sp.pts,0) + COALESCE(cp.pts,0) AS points
            FROM teams t
            LEFT JOIN single_points sp ON sp.team_id = t.id
            LEFT JOIN case_points cp ON cp.team_id = t.id
            ORDER BY points DESC, team ASC
            """
        ).fetchall()
    return {"score": [{"team": r["team"], "points": r["points"]} for r in rows]}


@router.get("/admin/export.csv")
async def admin_export_csv():
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT g.id AS game_id, r.number AS round, q.id AS question_id, t.name AS team, a.option_index, a.answered_at
            FROM answers a
            JOIN questions q ON q.id = a.question_id
            JOIN teams t ON t.id = a.team_id
            JOIN games g ON g.id = a.game_id
            JOIN rounds r ON r.id = q.round_id
            ORDER BY a.answered_at ASC
            """
        ).fetchall()
    out = StringIO()
    w = csv.writer(out)
    w.writerow(["game_id","round","question_id","team","option_index","answered_at"])
    for r in rows:
        w.writerow([r["game_id"], r["round"], r["question_id"], r["team"], r["option_index"], r["answered_at"]])
    return HTMLResponse(content=out.getvalue(), media_type="text/csv")


@router.post("/admin/partner-question")
async def partner_question(payload: dict, request: FastAPIRequest):
    # payload: { slide: "WORKS TEAM", text, options: [..], correct_index }
    slide = payload.get("slide")
    text = payload.get("text")
    options = payload.get("options")
    correct_index = int(payload.get("correct_index", 0))
    if not (slide and text and options and isinstance(options, list)):
        raise HTTPException(status_code=400, detail="slide, text, options обязательны")

    # 1) показать слайд на экране зала
    await broadcast_to_hall({"type": "slide", "text": slide})

    # 2) создать вопрос сразу после слайда в текущей активной игре
    with get_connection() as conn:
        game = conn.execute("SELECT * FROM games WHERE status='active' ORDER BY id DESC LIMIT 1").fetchone()
        if not game:
            cur = conn.execute("INSERT INTO games(name, status, current_round) VALUES ('Partner Game', 'active', 1)")
            game_id = cur.lastrowid
            cur = conn.execute("INSERT INTO rounds(game_id, number, status) VALUES (?, 1, 'active')", (game_id,))
            round_id = cur.lastrowid
        else:
            game_id = game["id"]
            rnd = conn.execute("SELECT id FROM rounds WHERE game_id=? AND status='active' ORDER BY number DESC LIMIT 1", (game_id,)).fetchone()
            round_id = rnd["id"] if rnd else conn.execute("INSERT INTO rounds(game_id, number, status) VALUES (?, 1, 'active')", (game_id,)).lastrowid

        # order_index = следующий
        ord_row = conn.execute("SELECT COALESCE(MAX(order_index),0)+1 AS next_idx FROM questions WHERE round_id=?", (round_id,)).fetchone()
        order_index = ord_row["next_idx"]
        cur = conn.execute(
            """
            INSERT INTO questions(round_id, order_index, text, options_json, correct_index, type)
            VALUES (?, ?, ?, ?, ?, 'single')
            """,
            (round_id, order_index, text, json.dumps(options, ensure_ascii=False), correct_index),
        )
        qid = cur.lastrowid
        conn.execute("UPDATE games SET current_question_id=?, current_question_deadline=datetime('now','+60 seconds') WHERE id=?", (qid, game_id))
        conn.commit()

    # 3) разослать капитанам с таймером 60с
    tg_app = request.app.state.tg_app if hasattr(request.app.state, 'tg_app') else None
    if tg_app is None:
        return {"ok": True, "warning": "tg bot disabled"}
    await send_question_to_captains(game_id, {"id": qid, "text": text, "options": options, "type": "single"}, tg_app.bot)
    return {"ok": True, "question_id": qid}


@router.get("/admin/final-results")
async def admin_final_results():
    """Финальная таблица с уровнями по кейсам."""
    with get_connection() as conn:
        # Подсчёт по кейсам
        rows = conn.execute(
            """
            WITH case_points AS (
                SELECT a.team_id,
                       a.question_id,
                       SUM(
                         COALESCE(json_extract(q.scoring_weights_json, '$.' || upper(printf('%c', 65 + json_each.value))), 0)
                       ) AS case_pts,
                       COUNT(CASE WHEN json_extract(q.scoring_weights_json, '$.' || upper(printf('%c', 65 + json_each.value))) = 0 THEN 1 END) AS zero_count
                FROM answers a
                JOIN questions q ON q.id = a.question_id AND q.type = 'case'
                JOIN json_each(COALESCE(a.option_indices_json, '[]'))
                GROUP BY a.team_id, a.question_id
            ),
            team_case_results AS (
                SELECT team_id,
                       SUM(case_pts) AS total_case_pts,
                       SUM(zero_count) AS total_zeros
                FROM case_points
                GROUP BY team_id
            ),
            single_points AS (
                SELECT a.team_id,
                       COUNT(CASE WHEN q.type='single' AND a.option_index = q.correct_index THEN 1 END) AS correct,
                       COUNT(CASE WHEN q.type='single' THEN 1 END) AS total
                FROM answers a
                JOIN questions q ON q.id = a.question_id
                GROUP BY a.team_id
            )
            SELECT t.name AS team,
                   COALESCE(sp.correct,0) AS single_correct,
                   COALESCE(sp.total,0) AS single_total,
                   COALESCE(tcr.total_case_pts,0) AS case_points,
                   COALESCE(tcr.total_zeros,0) AS has_zero
            FROM teams t
            LEFT JOIN single_points sp ON sp.team_id = t.id
            LEFT JOIN team_case_results tcr ON tcr.team_id = t.id
            ORDER BY single_correct + COALESCE(tcr.total_case_pts,0) DESC, team ASC
            """
        ).fetchall()
    
    results = []
    for r in rows:
        single_pts = r["single_correct"]
        case_pts = r["case_points"]
        has_zero = r["has_zero"] > 0
        total = single_pts + case_pts
        
        # Определение уровня
        if has_zero or single_pts == 0:
            level = "Базовый"
        elif case_pts >= 4:
            level = "Супер-профи"
        elif case_pts >= 3:
            level = "Продвинутый"
        elif case_pts >= 1.5:
            level = "Средний"
        else:
            level = "Базовый"
        
        results.append({
            "team": r["team"],
            "single_correct": single_pts,
            "single_total": r["single_total"],
            "case_points": case_pts,
            "total": total,
            "level": level
        })
    
    return {"results": results}

