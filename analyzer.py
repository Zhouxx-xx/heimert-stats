"""
数据分析模块
计算队内伤害排名、KDA、胜率等统计指标。
"""

import logging
from typing import List, Dict, Optional
from database import get_db

logger = logging.getLogger(__name__)


def calculate_team_damage_ranks(game_id: int) -> Dict[int, dict]:
    """
    计算某局游戏中每个队伍的伤害排名。
    返回: {participant_id: {rank, percentage, team_total_damage}}
    """
    with get_db() as db:
        rows = db.execute(
            "SELECT id, participant_id, team_id, total_damage_dealt_to_champions "
            "FROM participants WHERE game_id = ? ORDER BY team_id, total_damage_dealt_to_champions DESC",
            (game_id,)
        ).fetchall()

    # 按队伍分组
    teams = {}
    for row in rows:
        team_id = row["team_id"]
        if team_id not in teams:
            teams[team_id] = []
        teams[team_id].append({
            "id": row["id"],
            "participant_id": row["participant_id"],
            "damage": row["total_damage_dealt_to_champions"] or 0,
        })

    results = {}
    for team_id, members in teams.items():
        team_total = sum(m["damage"] for m in members) or 1  # 避免除零
        # 按伤害从高到低排序
        members_sorted = sorted(members, key=lambda m: m["damage"], reverse=True)
        for rank, member in enumerate(members_sorted, 1):
            results[member["participant_id"]] = {
                "team_id": team_id,
                "rank": rank,
                "total_members": len(members),
                "damage": member["damage"],
                "percentage": member["damage"] / team_total,
                "team_total_damage": team_total,
            }

    return results


def update_all_damage_ranks(game_ids: list = None):
    """
    更新对局的队内伤害排名。
    在导入新数据后调用。
    game_ids: 只更新指定的对局；None 表示更新所有对局。
    """
    if game_ids is not None:
        game_ids = [g for g in game_ids if g]
        if not game_ids:
            return 0

    with get_db() as db:
        if game_ids is None:
            rows = db.execute(
                "SELECT game_id FROM games WHERE is_remake = 0"
            ).fetchall()
        else:
            # 只对指定对局（跳过重开对局）
            placeholders = ",".join("?" * len(game_ids))
            rows = db.execute(
                f"SELECT game_id FROM games WHERE is_remake = 0 AND game_id IN ({placeholders})",
                tuple(game_ids),
            ).fetchall()

    updated = 0
    for row in rows:
        gid = row["game_id"]
        ranks = calculate_team_damage_ranks(gid)
        if not ranks:
            continue

        with get_db() as db:
            for participant_id, info in ranks.items():
                db.execute("""
                    UPDATE participants
                    SET team_damage_rank = ?, team_damage_pct = ?
                    WHERE game_id = ? AND participant_id = ?
                """, (info["rank"], info["percentage"], gid, participant_id))
        updated += 1

    logger.info(f"已更新 {updated} 场对局的伤害排名")
    return updated


def get_summary_stats(puuid: str = None, queue_id: int = None) -> Dict:
    """
    获取汇总统计数据。
    """
    with get_db() as db:
        where = ["g.is_remake = 0", "p.puuid = g.puuid"]
        params = []
        if puuid:
            where.append("g.puuid = ?")
            params.append(puuid)
        if queue_id:
            where.append("g.queue_id = ?")
            params.append(queue_id)

        where_sql = " AND ".join(where)

        # 基本统计
        basic = db.execute(f"""
            SELECT
                COUNT(*) as total_games,
                SUM(p.win) as wins,
                SUM(p.kills) as total_kills,
                SUM(p.deaths) as total_deaths,
                SUM(p.assists) as total_assists,
                SUM(p.total_damage_dealt_to_champions) as total_damage,
                SUM(p.total_damage_taken) as total_damage_taken,
                SUM(p.gold_earned) as total_gold,
                SUM(p.double_kills) as double_kills,
                SUM(p.triple_kills) as triple_kills,
                SUM(p.quadra_kills) as quadra_kills,
                SUM(p.penta_kills) as penta_kills,
                ROUND(AVG(p.total_damage_dealt_to_champions)) as avg_damage,
                ROUND(AVG(p.kills), 1) as avg_kills,
                ROUND(AVG(p.deaths), 1) as avg_deaths,
                ROUND(AVG(p.assists), 1) as avg_assists,
                ROUND(AVG(p.total_damage_dealt_to_champions) * 1.0 /
                      NULLIF(AVG(g.game_duration), 0) * 60, 1) as avg_dpm,
                ROUND(AVG(p.team_damage_pct * 100), 1) as avg_damage_pct,
                ROUND(AVG(p.team_damage_rank), 2) as avg_rank
            FROM participants p
            JOIN games g ON p.game_id = g.game_id
            WHERE {where_sql}
        """, params).fetchone()

        if not basic or not basic["total_games"]:
            return {"total_games": 0}

        stats = dict(basic)
        stats["win_rate"] = round(stats["wins"] / stats["total_games"] * 100, 1) if stats["total_games"] else 0
        stats["avg_kda"] = round(
            (stats["avg_kills"] + stats["avg_assists"]) / max(stats["avg_deaths"], 1), 2
        )

        return stats


