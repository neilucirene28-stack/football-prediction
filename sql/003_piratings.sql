-- 003: Pi-rating 持久化评分（MVP-5）
CREATE TABLE IF NOT EXISTS pi_ratings (
    team        TEXT PRIMARY KEY,
    rating_home DOUBLE PRECISION NOT NULL DEFAULT 0,
    rating_away DOUBLE PRECISION NOT NULL DEFAULT 0,
    matches     INT NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
