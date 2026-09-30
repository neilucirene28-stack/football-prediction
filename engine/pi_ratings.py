"""Pi-rating 持久化评分（penaltyblog）—— 第 4 信号，影子模式先行。

- 在线评分：每场赛后 update_ratings，零和更新，主客场分开
- 冷启动：双方都未见过 → 无信号（不输出）；一方未见过 → 视为平均水平
- 上线前只跑影子：scripts/pi_shadow.py 按时间重放 settlements，
  one-step-ahead 记录"当时会预测什么"，不碰 predictor()
"""
from penaltyblog.ratings.pi import PiRatingSystem


class PiRatingStore:
    def __init__(self, alpha: float = 0.15, beta: float = 0.1,
                 k: float = 0.75, sigma: float = 1.0):
        self._pi = PiRatingSystem(alpha=alpha, beta=beta, k=k, sigma=sigma)
        self._matches: dict[str, int] = {}

    # -- 预测（影子） --
    def known(self, team: str) -> bool:
        return self._matches.get(team, 0) > 0

    def predict_proba(self, home: str, away: str):
        """返回 (p_home, p_draw, p_away)；双方都未知时返回 None。"""
        if not self.known(home) and not self.known(away):
            return None
        probs = self._pi.calculate_match_probabilities(home, away)
        ph, pd, pa = (float(probs["home_win"]), float(probs["draw"]),
                      float(probs["away_win"]))
        s = ph + pd + pa
        return (ph / s, pd / s, pa / s) if s > 0 else None

    # -- 赛后更新 --
    def update(self, home: str, away: str, home_goals: int, away_goals: int,
               date=None):
        self._pi.update_ratings(home, away, home_goals - away_goals, date=date)
        self._matches[home] = self._matches.get(home, 0) + 1
        self._matches[away] = self._matches.get(away, 0) + 1

    def rating(self, team: str):
        r = self._pi.team_ratings.get(team)
        if r is None:
            return None
        return {"home": float(r["home"]), "away": float(r["away"]),
                "matches": self._matches.get(team, 0)}

    # -- 持久化 --
    def to_state(self) -> dict:
        return {team: {"home": float(r["home"]), "away": float(r["away"]),
                       "matches": self._matches.get(team, 0)}
                for team, r in self._pi.team_ratings.items()}

    def from_state(self, state: dict):
        for team, r in state.items():
            self._pi.team_ratings[team] = {"home": float(r["home"]),
                                           "away": float(r["away"])}
            self._matches[team] = int(r.get("matches", 0))

    def load_from_db(self, conn):
        with conn.cursor() as cur:
            cur.execute("SELECT team, rating_home, rating_away, matches "
                        "FROM pi_ratings")
            self.from_state({team: {"home": rh, "away": ra, "matches": m}
                             for team, rh, ra, m in cur.fetchall()})

    def save_to_db(self, conn):
        with conn.cursor() as cur:
            for team, r in self.to_state().items():
                cur.execute(
                    """INSERT INTO pi_ratings (team, rating_home, rating_away,
                       matches, updated_at)
                       VALUES (%s,%s,%s,%s,now())
                       ON CONFLICT (team) DO UPDATE SET
                           rating_home=EXCLUDED.rating_home,
                           rating_away=EXCLUDED.rating_away,
                           matches=EXCLUDED.matches,
                           updated_at=now()""",
                    (team, r["home"], r["away"], r["matches"]))
        conn.commit()
