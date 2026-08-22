"""
SQLite 数据库模块
存储对局数据、参与者数据，提供增删查操作。
"""

import os
import sys
import json
import sqlite3
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

logger = logging.getLogger(__name__)

if getattr(sys, 'frozen', False):
    # PyInstaller 打包模式：数据库放到 exe 同级 data/ 目录
    BASE_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DB_DIR, "heimert_stats.db")


@contextmanager
def get_db():
    """获取数据库连接（上下文管理器，自动提交和关闭）"""
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_database():
    """初始化数据库表结构"""
    with get_db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS summoner (
                puuid          TEXT PRIMARY KEY,
                game_name      TEXT,
                tag_line       TEXT,
                display_name   TEXT,
                summoner_id    INTEGER,
                account_id     INTEGER,
                profile_icon   INTEGER,
                updated_at     INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            );

            CREATE TABLE IF NOT EXISTS games (
                game_id        INTEGER PRIMARY KEY,
                queue_id       INTEGER NOT NULL,
                game_mode      TEXT,
                game_type      TEXT,
                map_id         INTEGER,
                game_creation  INTEGER NOT NULL,
                game_duration  INTEGER NOT NULL DEFAULT 0,
                game_version   TEXT,
                platform_id    TEXT,
                is_remake      INTEGER NOT NULL DEFAULT 0,
                puuid          TEXT NOT NULL DEFAULT '',
                raw_json       TEXT,
                fetched_at     INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            );

            CREATE TABLE IF NOT EXISTS participants (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id           INTEGER NOT NULL REFERENCES games(game_id),
                puuid             TEXT,
                summoner_name     TEXT,
                champion_id       INTEGER NOT NULL,
                champion_name     TEXT,
                team_id           INTEGER NOT NULL DEFAULT 100,
                participant_id    INTEGER NOT NULL,
                win               INTEGER NOT NULL DEFAULT 0,
                -- 核心 KDA
                kills             INTEGER NOT NULL DEFAULT 0,
                deaths            INTEGER NOT NULL DEFAULT 0,
                assists           INTEGER NOT NULL DEFAULT 0,
                -- 伤害相关
                total_damage_dealt_to_champions INTEGER NOT NULL DEFAULT 0,
                total_damage_dealt             INTEGER NOT NULL DEFAULT 0,
                total_damage_taken             INTEGER NOT NULL DEFAULT 0,
                physical_damage_dealt_to_champions INTEGER NOT NULL DEFAULT 0,
                magic_damage_dealt_to_champions    INTEGER NOT NULL DEFAULT 0,
                true_damage_dealt_to_champions     INTEGER NOT NULL DEFAULT 0,
                -- 经济与发育
                gold_earned       INTEGER NOT NULL DEFAULT 0,
                total_minions_killed INTEGER NOT NULL DEFAULT 0,
                neutral_minions_killed INTEGER NOT NULL DEFAULT 0,
                -- 治疗与护盾
                total_heal        INTEGER NOT NULL DEFAULT 0,
                total_damage_shielded_to_others INTEGER NOT NULL DEFAULT 0,
                -- 视野
                wards_placed      INTEGER NOT NULL DEFAULT 0,
                wards_killed      INTEGER NOT NULL DEFAULT 0,
                vision_score      INTEGER NOT NULL DEFAULT 0,
                -- 连杀
                double_kills      INTEGER NOT NULL DEFAULT 0,
                triple_kills      INTEGER NOT NULL DEFAULT 0,
                quadra_kills      INTEGER NOT NULL DEFAULT 0,
                penta_kills       INTEGER NOT NULL DEFAULT 0,
                largest_killing_spree INTEGER NOT NULL DEFAULT 0,
                -- 其他
                time_ccing_others INTEGER NOT NULL DEFAULT 0,
                summoner1_id      INTEGER,
                summoner2_id      INTEGER,
                item0             INTEGER,
                item1             INTEGER,
                item2             INTEGER,
                item3             INTEGER,
                item4             INTEGER,
                item5             INTEGER,
                item6             INTEGER,
                -- 海克斯增幅
                player_augment1   INTEGER DEFAULT 0,
                player_augment2   INTEGER DEFAULT 0,
                player_augment3   INTEGER DEFAULT 0,
                player_augment4   INTEGER DEFAULT 0,
                -- 增伤符文
                perk0             INTEGER,
                perk0_var1        INTEGER,
                perk0_var2        INTEGER,
                perk0_var3        INTEGER,
                -- 队内伤害排名 (由 analyzer 计算)
                team_damage_rank  INTEGER DEFAULT NULL,
                team_damage_pct   REAL DEFAULT NULL,
                UNIQUE(game_id, participant_id)
            );

            CREATE INDEX IF NOT EXISTS idx_games_creation ON games(game_creation DESC);
            CREATE INDEX IF NOT EXISTS idx_games_queue ON games(queue_id);
            CREATE INDEX IF NOT EXISTS idx_games_puuid ON games(puuid);
            CREATE INDEX IF NOT EXISTS idx_participants_game ON participants(game_id);
            CREATE INDEX IF NOT EXISTS idx_participants_puuid ON participants(puuid);
            CREATE INDEX IF NOT EXISTS idx_participants_champion ON participants(champion_id);

            -- 复盘会话
            CREATE TABLE IF NOT EXISTS review_sessions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT,
                created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                start_date  TEXT NOT NULL,
                end_date    TEXT NOT NULL,
                game_ids    TEXT NOT NULL DEFAULT '[]'
            );

            -- 复盘队伍分配: session内每个玩家的队伍编号
            CREATE TABLE IF NOT EXISTS review_teams (
                session_id  INTEGER NOT NULL REFERENCES review_sessions(id),
                puuid       TEXT NOT NULL,
                game_name   TEXT,
                team_number INTEGER NOT NULL DEFAULT 0,  -- 0,1,2,... -1=路人
                PRIMARY KEY (session_id, puuid)
            );

            -- 复盘积分结果: session内每场的结算
            CREATE TABLE IF NOT EXISTS review_scores (
                session_id  INTEGER NOT NULL REFERENCES review_sessions(id),
                game_id     INTEGER NOT NULL,
                puuid       TEXT NOT NULL,
                score       INTEGER NOT NULL DEFAULT 0,
                team_number INTEGER NOT NULL DEFAULT 0,
                rule_type   TEXT,  -- '2v2v1', '2v2', '1v1v1'
                PRIMARY KEY (session_id, game_id, puuid)
            );

            -- 按局队伍分配: 每局独立分配
            CREATE TABLE IF NOT EXISTS review_team_assignments (
                session_id  INTEGER NOT NULL REFERENCES review_sessions(id),
                game_id     INTEGER NOT NULL,
                puuid       TEXT NOT NULL,
                team_number INTEGER NOT NULL DEFAULT -1,  -- 0,1,2,... -1=路人
                multiplier  REAL NOT NULL DEFAULT 1.0,
                PRIMARY KEY (session_id, game_id, puuid)
            );

            -- 复盘结果缓存: 保存完整的计算结果
            CREATE TABLE IF NOT EXISTS review_results (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  INTEGER NOT NULL REFERENCES review_sessions(id),
                result_json TEXT NOT NULL,
                created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            );
        """)
        # 兼容旧表: 如果缺少 multiplier 列则添加
        try:
            db.execute("ALTER TABLE review_team_assignments ADD COLUMN multiplier REAL NOT NULL DEFAULT 1.0")
        except Exception:
            pass  # 列已存在
        # 兼容旧表: 海克斯增幅列（2026-07 新增）
        for col in ("player_augment1", "player_augment2", "player_augment3", "player_augment4"):
            try:
                db.execute(f"ALTER TABLE participants ADD COLUMN {col} INTEGER DEFAULT 0")
            except Exception:
                pass  # 列已存在
    logger.info("数据库初始化完成")


def get_champion_name_map() -> dict:
    """
    获取英雄中文名映射（champion_id → 中文名）
    """
    from champion_cn import CHAMPION_NAME_CN
    return dict(CHAMPION_NAME_CN)


def get_augment_display_name(augment_id: int) -> str:
    """
    获取增幅的显示名称，0 或未知返回空字符串。
    """
    from augment_cn import get_augment_display
    return get_augment_display(augment_id)


def insert_summoner(summoner: dict):
    """插入或更新召唤师信息"""
    with get_db() as db:
        db.execute("""
            INSERT OR REPLACE INTO summoner
                (puuid, game_name, tag_line, display_name, summoner_id, account_id, profile_icon, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, strftime('%s','now'))
        """, (
            summoner.get("puuid"),
            summoner.get("gameName") or summoner.get("displayName"),
            summoner.get("tagLine"),
            summoner.get("displayName"),
            summoner.get("summonerId"),
            summoner.get("accountId"),
            summoner.get("profileIconId"),
        ))


def get_summoner() -> Optional[dict]:
    """获取已存储的召唤师信息"""
    with get_db() as db:
        row = db.execute("SELECT * FROM summoner ORDER BY updated_at DESC LIMIT 1").fetchone()
        return dict(row) if row else None


def game_exists(game_id: int) -> bool:
    """检查对局是否已存在"""
    with get_db() as db:
        row = db.execute("SELECT 1 FROM games WHERE game_id = ?", (game_id,)).fetchone()
        return row is not None


def insert_game(game_data: dict, puuid: str) -> bool:
    """
    插入对局及其参与者数据。
    返回 True 表示新插入，False 表示已存在。
    """
    game_id = game_data.get("gameId")
    if not game_id or game_exists(game_id):
        return False

    # 检测是否为重开局（极短对局）
    game_duration = game_data.get("gameDuration", 0)
    is_remake = 1 if (game_duration < 300) else 0

    with get_db() as db:
        # 插入 games
        db.execute("""
            INSERT OR IGNORE INTO games
                (game_id, queue_id, game_mode, game_type, map_id, game_creation,
                 game_duration, game_version, platform_id, is_remake, puuid, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            game_id,
            game_data.get("queueId", 0),
            game_data.get("gameMode"),
            game_data.get("gameType"),
            game_data.get("mapId"),
            game_data.get("gameCreation", 0),
            game_duration,
            game_data.get("gameVersion"),
            game_data.get("platformId"),
            is_remake,
            puuid,
            json.dumps(game_data, ensure_ascii=False),
        ))

        # 插入参与者
        participants = game_data.get("participants", [])
        participant_identities = game_data.get("participantIdentities", [])

        # 构建 identity 映射
        identity_map = {}
        for pi in participant_identities:
            pid = pi.get("participantId")
            identity_map[pid] = pi.get("player", {})

        for p in participants:
            stats = p.get("stats", p)  # stats 可能是内嵌的
            pid = p.get("participantId", 0)
            player_info = identity_map.get(pid, {})

            db.execute("""
                INSERT OR IGNORE INTO participants
                    (game_id, puuid, summoner_name, champion_id, champion_name,
                     team_id, participant_id, win,
                     kills, deaths, assists,
                     total_damage_dealt_to_champions, total_damage_dealt,
                     total_damage_taken,
                     physical_damage_dealt_to_champions,
                     magic_damage_dealt_to_champions,
                     true_damage_dealt_to_champions,
                     gold_earned, total_minions_killed, neutral_minions_killed,
                     total_heal, total_damage_shielded_to_others,
                     wards_placed, wards_killed, vision_score,
                     double_kills, triple_kills, quadra_kills, penta_kills,
                     largest_killing_spree, time_ccing_others,
                     summoner1_id, summoner2_id,
                     item0, item1, item2, item3, item4, item5, item6,
                     player_augment1, player_augment2, player_augment3, player_augment4)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                game_id,
                p.get("puuid") or player_info.get("puuid"),
                player_info.get("gameName") or player_info.get("summonerName"),
                stats.get("championId") or p.get("championId", 0),
                stats.get("championName") or p.get("championName", ""),
                p.get("teamId") or stats.get("teamId", 100),
                pid,
                1 if stats.get("win") else 0,
                stats.get("kills", 0),
                stats.get("deaths", 0),
                stats.get("assists", 0),
                stats.get("totalDamageDealtToChampions", 0),
                stats.get("totalDamageDealt", 0),
                stats.get("totalDamageTaken", 0),
                stats.get("physicalDamageDealtToChampions", 0),
                stats.get("magicDamageDealtToChampions", 0),
                stats.get("trueDamageDealtToChampions", 0),
                stats.get("goldEarned", 0),
                stats.get("totalMinionsKilled", 0),
                stats.get("neutralMinionsKilled", 0),
                stats.get("totalHeal", 0),
                stats.get("totalDamageShieldedToOthers", 0),
                stats.get("wardsPlaced", 0),
                stats.get("wardsKilled", 0),
                stats.get("visionScore", 0),
                stats.get("doubleKills", 0),
                stats.get("tripleKills", 0),
                stats.get("quadraKills", 0),
                stats.get("pentaKills", 0),
                stats.get("largestKillingSpree", 0),
                stats.get("timeCCingOthers", 0),
                stats.get("summoner1Id"),
                stats.get("summoner2Id"),
                stats.get("item0"),
                stats.get("item1"),
                stats.get("item2"),
                stats.get("item3"),
                stats.get("item4"),
                stats.get("item5"),
                stats.get("item6"),
                p.get("playerAugment1") or stats.get("playerAugment1", 0),
                p.get("playerAugment2") or stats.get("playerAugment2", 0),
                p.get("playerAugment3") or stats.get("playerAugment3", 0),
                p.get("playerAugment4") or stats.get("playerAugment4", 0),
            ))

    return True


def get_recent_games(limit: int = 50, offset: int = 0, queue_id: int = None) -> List[Dict]:
    """获取最近的对局列表"""
    with get_db() as db:
        where = ["1=1"]
        params = []
        if queue_id:
            where.append("g.queue_id = ?")
            params.append(queue_id)

        # 先取总条数
        count_row = db.execute(
            f"SELECT COUNT(*) as cnt FROM games g WHERE {' AND '.join(where)}",
            params
        ).fetchone()
        total = count_row["cnt"] if count_row else 0

        # 取对局 + 自己的参与者数据
        where.append("g.puuid = p.puuid")
        rows = db.execute(f"""
            SELECT g.*, p.*,
                   p.total_damage_dealt_to_champions as my_damage,
                   p.kills as my_kills, p.deaths as my_deaths,
                   p.assists as my_assists, p.champion_id as my_champion_id,
                   p.champion_name as my_champion_name,
                   p.team_damage_rank, p.team_damage_pct,
                   p.win as my_win
            FROM games g
            JOIN participants p ON g.game_id = p.game_id
            WHERE {' AND '.join(where)}
            ORDER BY g.game_creation DESC
            LIMIT ? OFFSET ?
        """, [*params, limit, offset]).fetchall()

        games = []
        for row in rows:
            game = dict(row)
            game["game_date"] = datetime.fromtimestamp(game["game_creation"] / 1000).strftime("%Y-%m-%d %H:%M")
            game["duration_min"] = game["game_duration"] // 60
            game["duration_sec"] = game["game_duration"] % 60
            game["kda"] = (
                (game["my_kills"] + game["my_assists"]) / max(game["my_deaths"], 1)
            )
            games.append(game)

        return {"games": games, "total": total}


def get_game_with_participants(game_id: int) -> Optional[Dict]:
    """获取对局详情及所有参与者"""
    with get_db() as db:
        game = db.execute("SELECT * FROM games WHERE game_id = ?", (game_id,)).fetchone()
        if not game:
            return None

        participants = db.execute(
            "SELECT * FROM participants WHERE game_id = ? ORDER BY team_id, team_damage_rank",
            (game_id,)
        ).fetchall()

        return {
            "game": dict(game),
            "participants": [dict(p) for p in participants],
        }


def get_all_games() -> List[Dict]:
    """获取所有对局列表"""
    with get_db() as db:
        rows = db.execute("SELECT * FROM games ORDER BY game_creation DESC").fetchall()
        return [dict(r) for r in rows]


def get_participants_for_game(game_id: int) -> List[Dict]:
    """获取对局所有参与者数据"""
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM participants WHERE game_id = ? ORDER BY team_id, participant_id",
            (game_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_games_count() -> int:
    """获取对局总数"""
    with get_db() as db:
        row = db.execute("SELECT COUNT(*) as cnt FROM games").fetchone()
        return row["cnt"] if row else 0


def get_all_game_ids() -> set:
    """获取所有已存在的 game_id 集合（用于增量拉取去重）"""
    with get_db() as db:
        rows = db.execute("SELECT game_id FROM games").fetchall()
        return {r["game_id"] for r in rows}


def get_damage_stats(limit: int = 20) -> Dict:
    """获取伤害统计数据"""
    with get_db() as db:
        # 最高单场伤害
        top_damage = db.execute("""
            SELECT g.game_id, p.champion_name, p.champion_id, p.total_damage_dealt_to_champions as damage,
                   p.kills, p.deaths, p.assists, p.team_damage_rank,
                   g.game_creation
            FROM participants p
            JOIN games g ON p.game_id = g.game_id
            WHERE p.puuid = g.puuid
            ORDER BY p.total_damage_dealt_to_champions DESC
            LIMIT ?
        """, (limit,)).fetchall()

        # 平均伤害统计
        avg_stats = db.execute("""
            SELECT
                COUNT(*) as game_count,
                ROUND(AVG(p.total_damage_dealt_to_champions)) as avg_damage,
                ROUND(AVG(p.kills), 1) as avg_kills,
                ROUND(AVG(p.deaths), 1) as avg_deaths,
                ROUND(AVG(p.assists), 1) as avg_assists,
                ROUND(AVG(p.team_damage_pct * 100), 1) as avg_damage_pct,
                ROUND(AVG(p.team_damage_rank), 2) as avg_rank,
                SUM(p.win) as wins,
                COUNT(*) as total
            FROM participants p
            JOIN games g ON p.game_id = g.game_id
            WHERE p.puuid = g.puuid AND g.is_remake = 0
        """).fetchone()

        # 伤害排名分布
        rank_dist = db.execute("""
            SELECT team_damage_rank, COUNT(*) as cnt
            FROM participants p
            JOIN games g ON p.game_id = g.game_id
            WHERE p.puuid = g.puuid AND g.is_remake = 0 AND team_damage_rank IS NOT NULL
            GROUP BY team_damage_rank
            ORDER BY team_damage_rank
        """).fetchall()

        # 各英雄平均伤害
        champ_stats = db.execute("""
            SELECT p.champion_name, p.champion_id,
                   COUNT(*) as games,
                   ROUND(AVG(p.total_damage_dealt_to_champions)) as avg_damage,
                   ROUND(AVG(p.team_damage_pct * 100), 1) as avg_damage_pct,
                   ROUND(AVG(p.team_damage_rank), 2) as avg_rank,
                   ROUND(AVG(p.kills), 1) as avg_kills,
                   ROUND(AVG(p.deaths), 1) as avg_deaths,
                   ROUND(AVG(p.assists), 1) as avg_assists,
                   SUM(p.win) as wins
            FROM participants p
            JOIN games g ON p.game_id = g.game_id
            WHERE p.puuid = g.puuid AND g.is_remake = 0
            GROUP BY p.champion_id
            ORDER BY games DESC
        """).fetchall()

        return {
            "top_damage": [dict(r) for r in top_damage],
            "avg_stats": dict(avg_stats) if avg_stats else {},
            "rank_distribution": [dict(r) for r in rank_dist],
            "champion_stats": [dict(r) for r in champ_stats],
        }


def get_player_damage_ranking(limit: int = 50, start_date: str = None, end_date: str = None,
                              puuids: Optional[list] = None) -> List[Dict]:
    """获取所有玩家的平均伤害排名（按人聚合，不限当前用户）
    start_date / end_date: "%Y-%m-%d" 格式，过滤 game_creation 时间范围。
    puuids: 只统计这些 puuid 的玩家（好友筛选）；None 表示全部。"""
    with get_db() as db:
        where_extra = "g.is_remake = 0 AND p.team_damage_rank IS NOT NULL"
        params = []
        if start_date:
            import time, calendar
            start_dt = time.strptime(start_date + " 00:00:00", "%Y-%m-%d %H:%M:%S")
            start_ms = int(calendar.timegm(start_dt)) * 1000
            where_extra += " AND g.game_creation >= ?"
            params.append(start_ms)
        if end_date:
            import time, calendar
            end_dt = time.strptime(end_date + " 23:59:59", "%Y-%m-%d %H:%M:%S")
            end_ms = int(calendar.timegm(end_dt)) * 1000
            where_extra += " AND g.game_creation <= ?"
            params.append(end_ms)
        # 好友筛选（SQL 层过滤，保证排名序号正确）
        if puuids:
            placeholders = ", ".join(["?"] * len(puuids))
            where_extra += f" AND p.puuid IN ({placeholders})"
            params.extend(puuids)

        params.append(limit)
        rows = db.execute(f"""
            SELECT p.puuid, p.summoner_name,
                   COUNT(*) as games,
                   ROUND(AVG(p.total_damage_dealt_to_champions)) as avg_damage,
                   ROUND(AVG(p.team_damage_pct * 100), 1) as avg_damage_pct,
                   ROUND(AVG(p.team_damage_rank), 2) as avg_rank,
                   ROUND(AVG(p.kills), 1) as avg_kills,
                   ROUND(AVG(p.deaths), 1) as avg_deaths,
                   ROUND(AVG(p.assists), 1) as avg_assists,
                   SUM(p.win) as wins
            FROM participants p
            JOIN games g ON p.game_id = g.game_id
            WHERE {where_extra}
            GROUP BY p.puuid
            HAVING games >= 3
            ORDER BY avg_damage DESC
            LIMIT ?
        """, params).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["win_rate"] = round(d["wins"] / d["games"] * 100, 1) if d["games"] else 0
            d["avg_kda"] = round(
                (d["avg_kills"] + d["avg_assists"]) / max(d["avg_deaths"], 1), 2
            )
            results.append(d)
        return results


def get_champion_stats_all_players() -> List[Dict]:
    """获取所有玩家的英雄平均伤害排名（不限当前用户）"""
    with get_db() as db:
        rows = db.execute("""
            SELECT p.champion_name, p.champion_id,
                   COUNT(*) as games,
                   ROUND(AVG(p.total_damage_dealt_to_champions)) as avg_damage,
                   ROUND(AVG(p.team_damage_pct * 100), 1) as avg_damage_pct,
                   ROUND(AVG(p.team_damage_rank), 2) as avg_rank,
                   ROUND(AVG(p.kills), 1) as avg_kills,
                   ROUND(AVG(p.deaths), 1) as avg_deaths,
                   ROUND(AVG(p.assists), 1) as avg_assists,
                   SUM(p.win) as wins
            FROM participants p
            JOIN games g ON p.game_id = g.game_id
            WHERE g.is_remake = 0
              AND p.team_damage_rank IS NOT NULL
            GROUP BY p.champion_id
            ORDER BY avg_damage DESC
        """).fetchall()
        return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════
# 复盘系统
# ═══════════════════════════════════════════════════════════════

def create_review_session(name: str, start_date: str, end_date: str, game_ids: list) -> int:
    """创建复盘会话，返回 session_id"""
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO review_sessions (name, start_date, end_date, game_ids) VALUES (?, ?, ?, ?)",
            (name, start_date, end_date, json.dumps(game_ids))
        )
        return cur.lastrowid


def get_review_sessions(limit: int = 20) -> list:
    """获取最近的复盘会话"""
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM review_sessions ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_review_session(session_id: int) -> dict:
    """获取单个复盘会话"""
    with get_db() as db:
        row = db.execute("SELECT * FROM review_sessions WHERE id = ?", (session_id,)).fetchone()
        if row:
            d = dict(row)
            d["game_ids"] = json.loads(d.get("game_ids", "[]"))
            return d
        return None


def save_review_teams(session_id: int, teams: dict):
    """
    保存复盘队伍分配
    teams: {puuid: {"team_number": int, "game_name": str}} 或 {puuid: int}
    """
    with get_db() as db:
        db.execute("DELETE FROM review_teams WHERE session_id = ?", (session_id,))
        for puuid, info in teams.items():
            if isinstance(info, dict):
                team_num = info.get("team_number", -1)
                game_name = info.get("game_name", "")
            else:
                team_num = int(info)
                game_name = ""
            db.execute(
                "INSERT OR REPLACE INTO review_teams (session_id, puuid, game_name, team_number) VALUES (?, ?, ?, ?)",
                (session_id, puuid, game_name, team_num)
            )


def get_review_teams(session_id: int) -> dict:
    """获取复盘队伍分配"""
    with get_db() as db:
        rows = db.execute("SELECT * FROM review_teams WHERE session_id = ?", (session_id,)).fetchall()
        return {r["puuid"]: {"game_name": r["game_name"], "team_number": r["team_number"]} for r in rows}


# ═══ 按局队伍分配（新方式）═══

def save_game_teams(session_id: int, game_id: int, teams: dict):
    """
    保存一局游戏的队伍分配
    teams: {puuid: team_number} 或 {puuid: {"team": N, "multiplier": 1.1}}
    team_number: 0,1,2,... -1=路人
    """
    with get_db() as db:
        db.execute(
            "DELETE FROM review_team_assignments WHERE session_id = ? AND game_id = ?",
            (session_id, game_id)
        )
        for puuid, info in teams.items():
            if isinstance(info, dict):
                team_num = int(info.get("team", info.get("team_number", -1)))
                mult = float(info.get("multiplier", 1.0))
            else:
                team_num = int(info)
                mult = 1.0
            db.execute(
                "INSERT INTO review_team_assignments (session_id, game_id, puuid, team_number, multiplier) VALUES (?, ?, ?, ?, ?)",
                (session_id, game_id, puuid, team_num, mult)
            )


def get_game_teams(session_id: int, game_id: int) -> dict:
    """获取一局游戏的队伍分配，返回 {puuid: team_number}"""
    with get_db() as db:
        rows = db.execute(
            "SELECT puuid, team_number FROM review_team_assignments WHERE session_id = ? AND game_id = ?",
            (session_id, game_id)
        ).fetchall()
        return {r["puuid"]: r["team_number"] for r in rows}


def get_all_game_teams(session_id: int) -> dict:
    """获取 session 所有局的队伍分配，返回 {game_id: {puuid: team_number}}"""
    with get_db() as db:
        rows = db.execute(
            "SELECT game_id, puuid, team_number FROM review_team_assignments WHERE session_id = ?",
            (session_id,)
        ).fetchall()
        result = {}
        for r in rows:
            gid = r["game_id"]
            if gid not in result:
                result[gid] = {}
            result[gid][r["puuid"]] = r["team_number"]
        return result


def get_all_game_multipliers(session_id: int) -> dict:
    """获取所有局的倍率，返回 {game_id: {puuid: multiplier}}"""
    with get_db() as db:
        rows = db.execute(
            "SELECT game_id, puuid, multiplier FROM review_team_assignments WHERE session_id = ?",
            (session_id,)
        ).fetchall()
        result = {}
        for r in rows:
            gid = r["game_id"]
            if gid not in result:
                result[gid] = {}
            result[gid][r["puuid"]] = r["multiplier"]
        return result


def save_review_scores(session_id: int, scores: list):
    """
    保存复盘积分结果
    scores: [{game_id, puuid, score, team_number, rule_type}]
    """
    with get_db() as db:
        db.execute("DELETE FROM review_scores WHERE session_id = ?", (session_id,))
        for s in scores:
            db.execute(
                "INSERT INTO review_scores (session_id, game_id, puuid, score, team_number, rule_type) VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, s["game_id"], s["puuid"], s["score"], s["team_number"], s.get("rule_type", ""))
            )


def get_review_scores(session_id: int) -> list:
    """获取复盘积分结果"""
    with get_db() as db:
        rows = db.execute("SELECT * FROM review_scores WHERE session_id = ?", (session_id,)).fetchall()
        return [dict(r) for r in rows]


def get_games_by_date_range(start_date: str, end_date: str, limit: int = 200) -> list:
    """按日期范围获取对局"""
    import time
    start_ts = int(time.mktime(time.strptime(start_date + " 00:00:00", "%Y-%m-%d %H:%M:%S"))) * 1000
    end_ts = int(time.mktime(time.strptime(end_date + " 23:59:59", "%Y-%m-%d %H:%M:%S"))) * 1000

    with get_db() as db:
        rows = db.execute("""
            SELECT g.*, p.champion_id, p.champion_name, p.kills, p.deaths, p.assists,
                   p.total_damage_dealt_to_champions, p.team_damage_rank, p.team_damage_pct
            FROM games g
            JOIN participants p ON g.game_id = p.game_id AND p.puuid = g.puuid
            WHERE g.game_creation >= ? AND g.game_creation <= ?
            ORDER BY g.game_creation DESC
            LIMIT ?
        """, (start_ts, end_ts, limit)).fetchall()
        return [dict(r) for r in rows]


def get_game_players(game_ids: list) -> list:
    """获取指定对局中的所有玩家"""
    if not game_ids:
        return []
    placeholders = ",".join("?" * len(game_ids))
    with get_db() as db:
        # 获取所有参与者（去除路人标记，展示所有）
        rows = db.execute(f"""
            SELECT p.puuid, p.summoner_name, p.champion_name, p.champion_id,
                   COUNT(*) as game_count, g.game_creation
            FROM participants p
            JOIN games g ON g.game_id = p.game_id
            WHERE p.game_id IN ({placeholders})
            GROUP BY p.puuid
            ORDER BY game_count DESC
        """, game_ids).fetchall()
        return [dict(r) for r in rows]


def get_players_in_games(game_ids: list, puuid: str = None) -> list:
    """获取指定对局中每位玩家的详细数据"""
    if not game_ids:
        return []
    placeholders = ",".join("?" * len(game_ids))
    params = game_ids
    if puuid:
        params = [puuid] + game_ids
        puuid_where = "AND p.puuid = ?"
    else:
        puuid_where = ""
    with get_db() as db:
        rows = db.execute(f"""
            SELECT p.*, g.game_creation, g.game_duration, g.game_mode, g.queue_id, g.puuid as my_puuid
            FROM participants p
            JOIN games g ON g.game_id = p.game_id
            WHERE p.game_id IN ({placeholders}) {puuid_where}
            ORDER BY g.game_creation DESC
        """, params).fetchall()
        return [dict(r) for r in rows]


# ── 复盘结果缓存 ──────────────────────────────────────────

def save_review_result(session_id: int, result_json: str) -> int:
    """保存复盘计算结果"""
    with get_db() as db:
        # 删除旧结果（同一 session 重新计算时覆盖）
        db.execute("DELETE FROM review_results WHERE session_id = ?", (session_id,))
        db.execute("""
            INSERT INTO review_results (session_id, result_json)
            VALUES (?, ?)
        """, (session_id, result_json))
        return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def get_review_result(session_id: int) -> Optional[dict]:
    """获取某次复盘的缓存结果"""
    with get_db() as db:
        row = db.execute("""
            SELECT r.*, s.name, s.start_date, s.end_date
            FROM review_results r
            JOIN review_sessions s ON s.id = r.session_id
            WHERE r.session_id = ?
            ORDER BY r.created_at DESC
            LIMIT 1
        """, (session_id,)).fetchone()
        if row:
            d = dict(row)
            d["result"] = json.loads(d["result_json"])
            return d
        return None


def get_review_results_list(limit: int = 20) -> list:
    """获取所有已保存的复盘结果列表（含 session 信息）"""
    with get_db() as db:
        rows = db.execute(f"""
            SELECT r.id, r.session_id, r.created_at,
                   s.name, s.start_date, s.end_date, s.game_ids
            FROM review_results r
            JOIN review_sessions s ON s.id = r.session_id
            ORDER BY r.created_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["game_count"] = len(json.loads(d["game_ids"])) if d["game_ids"] else 0
            result.append(d)
        return result


def delete_review_session(session_id: int):
    """删除复盘会话及其所有关联数据"""
    with get_db() as db:
        db.execute("DELETE FROM review_results WHERE session_id = ?", (session_id,))
        db.execute("DELETE FROM review_scores WHERE session_id = ?", (session_id,))
        db.execute("DELETE FROM review_team_assignments WHERE session_id = ?", (session_id,))
        db.execute("DELETE FROM review_teams WHERE session_id = ?", (session_id,))
        db.execute("DELETE FROM review_sessions WHERE id = ?", (session_id,))


def get_review_sessions_by_ids(session_ids: list) -> list:
    """批量获取复盘会话"""
    if not session_ids:
        return []
    placeholders = ",".join("?" * len(session_ids))
    with get_db() as db:
        rows = db.execute(
            f"SELECT * FROM review_sessions WHERE id IN ({placeholders}) ORDER BY created_at DESC",
            session_ids
        ).fetchall()
        return [dict(r) for r in rows]


def get_champion_list() -> List[Dict]:
    """获取所有出现过的英雄列表（含次数、平均伤害等）"""
    with get_db() as db:
        rows = db.execute("""
            SELECT p.champion_id, p.champion_name,
                   COUNT(*) as games,
                   COUNT(DISTINCT p.puuid) as players,
                   ROUND(AVG(p.total_damage_dealt_to_champions)) as avg_damage,
                   ROUND(AVG(p.team_damage_rank), 2) as avg_rank,
                   SUM(p.win) as wins,
                   ROUND(AVG(p.kills), 1) as avg_kills,
                   ROUND(AVG(p.deaths), 1) as avg_deaths
            FROM participants p
            JOIN games g ON p.game_id = g.game_id
            WHERE g.is_remake = 0
              AND p.team_damage_rank IS NOT NULL
            GROUP BY p.champion_id
            ORDER BY games DESC, avg_damage DESC
        """).fetchall()
        return [dict(r) for r in rows]


def get_games_by_champion(champion_id: int, limit: int = 100) -> List[Dict]:
    """获取某个英雄的所有出场记录（含玩家、伤害、装备、游戏信息）"""
    with get_db() as db:
        rows = db.execute("""
            SELECT p.*, g.game_creation, g.game_duration, g.queue_id
            FROM participants p
            JOIN games g ON g.game_id = p.game_id
            WHERE p.champion_id = ?
              AND g.is_remake = 0
              AND p.team_damage_rank IS NOT NULL
            ORDER BY g.game_creation DESC
            LIMIT ?
        """, (champion_id, limit)).fetchall()
        return [dict(r) for r in rows]


def get_games_without_augments(limit: int = 50) -> List[Dict]:
    """获取尚未存储增幅数据的对局列表（用于补拉）"""
    with get_db() as db:
        rows = db.execute("""
            SELECT DISTINCT g.game_id, g.game_creation
            FROM games g
            JOIN participants p ON p.game_id = g.game_id
            WHERE g.is_remake = 0
              AND (p.player_augment1 IS NULL OR p.player_augment1 = 0)
            ORDER BY g.game_creation DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]


def update_game_augments(game_id: int, participants_augments: dict) -> int:
    """
    更新一场对局中所有参与者的增幅数据。
    participants_augments: { participantId: [aug1, aug2, aug3, aug4] }
    返回更新的行数。
    """
    with get_db() as db:
        updated = 0
        for pid, augs in participants_augments.items():
            db.execute("""
                UPDATE participants SET
                    player_augment1 = ?,
                    player_augment2 = ?,
                    player_augment3 = ?,
                    player_augment4 = ?
                WHERE game_id = ? AND participant_id = ?
            """, (augs[0], augs[1], augs[2], augs[3], game_id, pid))
            updated += db.rowcount if hasattr(db, 'rowcount') else 1
        return updated


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_database()
    print(f"数据库路径: {DB_PATH}")
    count = get_all_games_count()
    print(f"已存储 {count} 场对局")
