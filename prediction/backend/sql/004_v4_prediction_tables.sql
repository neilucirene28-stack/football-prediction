-- V4.0 migration: beidan_analysis / prediction_scores / ai_analysis_results
-- 向后兼容：仅新增表，不修改现有表
CREATE TABLE IF NOT EXISTS beidan_analysis (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  match_id UUID NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  sp_win NUMERIC(6,3),
  sp_draw NUMERIC(6,3),
  sp_lose NUMERIC(6,3),
  let_sp_win NUMERIC(6,3),
  let_sp_draw NUMERIC(6,3),
  let_sp_lose NUMERIC(6,3),
  change_history JSONB NOT NULL DEFAULT $$[]$$::jsonb,
  analysis_result JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_beidan_match UNIQUE (match_id)
);
CREATE INDEX IF NOT EXISTS idx_beidan_match ON beidan_analysis (match_id);

CREATE TABLE IF NOT EXISTS prediction_scores (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  match_id UUID NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  home_score INT NOT NULL,
  away_score INT NOT NULL,
  probability NUMERIC(6,4) NOT NULL,
  rank_no INT NOT NULL,
  engine_version TEXT NOT NULL DEFAULT $$v4.0$$,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_pred_score UNIQUE (match_id, home_score, away_score, engine_version)
);
CREATE INDEX IF NOT EXISTS idx_pred_scores_match ON prediction_scores (match_id, rank_no);

CREATE TABLE IF NOT EXISTS ai_analysis_results (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  match_id UUID NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  model TEXT NOT NULL,
  analysis JSONB NOT NULL,
  input_snapshot JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_ai_analysis_match_model UNIQUE (match_id, model)
);
CREATE INDEX IF NOT EXISTS idx_ai_analysis_match ON ai_analysis_results (match_id);