def get_champion_stats(puuid: str = None, queue_id: int = None) -> List[Dict]:
    """获取各英雄的统计数据"""
    with get_db() as db:
        where = ["g.is_remake = 0", "p.puuid = g.puuid"]
        params = []
        if puuid:
            where.append("g.puuid = ?")
            params.append(puuid)
        if queue_id:
            where.append("g.queue_id = ?")
            params.append(queue_id)

        where_sql = " AND ".join(where)

        rows = db.execute(f"""
            SELECT
                p.champion_name, p.champion_id,
                COUNT(*) as games,
                SUM(p.win) as wins,
                ROUND(AVG(p.kills), 1) as avg_kills,
                ROUND(AVG(p.deaths), 1) as avg_deaths,
                ROUND(AVG(p.assists), 1) as avg_assists,
                ROUND(AVG(p.total_damage_dealt_to_champions)) as avg_damage,
                ROUND(AVG(p.team_damage_pct * 100), 1) as avg_damage_pct,
                ROUND(AVG(p.team_damage_rank), 2) as avg_rank
            FROM participants p
            JOIN games g ON p.game_id = g.game_id
            WHERE {where_sql}
            GROUP BY p.champion_id
            ORDER BY games DESC
        """, params).fetchall()

        results = []
        for row in rows:
            r = dict(row)
            r["win_rate"] = round(r["wins"] / r["games"] * 100, 1) if r["games"] else 0
            r["avg_kda"] = round(
                (r["avg_kills"] + r["avg_assists"]) / max(r["avg_deaths"], 1), 2
            )
            results.append(r)

        return results


def get_damage_trend(limit: int = 50, puuid: str = None, queue_id: int = None) -> List[Dict]:
    """
    获取伤害趋势数据（按时间排序的对局）
    """
    with get_db() as db:
        where = ["g.is_remake = 0", "p.puuid = g.puuid"]
        params = []
        if puuid:
            where.append("g.puuid = ?")
            params.append(puuid)
        if queue_id:
            where.append("g.queue_id = ?")
            params.append(queue_id)

        where_sql = " AND ".join(where)

        rows = db.execute(f"""
            SELECT g.game_id, g.game_creation, p.champion_name,
                   p.kills, p.deaths, p.assists,
                   p.total_damage_dealt_to_champions as damage,
                   p.team_damage_pct, p.team_damage_rank,
                   p.win,
                   g.game_duration
            FROM participants p
            JOIN games g ON p.game_id = g.game_id
            WHERE {where_sql}
            ORDER BY g.game_creation ASC
            LIMIT ?
        """, [*params, limit]).fetchall()

        return [dict(r) for r in rows]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    updated = update_all_damage_ranks()
    print(f"更新了 {updated} 场对局的伤害排名")

    stats = get_summary_stats()
    if stats.get("total_games"):
        print(f"\n=== 汇总统计 ===")
        print(f"总场次: {stats['total_games']}")
        print(f"胜率: {stats['win_rate']}%")
        print(f"平均伤害: {stats['avg_damage']}")
        print(f"平均 KDA: {stats['avg_kills']}/{stats['avg_deaths']}/{stats['avg_assists']}")
        print(f"平均队内伤害占比: {stats['avg_damage_pct']}%")
        print(f"平均队内伤害排名: {stats['avg_rank']}")
