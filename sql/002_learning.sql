-- 002 自学习回路：预测不可变记录 + 幂等结算
-- 纪律（betting-agent 的教训）：
--  1. predictions 写入后永不 UPDATE 概率/版本/信号，只允许 settlements 追加赛果
--  2. prediction_id 是幂等键：batch 用确定性 uuid5，重复跑同一天 batch 是 no-op
--  3. 结算只接受已开赛的比赛（无前视），由 settle.py 保证

CREATE EXTENSION IF NOT EXISTS pgcrypto;

ALTER TABLE predictions ADD COLUMN IF NOT EXISTS prediction_id UUID NOT NULL DEFAULT gen_random_uuid();
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS model_version TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS league TEXT NOT NULL DEFAULT '';
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS kickoff_at TIMESTAMPTZ;
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS predicted_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS signals JSONB NOT NULL DEFAULT '{}';
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS weights JSONB NOT NULL DEFAULT '{}';
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS derivatives JSONB NOT NULL DEFAULT '{}';
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS odds_snapshot JSONB NOT NULL DEFAULT '{}';
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS divergence JSONB;  -- NULL = 门控未触发

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_predictions_prediction_id') THEN
        ALTER TABLE predictions ADD CONSTRAINT uq_predictions_prediction_id UNIQUE (prediction_id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_predictions_model_version ON predictions(model_version);
CREATE INDEX IF NOT EXISTS idx_predictions_kickoff ON predictions(kickoff_at);
CREATE INDEX IF NOT EXISTS idx_predictions_league ON predictions(league);

-- 结算表：只追加，prediction_id 去重保证幂等；永不修改 predictions
CREATE TABLE IF NOT EXISTS settlements (
    prediction_id UUID PRIMARY KEY REFERENCES predictions(prediction_id) ON DELETE CASCADE,
    home_goals  INT NOT NULL,
    away_goals  INT NOT NULL,
    ht_home     INT,
    ht_away     INT,
    yellow_home INT,   -- 为红黄牌预测预留
    yellow_away INT,
    red_home    INT,
    red_away    INT,
    source      TEXT NOT NULL DEFAULT '',
    settled_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
