"""
复盘积分引擎
根据队伍分配和每局伤害数据，计算伤害竞猜积分。

规则：
- 五人局(2v2v1): 两队各两人 + 一人单走。
  同队伤害相加，单人伤害×2。
  第三名队伍每人输3分给第一名队伍每人。
- 四人局(2v2): 两队各两人。
  第二名队伍每人输3分给第一名队伍每人。
- 三人局(1v1v1): 三人各算各的。
  第三名输3分给第一名，第二名不变。
- 路人(team_number=-1)不参与积分。
"""

import logging
from typing import List, Dict, Tuple, Optional
from database import get_db

logger = logging.getLogger(__name__)

POINTS = 3  # 每次输赢的积分


def detect_rule(team_map: Dict[str, int]) -> Optional[str]:
    """
    根据队伍分配检测规则。
    team_map: {puuid: team_number}
    返回 '2v2v1', '2v2', '1v1v1' 或 None
    """
    # 过滤路人
    players = {p: t for p, t in team_map.items() if t >= 0}
    if not players:
        return None

    team_counts = {}
    for t in players.values():
        team_counts[t] = team_counts.get(t, 0) + 1

    num_teams = len(team_counts)
    total_players = len(players)

    if num_teams == 3 and total_players == 5:
        # 检查是否 2+2+1 分布
        sizes = sorted(team_counts.values())
        if sizes == [1, 2, 2]:
            return "2v2v1"
    elif num_teams == 2 and total_players == 4:
        sizes = sorted(team_counts.values())
        if sizes == [2, 2]:
            return "2v2"
    elif num_teams == 3 and total_players == 3:
        sizes = sorted(team_counts.values())
        if sizes == [1, 1, 1]:
            return "1v1v1"

    return None


def calculate_team_damage(participants_data: list, team_map: Dict[str, int]) -> Dict[int, float]:
    """
    计算每队的总伤害（单人队伤害翻倍，与双人队公平对比）。
    返回 {team_number: total_damage}
    """
    from collections import defaultdict

    team_damage = defaultdict(float)
    team_counts = defaultdict(int)

    for p in participants_data:
        puuid = p.get("puuid")
        if puuid not in team_map:
            continue
        team = team_map[puuid]
        if team < 0:  # 路人
            continue

        dmg = p.get("total_damage_dealt_to_champions", 0) or 0
        team_damage[team] += dmg
        team_counts[team] += 1

    # 单人队伤害翻倍（1人 vs 双人队2人，公平对比）
    for team, count in team_counts.items():
        if count == 1:
            team_damage[team] *= 2

    return dict(team_damage)


