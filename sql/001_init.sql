-- football-prediction-v2 初始表结构
CREATE TABLE IF NOT EXISTS teams (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    competition TEXT NOT NULL DEFAULT '',
    UNIQUE (name, competition)
);

CREATE TABLE IF NOT EXISTS elo_ratings (
    team_id    INT PRIMARY KEY REFERENCES teams(id) ON DELETE CASCADE,
    rating     DOUBLE PRECISION NOT NULL DEFAULT 1500,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS matches (
    id           SERIAL PRIMARY KEY,
    external_id  TEXT,
    source       TEXT NOT NULL DEFAULT 'demo',
    competition  TEXT NOT NULL DEFAULT '',
    home_team    TEXT NOT NULL,
    away_team    TEXT NOT NULL,
    kickoff_at   TIMESTAMPTZ NOT NULL,
    status       TEXT NOT NULL DEFAULT 'scheduled',  -- scheduled/live/finished
    home_score   INT,
    away_score   INT,
    collected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    raw          JSONB NOT NULL DEFAULT '{}'
);
-- upsert 去重键（ON CONFLICT 依赖它；IF NOT EXISTS 保证重复执行安全）
CREATE UNIQUE INDEX IF NOT EXISTS uq_matches_external_source
    ON matches(external_id, source);
CREATE INDEX IF NOT EXISTS idx_matches_kickoff ON matches(kickoff_at);
CREATE INDEX IF NOT EXISTS idx_matches_teams ON matches(home_team, away_team);

CREATE TABLE IF NOT EXISTS odds_snapshots (
    id          SERIAL PRIMARY KEY,
    match_id    INT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    market      TEXT NOT NULL,            -- wdl_3way / asian / ou
    label       TEXT NOT NULL,            -- opening / live / closing
    home_odds   DOUBLE PRECISION,
    draw_odds   DOUBLE PRECISION,
    away_odds   DOUBLE PRECISION,
    line        DOUBLE PRECISION,         -- 亚盘盘口 / 大小球线
    home_water  DOUBLE PRECISION,
    away_water  DOUBLE PRECISION,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_odds_match ON odds_snapshots(match_id);

CREATE TABLE IF NOT EXISTS predictions (
    id            SERIAL PRIMARY KEY,
    match_id      INT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    p_home        DOUBLE PRECISION NOT NULL,
    p_draw        DOUBLE PRECISION NOT NULL,
    p_away        DOUBLE PRECISION NOT NULL,
    lambda_home   DOUBLE PRECISION,
    lambda_away   DOUBLE PRECISION,
    completeness  SMALLINT NOT NULL DEFAULT 0,
    grade         CHAR(1) NOT NULL DEFAULT 'D',
    confidence    CHAR(1) NOT NULL DEFAULT 'X',
    payload       JSONB NOT NULL DEFAULT '{}',  -- 完整 V4.2 输出
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (match_id, created_at)
);

CREATE TABLE IF NOT EXISTS backtest_results (
    id          SERIAL PRIMARY KEY,
    match_id    INT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    p_home      DOUBLE PRECISION NOT NULL,
    p_draw      DOUBLE PRECISION NOT NULL,
    p_away      DOUBLE PRECISION NOT NULL,
    outcome     SMALLINT NOT NULL,        -- 0=主胜 1=平 2=客胜
    brier       DOUBLE PRECISION NOT NULL,
    logloss     DOUBLE PRECISION NOT NULL,
    evaluated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
