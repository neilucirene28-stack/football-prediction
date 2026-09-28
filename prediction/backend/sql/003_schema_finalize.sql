-- 足球预测项目结构完善迁移（003 修正版 v3）：只增加不破坏，不执行于生成阶段

-- 0. 更新时间触发器函数（若已存在则跳过）
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 1. match_lineups 增补字段（含默认值）
ALTER TABLE match_lineups
  ADD COLUMN IF NOT EXISTS snapshot_id UUID,
  ADD COLUMN IF NOT EXISTS source TEXT,
  ADD COLUMN IF NOT EXISTS lineup_status TEXT NOT NULL DEFAULT 'not_announced',
  ADD COLUMN IF NOT EXISTS announced_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS starting_lineup JSONB NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS substitutes JSONB NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS payload JSONB NOT NULL DEFAULT '{}'::jsonb;
CREATE INDEX IF NOT EXISTS idx_match_lineups_match_team
  ON match_lineups (match_id, team_id);
CREATE INDEX IF NOT EXISTS idx_match_lineups_snapshot
  ON match_lineups (snapshot_id);

-- 2. prediction_runs：一场比赛在某快照基础上的一次模型预测
CREATE TABLE IF NOT EXISTS prediction_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  match_id UUID NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  snapshot_id UUID REFERENCES match_snapshots(id) ON DELETE SET NULL,
  model_version TEXT NOT NULL,
  generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  data_completeness JSONB NOT NULL DEFAULT '{}'::jsonb,
  feature_payload JSONB,
  model_outputs JSONB NOT NULL DEFAULT '{}'::jsonb,
  consistency_checks JSONB NOT NULL DEFAULT '{}'::jsonb,
  report_payload JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_prediction_runs_match_generated
  ON prediction_runs (match_id, generated_at DESC);
CREATE INDEX IF NOT EXISTS idx_prediction_runs_snapshot
  ON prediction_runs (snapshot_id);

-- 3. match_results 补充字段与 UNIQUE
ALTER TABLE match_results
  ADD COLUMN IF NOT EXISTS result TEXT,
  ADD COLUMN IF NOT EXISTS settled_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS payload JSONB;
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'uq_match_results_match_id'
      AND conrelid = 'match_results'::regclass
  ) THEN
    ALTER TABLE match_results ADD CONSTRAINT uq_match_results_match_id UNIQUE (match_id);
  END IF;
END $$;

