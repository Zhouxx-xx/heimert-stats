"""
云端 MySQL 数据库模块。

功能：
- 连接腾讯云 MySQL（CynosDB）
- 上传本地对局数据（自动去重）
- 云端池查询（玩家排行、英雄排行）
- 云端不可用时降级返回 None

配置说明：
- 数据库连接信息从 cloud_config.local.json 读取（该文件已加入 .gitignore，不上传）
- 仓库中没有该文件时自动降级为"云端不可用"，不影响本地功能
- 参考模板见 cloud_config.example.json
"""

import json
import logging
import os
from typing import Optional, List, Dict
from contextlib import contextmanager

import pymysql
from pymysql.cursors import DictCursor

logger = logging.getLogger(__name__)

# ── 连接配置 ────────────────────────────────────────────────

# 配置文件路径（本机私有配置，勿上传）
_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "cloud_config.local.json")

# 占位配置：未提供本地配置文件时的默认值（云端功能自动降级关闭）
MYSQL_CONFIG = {
    "host": "your-mysql-host.example.com",
    "port": 3306,
    "user": "your-username",
    "password": "your-password",
    "database": "your-database",
    "charset": "utf8mb4",
    "connect_timeout": 5,
}

# 若存在本地私有配置文件，则覆盖占位配置
if os.path.exists(_CONFIG_PATH):
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            _local = json.load(f)
        MYSQL_CONFIG.update(_local)
    except Exception as e:
        logger.warning(f"读取 cloud_config.local.json 失败，云端功能不可用: {e}")


# ── 连接管理器 ─────────────────────────────────────────────

@contextmanager
def get_cloud():
    """获取 MySQL 连接（自动关闭）。
    连接失败时 yield None；连接成功时 yield conn，
    块内异常会 rollback 后重新抛出（不二次 yield）。"""
    conn = None
    try:
        conn = pymysql.connect(**MYSQL_CONFIG)
    except Exception as e:
        logger.warning(f"云端数据库连接失败: {e}")
        yield None
        return

    try:
        yield conn
        conn.commit()
    except Exception:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        raise
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def cloud_available() -> bool:
    """检查云端是否可连接"""
    try:
        with get_cloud() as conn:
            if conn is None:
                return False
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                return True
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════
# 上传
# ═══════════════════════════════════════════════════════════

