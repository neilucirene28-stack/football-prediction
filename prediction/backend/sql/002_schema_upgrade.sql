-- 足球预测项目增量迁移：只增加不破坏现有结构
-- 阶段三补充：基于最终需求补全采集统计、单场快照、历史赔率与竞彩表

-- 1. collection_runs 增加采集统计字段
ALTER TABLE collection_runs
  ADD COLUMN IF NOT EXISTS total_matches INTEGER,
  ADD COLUMN IF NOT EXISTS success_matches INTEGER,
  ADD COLUMN IF NOT EXISTS partial_matches INTEGER,
  ADD COLUMN IF NOT EXISTS failed_matches INTEGER,
  ADD COLUMN IF NOT EXISTS error_summary JSONB;

-- 2. team_recent_form 增加快照关联与来源
ALTER TABLE team_recent_form
  ADD COLUMN IF NOT EXISTS snapshot_id UUID,
  ADD COLUMN IF NOT EXISTS source TEXT,
  ADD COLUMN IF NOT EXISTS form_scope TEXT,
  ADD COLUMN IF NOT EXISTS payload JSONB;

-- 3. team_standings 增加比赛与快照关联
ALTER TABLE team_standings
  ADD COLUMN IF NOT EXISTS match_id UUID REFERENCES matches(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS snapshot_id UUID,
  ADD COLUMN IF NOT EXISTS source TEXT,
  ADD COLUMN IF NOT EXISTS payload JSONB;

-- 4. odds_europe 增加历史与来源字段
ALTER TABLE odds_europe
  ADD COLUMN IF NOT EXISTS snapshot_id UUID,
  ADD COLUMN IF NOT EXISTS company TEXT,
  ADD COLUMN IF NOT EXISTS odds_type TEXT,
  ADD COLUMN IF NOT EXISTS is_opening BOOLEAN,
  ADD COLUMN IF NOT EXISTS observed_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS changed_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS raw_payload JSONB;

-- 5. odds_asia 增加历史与盘口文本
ALTER TABLE odds_asia
  ADD COLUMN IF NOT EXISTS snapshot_id UUID,
  ADD COLUMN IF NOT EXISTS company TEXT,
  ADD COLUMN IF NOT EXISTS handicap_text TEXT,
  ADD COLUMN IF NOT EXISTS home_water NUMERIC(8,3),
  ADD COLUMN IF NOT EXISTS away_water NUMERIC(8,3),
  ADD COLUMN IF NOT EXISTS is_opening BOOLEAN,
  ADD COLUMN IF NOT EXISTS observed_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS changed_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS raw_payload JSONB;

-- 6. odds_over_under 增加历史与盘口文本
ALTER TABLE odds_over_under
  ADD COLUMN IF NOT EXISTS snapshot_id UUID,
  ADD COLUMN IF NOT EXISTS company TEXT,
  ADD COLUMN IF NOT EXISTS line_text TEXT,
  ADD COLUMN IF NOT EXISTS over_water NUMERIC(8,3),
  ADD COLUMN IF NOT EXISTS under_water NUMERIC(8,3),
  ADD COLUMN IF NOT EXISTS is_opening BOOLEAN,
  ADD COLUMN IF NOT EXISTS observed_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS changed_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS raw_payload JSONB;

-- 7. odds_corners 增加分类与历史
ALTER TABLE odds_corners
  ADD COLUMN IF NOT EXISTS category TEXT,
  ADD COLUMN IF NOT EXISTS snapshot_id UUID,
  ADD COLUMN IF NOT EXISTS company TEXT,
  ADD COLUMN IF NOT EXISTS is_opening BOOLEAN,
  ADD COLUMN IF NOT EXISTS observed_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS changed_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS raw_payload JSONB;

-- 8. 新增单场采集快照表（不倀Ƞ除原 collection_snapshots）
CREATE TABLE IF NOT EXISTS match_snapshots (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  match_id UUID NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  collection_run_id UUID REFERENCES collection_runs(id) ON DELETE SET NULL,
  source TEXT NOT NULL,
  collected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  data_status TEXT,
  completeness JSONB,
  raw_payload JSONB,
  normalized_payload JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_match_snapshots_match ON match_snapshots (match_id);
CREATE INDEX IF NOT EXISTS idx_match_snapshots_run ON match_snapshots (collection_run_id);

-- 9. 新增竞彩足球赔率独立表
CREATE TABLE IF NOT EXISTS odds_sporttery (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  match_id UUID NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  market TEXT NOT NULL,
  handicap TEXT,
  home_odds NUMERIC(8,3),
  draw_odds NUMERIC(8,3),
  away_odds NUMERIC(8,3),
  observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  source TEXT,
  snapshot_id UUID REFERENCES match_snapshots(id) ON DELETE SET NULL,
  raw_payload JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_odds_sporttery_match ON odds_sporttery (match_id);
CREATE INDEX IF NOT EXISTS idx_odds_sporttery_snapshot ON odds_sporttery (snapshot_id);

-- 10. 为补全字段的赔率表增加 snapshot_id 索引
CREATE INDEX IF NOT EXISTS idx_odds_eu_snapshot ON odds_europe (snapshot_id);
CREATE INDEX IF NOT EXISTS idx_odds_asia_snapshot ON odds_asia (snapshot_id);
CREATE INDEX IF NOT EXISTS idx_odds_ou_snapshot ON odds_over_under (snapshot_id);
CREATE INDEX IF NOT EXISTS idx_odds_corner_snapshot ON odds_corners (snapshot_id);
CREATE INDEX IF NOT EXISTS idx_standings_snapshot ON team_standings (snapshot_id);
CREATE INDEX IF NOT EXISTS idx_recent_form_snapshot ON team_recent_form (snapshot_id);