def calculate_game_scores(
    game_id: int,
    team_map: Dict[str, int],
    participants_data: list,
    multipliers: Dict[str, float] = None
) -> Tuple[Optional[str], Dict[str, int]]:
    """
    计算一局游戏的积分。

    返回: (rule_type, {puuid: score})
    - rule_type: '2v2v1', '2v2', '1v1v1' 或 None
    - score: 每名玩家的积分变动（已乘以倍率并四舍五入）
    """
    rule = detect_rule(team_map)
    if not rule:
        # 拿出哪些玩家在 team_map 中的，但是路人跳过
        active = {p: t for p, t in team_map.items() if t >= 0}
        if len(active) < 3:
            logger.debug(f"游戏 {game_id}: 活跃玩家不足3人 ({len(active)})，跳过")
        else:
            logger.debug(f"游戏 {game_id}: 无法识别规则 (teams={len(set(active.values()))}, players={len(active)})")
        return None, {}

    # 计算队伍总伤害
    team_damage = calculate_team_damage(participants_data, team_map)
    if not team_damage:
        return None, {}

    # 按伤害排序
    ranked_teams = sorted(team_damage.items(), key=lambda x: x[1], reverse=True)
    num_active_teams = len(ranked_teams)

    # 验证活跃队伍数符合规则预期，否则跳过本局
    expected_teams = {"2v2v1": 3, "2v2": 2, "1v1v1": 3}
    if num_active_teams < expected_teams.get(rule, 99):
        logger.debug(f"游戏 {game_id}: 规则={rule} 但仅有 {num_active_teams} 队参赛，跳过")
        return None, {}

    # 初始化分数
    scores = {}

    if rule == "2v2v1":
        # 3 teams: 1st vs 3rd
        # 单人队 ±6（替不存在的队友承担），双人队每人 ±3
        first_team = ranked_teams[0][0]
        third_team = ranked_teams[num_active_teams - 1][0]

        # 统计每队人数
        present_puuids = {p["puuid"] for p in participants_data}
        team_counts = {}
        for puuid, t in team_map.items():
            if t >= 0 and puuid in present_puuids:
                team_counts[t] = team_counts.get(t, 0) + 1

        for puuid, team in team_map.items():
            if team < 0:
                continue
            if puuid not in present_puuids:
                continue
            if team == first_team:
                if team_counts.get(team, 2) == 1:
                    scores[puuid] = POINTS * 2  # 单人队 +6
                else:
                    scores[puuid] = POINTS     # 双人队 +3
            elif team == third_team:
                if team_counts.get(team, 2) == 1:
                    scores[puuid] = -POINTS * 2  # 单人队 -6
                else:
                    scores[puuid] = -POINTS      # 双人队 -3
            else:
                scores[puuid] = 0

    elif rule == "2v2":
        # 2 teams: 1st +3, 2nd -3
        first_team = ranked_teams[0][0]
        second_team = ranked_teams[1][0]

        present_puuids = {p["puuid"] for p in participants_data}
        for puuid, team in team_map.items():
            if team < 0:
                continue
            if puuid not in present_puuids:
                continue
            if team == first_team:
                scores[puuid] = POINTS
            elif team == second_team:
                scores[puuid] = -POINTS

    elif rule == "1v1v1":
        # 3 solo: 1st +3, 3rd -3, 2nd unchanged
        first_team = ranked_teams[0][0]
        third_team = ranked_teams[num_active_teams - 1][0]

        present_puuids = {p["puuid"] for p in participants_data}
        for puuid, team in team_map.items():
            if team < 0:
                continue
            if puuid not in present_puuids:
                continue
            if team == first_team:
                scores[puuid] = POINTS
            elif team == third_team:
                scores[puuid] = -POINTS
            else:
                scores[puuid] = 0

    # 应用玩家倍率（倍率 > 0 才生效）
    if multipliers:
        for puuid in list(scores.keys()):
            mult = multipliers.get(puuid, 1.0)
            if mult > 0 and mult != 1.0:
                scores[puuid] = round(scores[puuid] * mult)

    return rule, scores


