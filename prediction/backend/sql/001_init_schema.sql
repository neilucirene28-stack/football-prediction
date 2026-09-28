-- 足球预测项目数据库初始化 Schema
-- 阶段三：仅创建结构，不写入业务数据

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- 1. 球队统一身份
CREATE TABLE IF NOT EXISTS teams (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  canonical_name TEXT NOT NULL,
  country TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_teams_canonical_name ON teams (canonical_name);

-- 2. 球队别名映射
CREATE TABLE IF NOT EXISTS team_aliases (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  team_id UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  source TEXT NOT NULL,
  alias_name TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_team_aliases_source_alias UNIQUE (source, alias_name)
);

-- 3. 内部统一比赛表
CREATE TABLE IF NOT EXISTS matches (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  competition TEXT,
  season TEXT,
  sporttery_no TEXT,
  kickoff_at TIMESTAMPTZ NOT NULL,
  home_team_id UUID NOT NULL REFERENCES teams(id),
  away_team_id UUID NOT NULL REFERENCES teams(id),
  status TEXT NOT NULL DEFAULT 'scheduled',
  home_score INTEGER,
  away_score INTEGER,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT chk_matches_teams_diff CHECK (home_team_id <> away_team_id)
);
CREATE INDEX IF NOT EXISTS idx_matches_kickoff_at ON matches (kickoff_at);
CREATE INDEX IF NOT EXISTS idx_matches_sporttery_no ON matches (sporttery_no);
CREATE INDEX IF NOT EXISTS idx_matches_teams_kickoff ON matches (home_team_id, away_team_id, kickoff_at);

-- 4. 不同来源比赛 ID
CREATE TABLE IF NOT EXISTS match_source_ids (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  match_id UUID NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  source TEXT NOT NULL,
  external_match_id TEXT NOT NULL,
  external_match_id2 TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_match_source_ids_source_ext UNIQUE (source, external_match_id)
);

-- 5. 采集任务记录
CREATE TABLE IF NOT EXISTS collection_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source TEXT NOT NULL,
  run_type TEXT NOT NULL DEFAULT 'manual',
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  status TEXT NOT NULL DEFAULT 'running',
  items_collected INTEGER DEFAULT 0,
  error_message TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_collection_runs_started ON collection_runs (started_at);

-- 6. 采集快照（原始抓取落库）
CREATE TABLE IF NOT EXISTS collection_snapshots (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id UUID REFERENCES collection_runs(id) ON DELETE SET NULL,
  source TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  external_id TEXT,
  raw_payload JSONB NOT NULL,
  captured_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_snapshots_source_entity ON collection_snapshots (source, entity_type);

-- 7. 近期战绩
CREATE TABLE IF NOT EXISTS team_recent_form (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  team_id UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  match_id UUID REFERENCES matches(id) ON DELETE SET NULL,
  result TEXT,
  goals_for INTEGER,
  goals_against INTEGER,
  played_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_team_recent_form_team ON team_recent_form (team_id);

-- 8. 积分榜
CREATE TABLE IF NOT EXISTS team_standings (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  team_id UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  competition TEXT NOT NULL,
  season TEXT NOT NULL,
  position INTEGER,
  played INTEGER,
  won INTEGER,
  drawn INTEGER,
  lost INTEGER,
  points INTEGER,
  goal_diff INTEGER,
  captured_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_standings_comp_season ON team_standings (competition, season);

-- 9. 欧指
CREATE TABLE IF NOT EXISTS odds_europe (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  match_id UUID NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  bookmaker TEXT NOT NULL,
  home_odds NUMERIC(8,3),
  draw_odds NUMERIC(8,3),
  away_odds NUMERIC(8,3),
  captured_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_odds_eu_match ON odds_europe (match_id);

-- 10. 亚指
CREATE TABLE IF NOT EXISTS odds_asia (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  match_id UUID NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  bookmaker TEXT NOT NULL,
  handicap TEXT,
  home_odds NUMERIC(8,3),
  away_odds NUMERIC(8,3),
  captured_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_odds_asia_match ON odds_asia (match_id);

-- 11. 大小球
CREATE TABLE IF NOT EXISTS odds_over_under (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  match_id UUID NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  bookmaker TEXT NOT NULL,
  line NUMERIC(5,2),
  over_odds NUMERIC(8,3),
  under_odds NUMERIC(8,3),
  captured_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_odds_ou_match ON odds_over_under (match_id);

-- 12. 角球指
CREATE TABLE IF NOT EXISTS odds_corners (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  match_id UUID NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  bookmaker TEXT NOT NULL,
  line NUMERIC(5,2),
  over_odds NUMERIC(8,3),
  under_odds NUMERIC(8,3),
  captured_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_odds_corner_match ON odds_corners (match_id);

-- 13. 阵容
CREATE TABLE IF NOT EXISTS match_lineups (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  match_id UUID NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  team_id UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  formation TEXT,
  is_starting BOOLEAN NOT NULL DEFAULT true,
  player_name TEXT,
  player_number INTEGER,
  position TEXT,
  captured_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_lineups_match ON match_lineups (match_id);

-- 14. 预测记录
CREATE TABLE IF NOT EXISTS predictions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  match_id UUID NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  model_name TEXT NOT NULL,
  predicted_outcome TEXT,
  predicted_home_score INTEGER,
  predicted_away_score INTEGER,
  confidence NUMERIC(5,4),
  raw_output JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_predictions_match ON predictions (match_id);

-- 15. 赛果
CREATE TABLE IF NOT EXISTS match_results (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  match_id UUID NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  home_score INTEGER,
  away_score INTEGER,
  finished_at TIMESTAMPTZ,
  captured_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_results_match ON match_results (match_id);

-- 16. 复盘
CREATE TABLE IF NOT EXISTS match_reviews (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  match_id UUID NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  prediction_id UUID REFERENCES predictions(id) ON DELETE SET NULL,
  review_text TEXT,
  accuracy_score NUMERIC(5,4),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_reviews_match ON match_reviews (match_id);