-- 4. prediction_reviews：关联 prediction_runs 与 match_results
CREATE TABLE IF NOT EXISTS prediction_reviews (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  prediction_run_id UUID NOT NULL REFERENCES prediction_runs(id) ON DELETE CASCADE,
  match_result_id UUID NOT NULL REFERENCES match_results(id) ON DELETE CASCADE,
  probability_error JSONB,
  missing_data_at_prediction JSONB NOT NULL DEFAULT '[]'::jsonb,
  direction_conflicts JSONB NOT NULL DEFAULT '[]'::jsonb,
  review_notes JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_prediction_reviews_run ON prediction_reviews (prediction_run_id);
CREATE INDEX IF NOT EXISTS idx_prediction_reviews_result ON prediction_reviews (match_result_id);

-- 5. teams / matches updated_at 触发器（若不存在）
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_teams_updated_at' AND tgrelid = 'teams'::regclass) THEN
    CREATE TRIGGER trg_teams_updated_at BEFORE UPDATE ON teams
      FOR EACH ROW EXECUTE FUNCTION set_updated_at();
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_matches_updated_at' AND tgrelid = 'matches'::regclass) THEN
    CREATE TRIGGER trg_matches_updated_at BEFORE UPDATE ON matches
      FOR EACH ROW EXECUTE FUNCTION set_updated_at();
  END IF;
END $$;

-- 6. team_recent_form 补 wins/draws/losses
ALTER TABLE team_recent_form
  ADD COLUMN IF NOT EXISTS wins INTEGER,
  ADD COLUMN IF NOT EXISTS draws INTEGER,
  ADD COLUMN IF NOT EXISTS losses INTEGER;

-- 7. team_standings 补积分字段
ALTER TABLE team_standings
  ADD COLUMN IF NOT EXISTS goals_for INTEGER,
  ADD COLUMN IF NOT EXISTS goals_against INTEGER,
  ADD COLUMN IF NOT EXISTS wins INTEGER,
  ADD COLUMN IF NOT EXISTS draws INTEGER,
  ADD COLUMN IF NOT EXISTS losses INTEGER;

-- 8. collection_runs 默认值（不改 NOT NULL）
ALTER TABLE collection_runs
  ALTER COLUMN total_matches SET DEFAULT 0,
  ALTER COLUMN success_matches SET DEFAULT 0,
  ALTER COLUMN partial_matches SET DEFAULT 0,
  ALTER COLUMN failed_matches SET DEFAULT 0,
  ALTER COLUMN error_summary SET DEFAULT '{}'::jsonb;

-- 9. match_snapshots 默认值（不改 NOT NULL）
ALTER TABLE match_snapshots
  ALTER COLUMN data_status SET DEFAULT 'partial',
  ALTER COLUMN completeness SET DEFAULT '{}'::jsonb;

-- 10. 所有 snapshot_id 外键改为幂等安全 DO 块
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_recent_form_snapshot' AND conrelid = 'team_recent_form'::regclass) THEN
    ALTER TABLE team_recent_form ADD CONSTRAINT fk_recent_form_snapshot FOREIGN KEY (snapshot_id) REFERENCES match_snapshots(id) ON DELETE SET NULL;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_standings_snapshot' AND conrelid = 'team_standings'::regclass) THEN
    ALTER TABLE team_standings ADD CONSTRAINT fk_standings_snapshot FOREIGN KEY (snapshot_id) REFERENCES match_snapshots(id) ON DELETE SET NULL;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_odds_eu_snapshot' AND conrelid = 'odds_europe'::regclass) THEN
    ALTER TABLE odds_europe ADD CONSTRAINT fk_odds_eu_snapshot FOREIGN KEY (snapshot_id) REFERENCES match_snapshots(id) ON DELETE SET NULL;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_odds_asia_snapshot' AND conrelid = 'odds_asia'::regclass) THEN
    ALTER TABLE odds_asia ADD CONSTRAINT fk_odds_asia_snapshot FOREIGN KEY (snapshot_id) REFERENCES match_snapshots(id) ON DELETE SET NULL;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_odds_ou_snapshot' AND conrelid = 'odds_over_under'::regclass) THEN
    ALTER TABLE odds_over_under ADD CONSTRAINT fk_odds_ou_snapshot FOREIGN KEY (snapshot_id) REFERENCES match_snapshots(id) ON DELETE SET NULL;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_odds_corner_snapshot' AND conrelid = 'odds_corners'::regclass) THEN
    ALTER TABLE odds_corners ADD CONSTRAINT fk_odds_corner_snapshot FOREIGN KEY (snapshot_id) REFERENCES match_snapshots(id) ON DELETE SET NULL;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_match_lineups_snapshot' AND conrelid = 'match_lineups'::regclass) THEN
    ALTER TABLE match_lineups ADD CONSTRAINT fk_match_lineups_snapshot FOREIGN KEY (snapshot_id) REFERENCES match_snapshots(id) ON DELETE SET NULL;
  END IF;
END $$;

-- 11. odds_* 的 bookmaker 保持 NOT NULL，company 保持 nullable，不改动
-- 旧表 predictions 与 match_reviews 保留不删除
-- 第一版采集 API 写入 odds_europe、odds_asia、
-- odds_over_under、odds_corners 时，
-- bookmaker 和 company 必须同时写入相同的公司名称。
-- 本阶段不修改 bookmaker NOT NULL，不删除 company。
