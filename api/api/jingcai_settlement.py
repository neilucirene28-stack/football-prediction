"""竞彩专用不可变结算；牌数缺失留NULL，不推测零牌。"""
from engine.jingcai_cards import count


def save_jingcai_settlement(conn, prediction_id, home_goals, away_goals, *,
                           ht_home=None, ht_away=None, yellow_home=None, yellow_away=None,
                           red_home=None, red_away=None, source):
    count(home_goals,"home_goals");count(away_goals,"away_goals")
    for key,value in (("ht_home",ht_home),("ht_away",ht_away),("yellow_home",yellow_home),
                      ("yellow_away",yellow_away),("red_home",red_home),("red_away",red_away)):
        if value is not None:count(value,key)
    if (ht_home is None)!=(ht_away is None) or (ht_home is not None and (ht_home>home_goals or ht_away>away_goals)):
        raise ValueError("半场比分缺一方或大于全场")
    if not isinstance(source,str) or not source.strip():raise ValueError("竞彩结算需记录来源")
    values=(prediction_id,home_goals,away_goals,ht_home,ht_away,yellow_home,yellow_away,red_home,red_away,source,prediction_id)
    try:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO settlements
                (prediction_id,home_goals,away_goals,ht_home,ht_away,yellow_home,yellow_away,red_home,red_away,source)
                SELECT %s,%s,%s,%s,%s,%s,%s,%s,%s,%s FROM predictions p
                WHERE p.prediction_id=%s AND p.payload->'result'->>'model'='jingcai'
                  AND COALESCE(p.payload->'result'->>'project_scope','jingcai')='jingcai'
                  AND p.kickoff_at < clock_timestamp()
                ON CONFLICT (prediction_id) DO NOTHING RETURNING prediction_id""",values)
            inserted=cur.fetchone() is not None
            if not inserted:
                cur.execute("""SELECT s.prediction_id FROM settlements s JOIN predictions p
                    ON p.prediction_id=s.prediction_id WHERE p.prediction_id=%s
                    AND p.payload->'result'->>'model'='jingcai'
                    AND COALESCE(p.payload->'result'->>'project_scope','jingcai')='jingcai'""",(prediction_id,))
                if cur.fetchone() is None:raise ValueError("未找到可结算的已开球竞彩预测")
        conn.commit();return inserted
    except Exception:
        conn.rollback();raise
