"""配置：全部来自环境变量，有合理默认值。"""
import os


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


DATABASE_URL = os.environ.get("DATABASE_URL", "")
ELO_K = _f("ELO_K", 30.0)
ELO_HOME_ADV = _f("ELO_HOME_ADV", 65.0)
DIXON_COLES_RHO = _f("DIXON_COLES_RHO", -0.13)
LEAGUE_AVG_GOALS = _f("LEAGUE_AVG_GOALS", 2.70)
KELLY_FRACTION = _f("KELLY_FRACTION", 0.25)

ENGINE_CONFIG = {
    "rho": DIXON_COLES_RHO,
    "kelly_fraction": KELLY_FRACTION,
    "model_edge": 0.0,
}
