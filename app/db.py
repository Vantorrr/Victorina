from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from datetime import datetime, timezone


DATA_DIR = Path(os.getenv("DATA_DIR", str(Path(__file__).resolve().parent.parent / "data")))
DB_PATH = DATA_DIR / "quiz.db"


def get_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Инициализация БД и создание основной схемы викторины."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        # Версия схемы
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                version INTEGER NOT NULL
            )
            """
        )
        cur = conn.execute("SELECT version FROM schema_meta WHERE id = 1")
        row = cur.fetchone()
        current_version = row["version"] if row else 0

        # Миграции по версиям
        if current_version < 1:
            conn.execute("INSERT OR REPLACE INTO schema_meta (id, version) VALUES (1, 1)")

        if current_version < 2:
            # Основные таблицы: игры, раунды, команды, капитаны, вопросы, ответы
            conn.executescript(
                """
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS games (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft', -- draft|active|finished
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    current_round INTEGER,
                    current_question_id INTEGER
                );

                CREATE TABLE IF NOT EXISTS rounds (
                    id INTEGER PRIMARY KEY,
                    game_id INTEGER NOT NULL,
                    number INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', -- pending|active|finished
                    FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS teams (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE
                );

                CREATE TABLE IF NOT EXISTS captains (
                    id INTEGER PRIMARY KEY,
                    telegram_user_id INTEGER NOT NULL UNIQUE,
                    username TEXT,
                    team_id INTEGER UNIQUE,
                    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS questions (
                    id INTEGER PRIMARY KEY,
                    round_id INTEGER NOT NULL,
                    order_index INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    options_json TEXT NOT NULL, -- JSON массив строк
                    correct_index INTEGER NOT NULL,
                    slide_url TEXT,
                    FOREIGN KEY (round_id) REFERENCES rounds(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS answers (
                    id INTEGER PRIMARY KEY,
                    game_id INTEGER NOT NULL,
                    question_id INTEGER NOT NULL,
                    team_id INTEGER NOT NULL,
                    captain_user_id INTEGER NOT NULL,
                    option_index INTEGER NOT NULL,
                    answered_at TEXT NOT NULL DEFAULT (datetime('now')),
                    UNIQUE (team_id, question_id),
                    FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE,
                    FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE,
                    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_rounds_game ON rounds(game_id);
                CREATE INDEX IF NOT EXISTS idx_questions_round ON questions(round_id);
                CREATE INDEX IF NOT EXISTS idx_answers_question ON answers(question_id);
                CREATE INDEX IF NOT EXISTS idx_answers_team ON answers(team_id);
                """
            )
            conn.execute("UPDATE schema_meta SET version = 2 WHERE id = 1")

        conn.commit()

        # v3: поддержка кейсов (мультивыбор + веса)
        if current_version < 3:
            # Добавляем поля к вопросам
            try:
                conn.execute("ALTER TABLE questions ADD COLUMN type TEXT NOT NULL DEFAULT 'single'")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE questions ADD COLUMN correct_indices_json TEXT")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE questions ADD COLUMN scoring_weights_json TEXT")
            except sqlite3.OperationalError:
                pass

            # Добавляем поле для хранения множественного выбора ответа (опционально)
            try:
                conn.execute("ALTER TABLE answers ADD COLUMN option_indices_json TEXT")
            except sqlite3.OperationalError:
                pass

            conn.execute("UPDATE schema_meta SET version = 3 WHERE id = 1")
            conn.commit()

        # v4: очки, дедлайн вопроса и чат капитана
        cur = conn.execute("SELECT version FROM schema_meta WHERE id = 1")
        row = cur.fetchone()
        current_version = row["version"] if row else 0
        if current_version < 4:
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS scores (
                        id INTEGER PRIMARY KEY,
                        game_id INTEGER NOT NULL,
                        team_id INTEGER NOT NULL,
                        points REAL NOT NULL DEFAULT 0,
                        UNIQUE (game_id, team_id),
                        FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE,
                        FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE
                    );
                    """
                )
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE games ADD COLUMN current_question_deadline TEXT")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE captains ADD COLUMN chat_id INTEGER")
            except sqlite3.OperationalError:
                pass
            conn.execute("UPDATE schema_meta SET version = 4 WHERE id = 1")
            conn.commit()

        # v5: таблица админов
        cur = conn.execute("SELECT version FROM schema_meta WHERE id = 1")
        row = cur.fetchone()
        current_version = row["version"] if row else 0
        if current_version < 5:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS admins (
                    id INTEGER PRIMARY KEY,
                    telegram_user_id INTEGER UNIQUE,
                    username TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS idx_admins_username ON admins(lower(username));
                """
            )
            conn.execute("UPDATE schema_meta SET version = 5 WHERE id = 1")
            conn.commit()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