def upload_games(
    games_data: List[Dict],
    participants_data: Dict[int, List[Dict]],
    uploader_puuid: str,
    uploader_name: str,
) -> dict:
    """
    批量上传对局和参与者数据到云端。

    games_data:          [{game_id, queue_id, game_mode, ...}, ...]
    participants_data:   {game_id: [{puuid, champion_id, ...}, ...]}
    uploader_puuid:     上传者的 puuid
    uploader_name:      上传者召唤师名

    返回: {uploaded, skipped, errors}
    """
    result = {"uploaded": 0, "skipped": 0, "errors": 0}

    with get_cloud() as conn:
        if conn is None:
            result["errors"] = len(games_data)
            return result

        with conn.cursor() as cur:
            for game in games_data:
                gid = game["game_id"]
                try:
                    # ── 检查是否已存在（去重） ──
                    cur.execute("SELECT 1 FROM cloud_games WHERE game_id = %s", (gid,))
                    if cur.fetchone():
                        result["skipped"] += 1
                        continue

                    # ── 插入 cloud_games ──
                    cur.execute("""
                        INSERT INTO cloud_games
                            (game_id, queue_id, game_mode, game_type, map_id,
                             game_creation, game_duration, game_version, platform_id,
                             is_remake, uploader_puuid, uploader_name)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        gid,
                        game.get("queue_id", 0),
                        game.get("game_mode"),
                        game.get("game_type"),
                        game.get("map_id"),
                        game.get("game_creation", 0),
                        game.get("game_duration", 0),
                        game.get("game_version"),
                        game.get("platform_id"),
                        game.get("is_remake", 0),
                        uploader_puuid,
                        uploader_name,
                    ))

                    # ── 插入参与者 ──
                    parts = participants_data.get(gid, [])
                    for p in parts:
                        cur.execute("""
                            INSERT INTO cloud_participants
                                (game_id, puuid, summoner_name,
                                 champion_id, champion_name, team_id, participant_id, win,
                                 kills, deaths, assists,
                                 total_damage_dealt_to_champions, total_damage_taken,
                                 gold_earned, total_minions_killed,
                                 total_heal, total_damage_shielded_to_others,
                                 wards_placed, wards_killed, vision_score,
                                 double_kills, triple_kills, quadra_kills, penta_kills,
                                 largest_killing_spree, time_ccing_others,
                                 summoner1_id, summoner2_id,
                                 item0, item1, item2, item3, item4, item5, item6,
                                 player_augment1, player_augment2,
                                 player_augment3, player_augment4,
                                 team_damage_rank, team_damage_pct,
                                 uploader_puuid, uploader_name)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                    %s, %s, %s, %s, %s)
                        """, (
                            gid,
                            p.get("puuid", ""),
                            p.get("summoner_name", ""),
                            p.get("champion_id", 0),
                            p.get("champion_name", ""),
                            p.get("team_id", 100),
                            p.get("participant_id", 0),
                            p.get("win", 0),
                            p.get("kills", 0),
                            p.get("deaths", 0),
                            p.get("assists", 0),
                            p.get("total_damage_dealt_to_champions", 0),
                            p.get("total_damage_taken", 0),
                            p.get("gold_earned", 0),
                            p.get("total_minions_killed", 0),
                            p.get("total_heal", 0),
                            p.get("total_damage_shielded_to_others", 0),
                            p.get("wards_placed", 0),
                            p.get("wards_killed", 0),
                            p.get("vision_score", 0),
                            p.get("double_kills", 0),
                            p.get("triple_kills", 0),
                            p.get("quadra_kills", 0),
                            p.get("penta_kills", 0),
                            p.get("largest_killing_spree", 0),
                            p.get("time_ccing_others", 0),
                            p.get("summoner1_id"),
                            p.get("summoner2_id"),
                            p.get("item0"),
                            p.get("item1"),
                            p.get("item2"),
                            p.get("item3"),
                            p.get("item4"),
                            p.get("item5"),
                            p.get("item6"),
                            p.get("player_augment1", 0),
                            p.get("player_augment2", 0),
                            p.get("player_augment3", 0),
                            p.get("player_augment4", 0),
                            p.get("team_damage_rank"),
                            p.get("team_damage_pct"),
                            uploader_puuid,
                            uploader_name,
                        ))

                    result["uploaded"] += 1

                except Exception as e:
                    logger.warning(f"上传对局 {gid} 失败: {e}")
                    result["errors"] += 1
                    # 单局失败不影响其他局

    return result


def get_upload_stats() -> dict:
    """获取云端数据统计"""
    try:
        with get_cloud() as conn:
            if conn is None:
                return {"total_games": 0, "total_players": 0, "uploaders": []}
            with conn.cursor(DictCursor) as cur:
                cur.execute("SELECT COUNT(*) as cnt FROM cloud_games WHERE is_remake = 0")
                games = cur.fetchone()["cnt"]

                cur.execute("SELECT COUNT(DISTINCT puuid) as cnt FROM cloud_participants")
                players = cur.fetchone()["cnt"]

                cur.execute("""
                    SELECT uploader_name, uploader_puuid, COUNT(DISTINCT g.game_id) as games
                    FROM cloud_games g
                    WHERE uploader_name IS NOT NULL AND g.is_remake = 0
                    GROUP BY uploader_puuid, uploader_name
                    ORDER BY games DESC
                """)
                uploaders = [dict(r) for r in cur.fetchall()]

                return {"total_games": games, "total_players": players, "uploaders": uploaders}
    except Exception as e:
        logger.warning(f"获取云端统计失败: {e}")
        return {"total_games": 0, "total_players": 0, "uploaders": []}


# ═══════════════════════════════════════════════════════════
# 排行查询（云端池）
# ═══════════════════════════════════════════════════════════

def get_cloud_player_ranking(limit: int = 50, start_date: str = None, end_date: str = None,
                             puuids: Optional[List[str]] = None) -> Optional[List[Dict]]:
    """
    玩家伤害排行：按 puuid 聚合，所有上传者的平均伤害排名。
    要求至少参与 3 局。
    start_date / end_date: "%Y-%m-%d" 格式，过滤 game_creation 时间范围。
    puuids: 只统计这些 puuid 的玩家（好友筛选）；None 表示全部。

    返回: [{summoner_name, puuid, games, avg_damage, avg_damage_pct,
            avg_rank, avg_kills, avg_deaths, avg_assists, wins, win_rate, avg_kda}]
    """
    try:
        with get_cloud() as conn:
            if conn is None:
                return None
            with conn.cursor(DictCursor) as cur:
                # 日期范围过滤
                where_extra = "g.is_remake = 0 AND p.team_damage_rank IS NOT NULL"
                params = []
                if start_date:
                    import time, calendar
                    start_dt = time.strptime(start_date + " 00:00:00", "%Y-%m-%d %H:%M:%S")
                    start_ms = int(calendar.timegm(start_dt)) * 1000
                    where_extra += " AND g.game_creation >= %s"
                    params.append(start_ms)
                if end_date:
                    import time, calendar
                    end_dt = time.strptime(end_date + " 23:59:59", "%Y-%m-%d %H:%M:%S")
                    end_ms = int(calendar.timegm(end_dt)) * 1000
                    where_extra += " AND g.game_creation <= %s"
                    params.append(end_ms)
                # 好友筛选（SQL 层过滤，保证排名序号正确）
                if puuids:
                    placeholders = ", ".join(["%s"] * len(puuids))
                    where_extra += f" AND p.puuid IN ({placeholders})"
                    params.extend(puuids)

                params.append(limit)
                cur.execute(f"""
                    SELECT
                        p.puuid,
                        p.summoner_name,
                        COUNT(*) as games,
                        ROUND(AVG(p.total_damage_dealt_to_champions)) as avg_damage,
                        ROUND(AVG(p.team_damage_pct * 100), 1) as avg_damage_pct,
                        ROUND(AVG(p.team_damage_rank), 2) as avg_rank,
                        ROUND(AVG(p.kills), 1) as avg_kills,
                        ROUND(AVG(p.deaths), 1) as avg_deaths,
                        ROUND(AVG(p.assists), 1) as avg_assists,
                        SUM(p.win) as wins
                    FROM cloud_participants p
                    JOIN cloud_games g ON p.game_id = g.game_id
                    WHERE {where_extra}
                    GROUP BY p.puuid, p.summoner_name
                    HAVING games >= 3
                    ORDER BY avg_damage DESC
                    LIMIT %s
                """, tuple(params))
                rows = cur.fetchall()
                results = []
                for r in rows:
                    d = dict(r)
                    d["win_rate"] = round(d["wins"] / d["games"] * 100, 1) if d["games"] else 0
                    d["avg_kda"] = round(
                        (d["avg_kills"] + d["avg_assists"]) / max(d["avg_deaths"], 1), 2
                    )
                    results.append(d)
                return results
    except Exception as e:
        logger.warning(f"查询云端玩家排行失败: {e}")
        return None


def get_cloud_champion_ranking(limit: Optional[int] = 50, start_date: str = None, end_date: str = None) -> Optional[List[Dict]]:
    """
    英雄伤害排行：按 champion_id 聚合，所有上传者的数据。
    start_date / end_date: "%Y-%m-%d" 格式，过滤 game_creation 时间范围。
    limit: None 表示返回全部英雄（不加 LIMIT）。

    返回: [{champion_name, champion_id, games, avg_damage, avg_damage_pct,
            avg_rank, avg_kills, avg_deaths, avg_assists, wins, win_rate}]
    """
    try:
        with get_cloud() as conn:
            if conn is None:
                return None
            with conn.cursor(DictCursor) as cur:
                where_extra = "g.is_remake = 0 AND p.team_damage_rank IS NOT NULL"
                params = []
                if start_date:
                    import time, calendar
                    start_dt = time.strptime(start_date + " 00:00:00", "%Y-%m-%d %H:%M:%S")
                    start_ms = int(calendar.timegm(start_dt)) * 1000
                    where_extra += " AND g.game_creation >= %s"
                    params.append(start_ms)
                if end_date:
                    import time, calendar
                    end_dt = time.strptime(end_date + " 23:59:59", "%Y-%m-%d %H:%M:%S")
                    end_ms = int(calendar.timegm(end_dt)) * 1000
                    where_extra += " AND g.game_creation <= %s"
                    params.append(end_ms)

                limit_sql = ""
                if limit is not None:
                    limit_sql = " LIMIT %s"
                    params.append(limit)
                cur.execute(f"""
                    SELECT
                        p.champion_name,
                        p.champion_id,
                        COUNT(*) as games,
                        ROUND(AVG(p.total_damage_dealt_to_champions)) as avg_damage,
                        ROUND(AVG(p.team_damage_pct * 100), 1) as avg_damage_pct,
                        ROUND(AVG(p.team_damage_rank), 2) as avg_rank,
                        ROUND(AVG(p.kills), 1) as avg_kills,
                        ROUND(AVG(p.deaths), 1) as avg_deaths,
                        ROUND(AVG(p.assists), 1) as avg_assists,
                        SUM(p.win) as wins
                    FROM cloud_participants p
                    JOIN cloud_games g ON p.game_id = g.game_id
                    WHERE {where_extra}
                    GROUP BY p.champion_id, p.champion_name
                    HAVING games >= 3
                    ORDER BY avg_damage DESC{limit_sql}
                """, tuple(params))
                rows = cur.fetchall()
                results = []
                for r in rows:
                    d = dict(r)
                    d["win_rate"] = round(d["wins"] / d["games"] * 100, 1) if d["games"] else 0
                    # 补充中文名
                    from champion_cn import get_champion_name
                    d["champion_cn"] = get_champion_name(d["champion_id"], d.get("champion_name"))
                    results.append(d)
                return results
    except Exception as e:
        logger.warning(f"查询云端英雄排行失败: {e}")
        return None


def get_cloud_champion_list() -> Optional[List[Dict]]:
    """
    云端英雄列表（所有上传者的数据合并）。
    返回: [{champion_id, champion_name, games, players, avg_damage,
            avg_rank, wins, avg_kills, avg_deaths}]
    """
    try:
        with get_cloud() as conn:
            if conn is None:
                return None
            with conn.cursor(DictCursor) as cur:
                cur.execute("""
                    SELECT
                        p.champion_id,
                        p.champion_name,
                        COUNT(*) as games,
                        COUNT(DISTINCT p.puuid) as players,
                        ROUND(AVG(p.total_damage_dealt_to_champions)) as avg_damage,
                        ROUND(AVG(p.team_damage_rank), 2) as avg_rank,
                        SUM(p.win) as wins,
                        ROUND(AVG(p.kills), 1) as avg_kills,
                        ROUND(AVG(p.deaths), 1) as avg_deaths
                    FROM cloud_participants p
                    JOIN cloud_games g ON p.game_id = g.game_id
                    WHERE g.is_remake = 0
                      AND p.team_damage_rank IS NOT NULL
                    GROUP BY p.champion_id, p.champion_name
                    ORDER BY games DESC, avg_damage DESC
                """)
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.warning(f"查询云端英雄列表失败: {e}")
        return None


def get_cloud_champion_games(champion_id: int, limit: int = 100) -> Optional[List[Dict]]:
    """
    云端某个英雄的所有出场记录（所有上传者）。
    返回: participants 全字段 + game_creation, game_duration, queue_id
    """
    try:
        with get_cloud() as conn:
            if conn is None:
                return None
            with conn.cursor(DictCursor) as cur:
                cur.execute("""
                    SELECT p.*, g.game_creation, g.game_duration, g.queue_id
                    FROM cloud_participants p
                    JOIN cloud_games g ON g.game_id = p.game_id
                    WHERE p.champion_id = %s
                      AND g.is_remake = 0
                      AND p.team_damage_rank IS NOT NULL
                    ORDER BY g.game_creation DESC
                    LIMIT %s
                """, (champion_id, limit))
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.warning(f"查询云端英雄出场记录失败: {e}")
        return None


# ═══════════════════════════════════════════════════════════
# 版本更新公告（BLOB 方案：zip 直接存库，客户端转发下载）
# ═══════════════════════════════════════════════════════════

UPDATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS app_updates (
    id INT AUTO_INCREMENT PRIMARY KEY,
    version VARCHAR(20) NOT NULL,
    title VARCHAR(100) NOT NULL DEFAULT '',
    notes TEXT,
    zip_data LONGBLOB,
    zip_name VARCHAR(100),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    is_active TINYINT DEFAULT 1
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def get_cloud_champion_rank_map() -> Optional[Dict[int, Dict]]:
    """
    构建云端英雄名次映射（供选人阶段/英雄查询使用）。
    按场均伤害降序排名，rank = 伤害榜名次。
    返回: {champion_id: {rank, champion_name, champion_cn, games, avg_damage, win_rate}}
          云端不可用返回 None。
    """
    ranking = get_cloud_champion_ranking(limit=None)
    if ranking is None:
        return None
    result = {}
    for idx, r in enumerate(ranking):
        cid = r.get("champion_id")
        if cid is None:
            continue
        result[cid] = {
            "rank": idx + 1,
            "champion_name": r.get("champion_name", ""),
            "champion_cn": r.get("champion_cn", ""),
            "games": r.get("games", 0),
            "avg_damage": r.get("avg_damage", 0),
            "win_rate": r.get("win_rate", 0),
        }
    return result


def ensure_update_table() -> bool:
    """确保 app_updates 表存在（幂等，可重复调用）"""
    try:
        with get_cloud() as conn:
            if conn is None:
                return False
            with conn.cursor() as cur:
                cur.execute(UPDATE_TABLE_SQL)
        return True
    except Exception as e:
        logger.warning(f"创建 app_updates 表失败: {e}")
        return False


def check_latest_update() -> Optional[Dict]:
    """
    查询最新版本公告（不读 BLOB，只取元数据）。
    返回: {id, version, title, notes, created_at} 或 None（无公告/云端不可用）
    """
    try:
        with get_cloud() as conn:
            if conn is None:
                return None
            with conn.cursor(DictCursor) as cur:
                cur.execute("""
                    SELECT id, version, title, notes, created_at
                    FROM app_updates
                    WHERE is_active = 1
                    ORDER BY id DESC
                    LIMIT 1
                """)
                row = cur.fetchone()
                if not row:
                    return None
                row = dict(row)
                if row.get("created_at"):
                    row["created_at"] = row["created_at"].strftime("%Y-%m-%d")
                return row
    except Exception as e:
        logger.warning(f"查询最新版本公告失败: {e}")
        return None


def get_update_zip(update_id: int) -> Optional[Dict]:
    """
    读取指定公告的 zip BLOB。
    返回: {zip_data: bytes, zip_name: str} 或 None（不存在/云端不可用）
    """
    try:
        with get_cloud() as conn:
            if conn is None:
                return None
            with conn.cursor(DictCursor) as cur:
                cur.execute(
                    "SELECT zip_data, zip_name FROM app_updates WHERE id = %s",
                    (update_id,),
                )
                row = cur.fetchone()
                if not row or not row.get("zip_data"):
                    return None
                return {
                    "zip_data": row["zip_data"],
                    "zip_name": row.get("zip_name") or "小熊海克斯复盘-更新.zip",
                }
    except Exception as e:
        logger.warning(f"读取更新包失败: {e}")
        return None