def calculate_session(session_id: int) -> dict:
    """
    计算整个复盘会话的积分（按局独立分配队伍）。
    返回汇总结果。
    """
    from database import get_review_session, get_all_game_teams, get_all_game_multipliers, get_players_in_games
    from collections import defaultdict
    from item_cn import get_item_build

    session = get_review_session(session_id)
    if not session:
        return {"error": "会话不存在"}

    game_ids = session["game_ids"]
    # 获取按局队伍分配: {game_id: {puuid: team_number}}
    all_game_teams = get_all_game_teams(session_id)
    # 获取按局倍率: {game_id: {puuid: multiplier}}
    all_multipliers = get_all_game_multipliers(session_id)

    # 获取所有对局的参与者数据
    all_participants = get_players_in_games(game_ids)

    # 按 game_id 分组
    game_participants = defaultdict(list)
    for p in all_participants:
        game_participants[p["game_id"]].append(p)

    # 逐局计算
    all_scores = []
    player_totals = defaultdict(int)
    player_games = defaultdict(int)
    player_teams = {}  # {puuid: latest_team_number}
    player_name_cache = {}
    player_damage_total = defaultdict(int)  # 累积伤害
    player_damage_games = defaultdict(int)  # 参与局数
    player_rank_total = defaultdict(int)    # 每局伤害排名累加（1=最高）
    player_rank_games = defaultdict(int)    # 有排名数据的局数
    player_tm_rank_total = defaultdict(float)  # 每局队友平均排名累加
    player_tm_rank_games = defaultdict(int)    # 有队友的局数
    game_results = []

    for gid in game_ids:
        parts = game_participants.get(gid, [])
        if not parts:
            continue

        team_map = all_game_teams.get(gid, {})
        if not team_map:
            logger.debug(f"游戏 {gid}: 未分配队伍，跳过")
            continue

        multipliers = all_multipliers.get(gid, {})
        rule, scores = calculate_game_scores(gid, team_map, parts, multipliers)
        if rule is None:
            continue

        # 收集玩家名（第一局出现的名字为准）
        for p in parts:
            puuid = p["puuid"]
            if puuid not in player_name_cache:
                player_name_cache[puuid] = p.get("summoner_name", puuid[:8])
            team_num = team_map.get(puuid, -1)
            if team_num >= 0:
                player_damage_total[puuid] += p.get("total_damage_dealt_to_champions", 0)
                player_damage_games[puuid] += 1

        # 构建本局的参与者详情（含出装）
        participant_details = []
        max_dmg = 0
        max_dmg_player = None
        for p in parts:
            puuid = p["puuid"]
            team_num = team_map.get(puuid, -1)
            dmg = p.get("total_damage_dealt_to_champions", 0)
            items = [p.get(f"item{i}", 0) for i in range(7)]
            detail = {
                "puuid": puuid,
                "summoner_name": p.get("summoner_name", puuid[:8]),
                "champion_name": p.get("champion_cn") or p.get("champion_name", "?"),
                "champion_id": p.get("champion_id", 0),
                "team_number": team_num,
                "damage": dmg,
                "kills": p.get("kills", 0),
                "deaths": p.get("deaths", 0),
                "assists": p.get("assists", 0),
                "items": get_item_build(items),
                "team_damage_rank": p.get("team_damage_rank"),
            }
            # 计算队友平均伤害
            if team_num >= 0:
                teammate_damages = [
                    op["total_damage_dealt_to_champions"]
                    for op in parts
                    if team_map.get(op["puuid"], -2) == team_num and op["puuid"] != puuid
                ]
                detail["teammate_avg_damage"] = (
                    round(sum(teammate_damages) / len(teammate_damages), 0)
                    if teammate_damages else 0
                )
            participant_details.append(detail)

            # 本局最高伤害
            if dmg > max_dmg and team_num >= 0:
                max_dmg = dmg
                max_dmg_player = detail

        # 计算本局全员的伤害排名（1=最高伤害）
        active_parts = [d for d in participant_details if d["team_number"] >= 0]
        active_parts.sort(key=lambda x: x["damage"], reverse=True)
        for rank_idx, d in enumerate(active_parts, 1):
            d["game_damage_rank"] = rank_idx
            player_rank_total[d["puuid"]] += rank_idx
            player_rank_games[d["puuid"]] += 1

        # 计算每人在本局的队友平均排名（越低=队友越强）
        for d in active_parts:
            teammates = [x for x in active_parts
                         if x["team_number"] == d["team_number"] and x["puuid"] != d["puuid"]]
            if teammates:
                tm_sum = sum(t.get("game_damage_rank", 3) for t in teammates)
                player_tm_rank_total[d["puuid"]] += tm_sum / len(teammates)
                player_tm_rank_games[d["puuid"]] += 1

        # 存储结果
        for puuid, score in scores.items():
            team_num = team_map.get(puuid, -1)
            all_scores.append({
                "game_id": gid,
                "puuid": puuid,
                "score": score,
                "team_number": team_num,
                "rule_type": rule,
            })
            player_totals[puuid] += score
            player_games[puuid] += 1
            player_teams[puuid] = team_num

        # 记录本局完整信息
        game_results.append({
            "game_id": gid,
            "rule": rule,
            "scores": scores,
            "participants": participant_details,
            "top_damage": max_dmg_player,
        })

    # 保存到数据库
    from database import save_review_scores
    save_review_scores(session_id, all_scores)

    # 汇总排名
    ranking = []
    for puuid, total in sorted(player_totals.items(), key=lambda x: x[1], reverse=True):
        team_num = player_teams.get(puuid, -1)
        dmg_avg = 0
        if player_damage_games.get(puuid, 0) > 0:
            dmg_avg = round(player_damage_total[puuid] / player_damage_games[puuid], 0)
        avg_rank = 0
        if player_rank_games.get(puuid, 0) > 0:
            avg_rank = round(player_rank_total[puuid] / player_rank_games[puuid], 1)
        avg_tm_rank = 0
        if player_tm_rank_games.get(puuid, 0) > 0:
            avg_tm_rank = round(player_tm_rank_total[puuid] / player_tm_rank_games[puuid], 1)
        ranking.append({
            "puuid": puuid,
            "game_name": player_name_cache.get(puuid, puuid[:8]),
            "team_number": team_num,
            "total_score": total,
            "games_played": player_games.get(puuid, 0),
            "avg_damage": dmg_avg,
            "avg_damage_rank": avg_rank,
            "avg_teammate_rank": avg_tm_rank,
        })

    # 伤害排名按平均名次排序（只算活跃玩家）
    active_ranking = [r for r in ranking if r["team_number"] >= 0]

    # 全场最佳
    mvp = ranking[0] if ranking else None

    return {
        "session_id": session_id,
        "session_name": session.get("name", ""),
        "total_games": len(game_results),
        "total_players": len(active_ranking),
        "ranking": ranking,
        "mvp": mvp,
        "game_results": game_results,
    }
