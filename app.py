"""
海克斯大乱斗伤害复盘分析器 - Web 仪表盘
"""

import os
import sys
import json
import logging
from datetime import datetime, date

import flask
from flask import Flask, render_template, request, jsonify, redirect, url_for

from database import (
    init_database, insert_game, insert_summoner, get_summoner,
    get_all_games_count, get_all_game_ids, get_recent_games, get_game_with_participants,
    get_damage_stats, get_champion_stats_all_players, get_player_damage_ranking,
    get_games_by_date_range, get_game_players,
    create_review_session, get_review_sessions, get_review_session,
    save_review_teams, get_review_teams, get_review_scores,
    save_review_result, get_review_result, get_review_results_list,
    delete_review_session, get_review_sessions_by_ids,
    get_champion_list, get_games_by_champion,
    get_games_without_augments, update_game_augments,
)
from lcu_client import LCUClient, MAYHEM_QUEUE_IDS, ARAM_QUEUE_ID
from analyzer import update_all_damage_ranks, get_summary_stats, get_champion_stats, get_damage_trend
from champion_cn import get_champion_name, en_to_cn
from cloud_db import (
    upload_games, get_upload_stats,
    get_cloud_player_ranking, get_cloud_champion_ranking,
    get_cloud_champion_list, get_cloud_champion_games,
    cloud_available,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ── 日志同时写入文件 logs/app.log（设置面板可一键打开） ──
if getattr(sys, 'frozen', False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_LOG_DIR = os.path.join(_BASE_DIR, "logs")
os.makedirs(_LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(_LOG_DIR, "app.log")
try:
    _fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logging.getLogger().addHandler(_fh)
except Exception:
    pass

# PyInstaller 打包时，模板和静态文件在 _MEIPASS 下
if getattr(sys, 'frozen', False):
    _base = sys._MEIPASS
    app = Flask(__name__,
                template_folder=os.path.join(_base, 'templates'),
                static_folder=os.path.join(_base, 'static'))
else:
    app = Flask(__name__)
app.config["TITLE"] = "海克斯大乱斗 - 伤害复盘分析器"


@app.context_processor
def inject_globals():
    """注入全局模板变量"""
    return dict(
        get_champion_name=get_champion_name,
        current_version=CURRENT_VERSION,
    )


def get_queue_name(queue_id: int) -> str:
    """获取队列名称"""
    if queue_id in MAYHEM_QUEUE_IDS:
        return "海克斯大乱斗"
    elif queue_id == ARAM_QUEUE_ID:
        return "极地大乱斗"
    return f"队列({queue_id})"


@app.route("/")
def index():
    """仪表盘首页"""
    init_database()
    total_games = get_all_games_count()
    summoner = get_summoner()

    stats = get_summary_stats()
    champ_stats = get_champion_stats()
    # 按平均伤害降序排列
    champ_stats.sort(key=lambda x: x.get("avg_damage", 0), reverse=True)
    player_dmg_rank = get_player_damage_ranking()

    return render_template(
        "index.html",
        total_games=total_games,
        summoner=summoner,
        stats=stats,
        champ_stats=champ_stats,
        player_dmg_rank=player_dmg_rank,
        queue_name=get_queue_name,
    )


@app.route("/matches")
def matches():
    """对局列表页面"""
    init_database()
    page = request.args.get("page", 1, type=int)
    queue_id = request.args.get("queue", type=int)
    per_page = 20
    offset = (page - 1) * per_page

    data = get_recent_games(limit=per_page, offset=offset, queue_id=queue_id)
    total_pages = (data["total"] + per_page - 1) // per_page

    return render_template(
        "matches.html",
        games=data["games"],
        page=page,
        total_pages=total_pages,
        total=data["total"],
        queue_id=queue_id,
        queue_name=get_queue_name,
    )


@app.route("/match/<int:game_id>")
def match_detail(game_id: int):
    """对局详情页面"""
    init_database()
    data = get_game_with_participants(game_id)
    if not data:
        return "对局不存在", 404

    # 按队伍分组，添加中文名
    for p in data["participants"]:
        p["champion_cn"] = get_champion_name(p["champion_id"], p.get("champion_name"))
    team_100 = [p for p in data["participants"] if p["team_id"] == 100]
    team_200 = [p for p in data["participants"] if p["team_id"] == 200]

    # 召唤师信息
    summoner = get_summoner()
    summoner_puuid = summoner["puuid"] if summoner else None

    return render_template(
        "match_detail.html",
        game=data["game"],
        team_100=team_100,
        team_200=team_200,
        summoner_puuid=summoner_puuid,
        queue_name=get_queue_name(data["game"]["queue_id"]),
    )


@app.route("/analysis")
@app.route("/rankings")
def rankings():
    """排行页面（云端池 + 本地降级）"""
    init_database()

    # ── 日期筛选 ──
    start_date = request.args.get("start")
    end_date = request.args.get("end")

    # ── 日期预设值 ──
    import datetime as _dt
    today = _dt.date.today()
    first_of_month = today.replace(day=1)
    this_month_start = first_of_month.isoformat()
    this_month_end = today.isoformat()
    # 上个月
    last_month_end = first_of_month - _dt.timedelta(days=1)
    last_month_start = last_month_end.replace(day=1).isoformat()
    last_month_end = last_month_end.isoformat()
    # 近3个月
    last_3m_start = (first_of_month - _dt.timedelta(days=90)).isoformat()

    # ── 云端状态（先查，用于判断是否降级） ──
    cloud_stats = get_upload_stats()
    cloud_has_data = cloud_stats.get("total_games", 0) > 0

    # ── 好友筛选 ──
    friends_only = request.args.get("friends") == "1"
    friend_puuids = None
    friends_error = ""
    if friends_only:
        friend_puuids = get_friend_puuids()
        if friend_puuids is None:
            friends_error = "未检测到游戏客户端，无法获取好友列表，当前显示全部玩家"
            friends_only = False  # 降级显示全部

    # ── 玩家伤害排行（云端优先 → 本地降级） ──
    player_ranking = get_cloud_player_ranking(limit=50, start_date=start_date, end_date=end_date,
                                              puuids=friend_puuids)
    if player_ranking is None or (not cloud_has_data and not player_ranking):
        # 云端不可用 或 云端无数据 → 降级到本地
        player_ranking = get_player_damage_ranking(limit=50, start_date=start_date, end_date=end_date,
                                                   puuids=friend_puuids)

    # ── 英雄伤害排行（云端优先 → 本地降级，不随日期筛选变动；全量返回） ──
    champ_ranking = get_cloud_champion_ranking(limit=None)
    if champ_ranking is None or (not cloud_has_data and not champ_ranking):
        # 云端不可用 或 云端无数据 → 降级到本地全部玩家英雄排行
        all_champ = get_champion_stats_all_players()
        champ_ranking = []
        for c in all_champ:
            cn = get_champion_name(c.get("champion_id", 0), c.get("champion_name"))
            champ_ranking.append({
                "champion_name": c.get("champion_name", ""),
                "champion_id": c.get("champion_id", 0),
                "champion_cn": cn,
                "games": c.get("games", 0),
                "avg_damage": c.get("avg_damage", 0),
                "avg_damage_pct": c.get("avg_damage_pct", 0),
                "avg_rank": c.get("avg_rank", 0),
                "avg_kills": c.get("avg_kills", 0),
                "avg_deaths": c.get("avg_deaths", 0),
                "avg_assists": c.get("avg_assists", 0),
                "wins": c.get("wins", 0),
                "win_rate": round(c.get("wins", 0) / max(c.get("games", 1), 1) * 100, 1),
            })

    data_source = "cloud" if cloud_has_data else "local"

    return render_template(
        "analysis.html",
        player_ranking=player_ranking or [],
        champ_ranking=champ_ranking or [],
        cloud_stats=cloud_stats,
        data_source=data_source,
        start_date=start_date or "",
        end_date=end_date or "",
        this_month_start=this_month_start,
        this_month_end=this_month_end,
        last_month_start=last_month_start,
        last_month_end=last_month_end,
        last_3m_start=last_3m_start,
        friends_only=friends_only,
        friends_error=friends_error,
    )


@app.route("/fetch", methods=["GET", "POST"])
def fetch_data():
    """从 LCU 拉取数据（增量：只拉新对局详情）"""
    if request.method == "GET":
        return render_template("fetch.html")

    # POST: 开始拉取
    try:
        client = LCUClient()
        if not client.connect():
            return jsonify({"success": False, "error": "无法连接到英雄联盟客户端，请确保游戏正在运行"})

        # 尝试加载增幅中文名
        from augment_cn import load_augment_map_from_lcu
        load_augment_map_from_lcu(client)

        # 获取召唤师
        summoner = client.get_current_summoner()
        if not summoner:
            return jsonify({"success": False, "error": "无法获取召唤师信息"})

        puuid = summoner.get("puuid")
        insert_summoner(summoner)

        # 拉取对局（增量：本地已有的 game_id 会被跳过详情拉取）
        count = request.json.get("count", 100) if request.is_json else 100
        known_ids = get_all_game_ids()
        matches = client.fetch_all_matches(puuid=puuid, max_games=count, known_game_ids=known_ids)

        if not matches:
            return jsonify({"success": True, "new_games": 0, "total": get_all_games_count(), "message": "没有找到新的对局"})

        # 只对新增对局获取详情并入库
        new_games = 0
        new_game_ids = []
        details = client.fetch_game_details_batch(matches)

        for detail in details:
            if insert_game(detail, puuid):
                new_games += 1
                new_game_ids.append(detail.get("gameId"))

        # 只重算新增对局的伤害排名（不再全量重算）
        if new_game_ids:
            update_all_damage_ranks(game_ids=new_game_ids)

        return jsonify({
            "success": True,
            "new_games": new_games,
            "total": get_all_games_count(),
            "message": f"成功拉取 {len(details)} 场对局，新增 {new_games} 场",
        })
    except Exception as e:
        logger.exception("拉取数据失败")
        return jsonify({"success": False, "error": str(e)})


# ═══════════════════════════════════════════════════════════════
# 复盘系统
# ═══════════════════════════════════════════════════════════════

@app.route("/review")
def review_page():
    """复盘主页"""
    init_database()
    sessions = get_review_sessions(limit=10)
    today = date.today().isoformat()
    summoner = get_summoner()
    my_puuid = summoner["puuid"] if summoner else ""
    my_name = (summoner or {}).get("game_name", "") or ""
    return render_template(
        "review.html",
        sessions=sessions,
        today=today,
        my_puuid=my_puuid,
        my_name=my_name,
        queue_name=get_queue_name,
        get_champion_name=get_champion_name,
    )


@app.route("/review/api/games")
def review_api_games():
    """API: 获取日期范围内的对局"""
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    if not start or not end:
        return jsonify({"error": "需要 start 和 end 参数"}), 400
    games = get_games_by_date_range(start, end, limit=200)
    # 给英雄名转中文
    for g in games:
        cn = get_champion_name(g.get("champion_id", 0), g.get("champion_name"))
        g["champion_cn"] = cn
    return jsonify({"games": games, "total": len(games)})


@app.route("/review/api/players", methods=["POST"])
def review_api_players():
    """API: 获取指定对局的玩家列表"""
    data = request.get_json() or {}
    game_ids = data.get("game_ids", [])
    if not game_ids:
        return jsonify({"error": "需要 game_ids"}), 400
    players = get_game_players(game_ids)
    # 英雄名转中文
    for p in players:
        cn = get_champion_name(p.get("champion_id", 0), p.get("champion_name"))
        p["champion_cn"] = cn
    return jsonify({"players": players})


@app.route("/review/api/session", methods=["POST"])
def review_api_create_session():
    """API: 创建复盘会话"""
    data = request.get_json() or {}
    name = data.get("name", f"复盘 {date.today().isoformat()}")
    start_date = data.get("start_date", "")
    end_date = data.get("end_date", "")
    game_ids = data.get("game_ids", [])
    teams = data.get("teams", {})

    if not game_ids:
        return jsonify({"error": "请至少选择一场对局"}), 400

    sid = create_review_session(name, start_date, end_date, game_ids)
    if teams:
        save_review_teams(sid, teams)

    return jsonify({"session_id": sid})


@app.route("/review/api/sessions")
def review_api_sessions():
    """API: 获取复盘会话列表"""
    sessions = get_review_sessions(limit=20)
    return jsonify({"sessions": sessions})


@app.route("/review/api/session/<int:session_id>")
def review_api_session(session_id: int):
    """API: 获取复盘会话详情"""
    session = get_review_session(session_id)
    if not session:
        return jsonify({"error": "会话不存在"}), 404
    teams = get_review_teams(session_id)
    scores = get_review_scores(session_id)
    return jsonify({
        "session": session,
        "teams": teams,
        "scores": scores,
    })


@app.route("/review/api/teams", methods=["POST"])
def review_api_save_teams():
    """API: 保存队伍分配"""
    data = request.get_json() or {}
    session_id = data.get("session_id")
    teams = data.get("teams", {})
    if not session_id:
        return jsonify({"error": "需要 session_id"}), 400
    save_review_teams(session_id, teams)
    return jsonify({"success": True})


@app.route("/review/api/session/<int:session_id>/game/<int:game_id>/teams", methods=["POST"])
def review_api_save_game_teams(session_id: int, game_id: int):
    """API: 保存一局游戏的队伍分配"""
    data = request.get_json() or {}
    teams = data.get("teams", {})
    from database import save_game_teams
    save_game_teams(session_id, game_id, teams)
    return jsonify({"success": True})


@app.route("/review/api/session/<int:session_id>/game-teams")
def review_api_get_game_teams(session_id: int):
    """API: 获取 session 所有局的队伍分配"""
    from database import get_all_game_teams
    teams = get_all_game_teams(session_id)
    return jsonify({"games": teams})


@app.route("/review/api/session/<int:session_id>", methods=["DELETE"])
def review_api_delete_session(session_id: int):
    """API: 删除复盘会话"""
    delete_review_session(session_id)
    return jsonify({"success": True})


@app.route("/review/api/sessions/merge", methods=["POST"])
def review_api_merge_sessions():
    """API: 合并多个复盘会话为一个新会话"""
    data = request.get_json() or {}
    session_ids = data.get("session_ids", [])
    if len(session_ids) < 2:
        return jsonify({"error": "至少选择2个复盘"}), 400

    sessions = get_review_sessions_by_ids(session_ids)
    if not sessions:
        return jsonify({"error": "未找到有效复盘"}), 400

    # 合并所有 game_ids（去重）
    all_game_ids = []
    seen = set()
    import json as _json
    for s in sessions:
        gids = _json.loads(s.get("game_ids", "[]"))
        for gid in gids:
            if gid not in seen:
                seen.add(gid)
                all_game_ids.append(gid)

    if not all_game_ids:
        return jsonify({"error": "合并的复盘中无可用的对局"}), 400

    # 创建新会话
    names = [s.get("name", "") for s in sessions if s.get("name")]
    merged_name = " + ".join(names[:3])
    if len(names) > 3:
        merged_name += f" 等{len(names)}个"
    merged_name = merged_name or f"大复盘 {date.today().isoformat()}"

    dates = [s.get("start_date", "") for s in sessions if s.get("start_date")]
    end_dates = [s.get("end_date", "") for s in sessions if s.get("end_date")]
    start = min(dates) if dates else ""
    end = max(end_dates) if end_dates else ""

    new_sid = create_review_session(merged_name, start, end, all_game_ids)
    return jsonify({"session_id": new_sid, "name": merged_name, "game_ids": all_game_ids, "game_count": len(all_game_ids)})


@app.route("/review/api/calculate", methods=["POST"])
def review_api_calculate():
    """API: 计算积分并自动保存结果"""
    data = request.get_json() or {}
    session_id = data.get("session_id")
    if not session_id:
        return jsonify({"error": "需要 session_id"}), 400

    from reviews import calculate_session
    import json
    result = calculate_session(session_id)
    # 自动缓存结果
    if "error" not in result:
        save_review_result(session_id, json.dumps(result, ensure_ascii=False))
    return jsonify(result)


@app.route("/review/api/saved-results")
def review_api_saved_results():
    """API: 获取已保存的复盘结果列表"""
    results = get_review_results_list(limit=20)
    return jsonify({"results": results})


@app.route("/review/api/saved-result/<int:session_id>")
def review_api_saved_result(session_id: int):
    """API: 获取复盘结果——实时重算，确保使用最新计分逻辑"""
    from reviews import calculate_session
    import json as _json
    result = calculate_session(session_id)
    if "error" in result:
        return jsonify({"error": result["error"]}), 404
    # 缓存最新结果
    save_review_result(session_id, _json.dumps(result, ensure_ascii=False))
    # 组装返回格式与之前一致
    return jsonify({"session_id": session_id, "result": result})


@app.route("/review/api/player-matches", methods=["POST"])
def review_api_player_matches():
    """API: 获取玩家在各局的数据"""
    data = request.get_json() or {}
    game_ids = data.get("game_ids", [])
    if not game_ids:
        return jsonify({"error": "需要 game_ids"}), 400

    from database import get_players_in_games
    participants = get_players_in_games(game_ids)
    # 英雄名转中文
    for p in participants:
        cn = get_champion_name(p.get("champion_id", 0), p.get("champion_name"))
        p["champion_cn"] = cn
    return jsonify({"participants": participants})


@app.route("/api/stats")
def api_stats():
    """API: 统计数据"""
    stats = get_summary_stats()
    return jsonify(stats)


@app.route("/api/matches")
def api_matches():
    """API: 对局列表"""
    page = request.args.get("page", 1, type=int)
    per_page = 20
    data = get_recent_games(limit=per_page, offset=(page - 1) * per_page)
    return jsonify(data)


@app.route("/champion-query")
def champion_query():
    """英雄查询页面（云端所有上传者数据优先 → 本地降级）"""
    init_database()
    # 云端优先
    champions = get_cloud_champion_list()
    if champions is None:
        # 云端不可用 → 本地
        champions = get_champion_list()
    # 补充英雄中文名
    for c in champions:
        c["champion_cn"] = get_champion_name(c["champion_id"], c.get("champion_name"))

    # 英雄伤害排行名次映射：champion_id → 名次（云端优先 → 本地降级）
    champ_rank_map = {}
    try:
        ranking = get_cloud_champion_ranking(limit=None)
        if ranking is None:
            from database import get_champion_stats_all_players
            ranking = get_champion_stats_all_players()
        if ranking:
            for idx, r in enumerate(ranking, start=1):
                champ_rank_map[r.get("champion_id")] = idx
    except Exception as e:
        logger.warning(f"获取英雄排行名次失败: {e}")

    return render_template("champion_query.html", champions=champions, champ_rank_map=champ_rank_map)


@app.route("/api/champion-games/<int:champion_id>")
def api_champion_games(champion_id: int):
    """API: 获取某个英雄的所有出场记录（云端优先 → 本地降级）"""
    games = get_cloud_champion_games(champion_id)
    if games is None:
        games = get_games_by_champion(champion_id)
    # 补充英雄中文名 + 装备中文名 + 增幅名
    from item_cn import get_item_build
    from augment_cn import get_augment_display
    for g in games:
        g["champion_cn"] = get_champion_name(g["champion_id"], g.get("champion_name"))
        items = [g.get(f"item{i}", 0) for i in range(7)]
        g["items"] = get_item_build(items)
        # 增幅信息
        aug_ids = [g.get(f"player_augment{i}", 0) for i in range(1, 5)]
        g["augments"] = [get_augment_display(aid) for aid in aug_ids if aid]
        # 格式化时间
        from datetime import datetime
        g["game_time"] = datetime.fromtimestamp(g["game_creation"] / 1000).strftime("%m/%d %H:%M")
    return jsonify({"games": games, "total": len(games)})


@app.route("/api/backfill-augments", methods=["POST"])
def api_backfill_augments():
    """补拉已有对局的增幅数据"""
    try:
        client = LCUClient()
        if not client.connect():
            return jsonify({"success": False, "error": "无法连接到英雄联盟客户端"})

        # 加载增幅名称（如果可用）
        from augment_cn import load_augment_map_from_lcu
        load_augment_map_from_lcu(client)

        games = get_games_without_augments(limit=50)
        if not games:
            return jsonify({"success": True, "updated": 0, "message": "所有对局已有增幅数据"})

        updated = 0
        errors = 0
        for g in games:
            gid = g["game_id"]
            try:
                # 从 LCU 重新拉取详情
                detail = client._request("GET", f"/lol-match-history/v1/games/{gid}")
                if not detail:
                    errors += 1
                    continue

                # 提取增幅数据
                participants_augments = {}
                for p in detail.get("participants", []):
                    pid = p.get("participantId", 0)
                    stats = p.get("stats", p)
                    a1 = stats.get("playerAugment1", 0) or p.get("playerAugment1", 0)
                    a2 = stats.get("playerAugment2", 0) or p.get("playerAugment2", 0)
                    a3 = stats.get("playerAugment3", 0) or p.get("playerAugment3", 0)
                    a4 = stats.get("playerAugment4", 0) or p.get("playerAugment4", 0)
                    if a1 or a2 or a3 or a4:
                        participants_augments[pid] = [a1, a2, a3, a4]

                if participants_augments:
                    update_game_augments(gid, participants_augments)
                    updated += 1
            except Exception as e:
                logger.warning(f"补拉对局 {gid} 失败: {e}")
                errors += 1

        return jsonify({
            "success": True,
            "updated": updated,
            "errors": errors,
            "total_checked": len(games),
            "message": f"成功补拉 {updated} 场，失败 {errors} 场",
        })
    except Exception as e:
        logger.exception("补拉增幅数据失败")
        return jsonify({"success": False, "error": str(e)})


# ═══════════════════════════════════════════════════════════════
# 云端上传 + 排行 API
# ═══════════════════════════════════════════════════════════════

@app.route("/api/upload-cloud", methods=["POST"])
def api_upload_cloud():
    """上传本地所有对局到云端（去重）"""
    try:
        result = _upload_games_to_cloud()
        return jsonify(result)
    except Exception as e:
        logger.exception("上传云端失败")
        return jsonify({"success": False, "error": str(e)})


def _upload_games_to_cloud(game_ids: list = None) -> dict:
    """
    上传对局到云端（去重）。
    game_ids: 只上传指定的 game_id 列表；None 表示上传本地所有对局。
    """
    init_database()
    from database import get_all_games, get_participants_for_game

    # 获取召唤师信息
    summoner = get_summoner()
    if not summoner:
        return {"success": False, "error": "未找到召唤师信息，请先拉取数据"}
    uploader_puuid = summoner["puuid"]
    uploader_name = summoner.get("game_name", "") or summoner.get("display_name", "") or "未知玩家"

    # 读取本地对局（可按 game_ids 过滤）
    all_games = get_all_games()
    if not all_games:
        return {"success": False, "error": "本地没有对局数据"}
    if game_ids:
        id_set = set(game_ids)
        all_games = [g for g in all_games if g["game_id"] in id_set]

    games_data = []
    participants_data = {}
    for g in all_games:
        gid = g["game_id"]
        games_data.append({
            "game_id": gid,
            "queue_id": g.get("queue_id", 0),
            "game_mode": g.get("game_mode", ""),
            "game_type": g.get("game_type", ""),
            "map_id": g.get("map_id", 0),
            "game_creation": g.get("game_creation", 0),
            "game_duration": g.get("game_duration", 0),
            "game_version": g.get("game_version", ""),
            "platform_id": g.get("platform_id", ""),
            "is_remake": g.get("is_remake", 0),
        })
        parts = get_participants_for_game(gid)
        participants_data[gid] = parts

    result = upload_games(games_data, participants_data, uploader_puuid, uploader_name)
    return {
        "success": result["errors"] == 0,
        "uploaded": result["uploaded"],
        "skipped": result["skipped"],
        "errors": result["errors"],
        "message": f"上传完成：新增{result['uploaded']}场，跳过{result['skipped']}场，失败{result['errors']}场",
    }


# ── 自动增量更新（程序启动时静默拉取新对局） ──────────────────
# status: pending(未开始) / updating(更新中) / done(完成) / no_client(客户端未开) / error(失败)

AUTO_UPDATE_RESULT = {
    "status": "pending",
    "new_games": 0,
    "uploaded": 0,
    "message": "",
    "error": "",
    "acknowledged": False,  # 提示条是否已提醒过（每次程序启动只提醒一次）
}


# ── 版本更新检查（云端 app_updates 表，BLOB 方案） ─────────────
# 每次打包发布前手动把 CURRENT_VERSION 改成要发布的版本号，
# 然后运行 upload_update.py 上传新 zip 到云端公告表。

CURRENT_VERSION = "1.3.0"  # 本程序当前版本（与发布包一致）

# status: pending(未开始) / has_update(有新版本) / up_to_date(已是最新) / error(检查失败)
UPDATE_NOTICE = {
    "status": "pending",
    "version": "",
    "title": "",
    "notes": "",
    "created_at": "",
    "download_url": "",  # 前端横幅点击下载用
    "error": "",
}


def parse_version(v: str) -> list:
    """"1.2.0" → [1, 2, 0]；忽略非数字段，空串 → [0]"""
    parts = []
    for seg in str(v).strip().split("."):
        digits = "".join(ch for ch in seg if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return parts or [0]


def compare_versions(current: str, latest: str) -> int:
    """比较两个版本号：latest > current 返回 1，相等返回 0，小于返回 -1。
    逐段比较数字（"1.10.0" > "1.9.0"）。"""
    c, l = parse_version(current), parse_version(latest)
    n = max(len(c), len(l))
    c += [0] * (n - len(c))
    l += [0] * (n - len(l))
    return (l > c) - (l < c)


def update_notice_worker():
    """程序启动后后台线程：静默检查云端是否有新版本（失败不影响主流程）。
    状态流转：pending → has_update / up_to_date / error"""
    import time
    try:
        time.sleep(4)  # 等服务启动
        from cloud_db import check_latest_update
        info = check_latest_update()
        if not info:
            UPDATE_NOTICE.update({"status": "up_to_date"})
            return
        if compare_versions(CURRENT_VERSION, info["version"]) > 0:
            UPDATE_NOTICE.update({
                "status": "has_update",
                "version": info["version"],
                "title": info.get("title", "") or "",
                "notes": info.get("notes", "") or "",
                "created_at": info.get("created_at", "") or "",
                "download_url": f"/api/update/download?id={info['id']}",
            })
            logger.info(f"发现新版本 {info['version']}（当前 {CURRENT_VERSION}）")
        else:
            UPDATE_NOTICE.update({"status": "up_to_date", "version": info["version"]})
    except Exception as e:
        logger.warning(f"检查新版本失败: {e}")
        UPDATE_NOTICE.update({"status": "error", "error": str(e)})


# ── 实时选人阶段面板（champ-select） ─────────────────────────
# active: 是否处于选人阶段；my_team: 我方 5 人的英雄资料（云端名次）

CHAMP_SELECT = {
    "active": False,
    # phase: idle(未匹配) / selecting(选人中) / locked(选人结束已进对局，面板冻结保留)
    "phase": "idle",
    "my_team": [],       # [{cell_id, is_self, summoner_name, champion_id, champion_cn,
    #                       rank, games, avg_damage, win_rate, champion_name}]
    "bench": [],         # 大乱斗待选池：[{champion_id, champion_cn, rank, games, avg_damage, win_rate, champion_name}]
    "bench_enabled": False,  # 是否大乱斗（session.benchEnabled）
    "hero_map": {},      # {champion_id: {rank, games, avg_damage, win_rate, ...}} 带 30s 缓存
    "hero_map_ts": 0,
    "my_champion": None,  # 我本局最终选定的英雄（选人阶段实时跟随，进对局后固定）
    "my_stats": None,     # 该英雄的历史战绩（database.get_champion_detail 结果）
    "updated_at": "",
    "error": "",
}

_CHAMP_SELECT_OPENED = False  # 本次选人是否已自动打开过页面（避免重复弹窗）

# 本局英雄战绩缓存 {champion_id, data} —— 只在换英雄时重算，避免每次轮询都查库
_MY_STATS_CACHE = {"champion_id": None, "data": None}

# 连续多少轮检测到「既不在选人、也不在对局」。
# 选人结束的那一瞬间 gameflow 阶段可能还没切到 GameStart，
# 若立刻清空会误伤，所以要求连续两轮确认后才真正清空。
_CHAMP_SELECT_GONE = 0

# 这些 gameflow 阶段表示「选人结束、已进入对局（或结算）」，
# 此时选人 session 已消失，但页面应当冻结保留而不是清空。
IN_GAME_PHASES = {
    "GameStart", "Loading", "InProgress", "Reconnect",
    "PreEndOfGame", "WaitingForStats", "EndOfGame",
}

# 伤害榜名次 → 马级：T0 上等马(≤20) / T1 中等马(≤60) / T2 下等马(≤100) / T3 没有马(>100 或无排名)
TIER_NAMES = {"t0": "上等马", "t1": "中等马", "t2": "下等马", "t3": "没有马"}


def _tier_of(rank) -> str:
    """伤害榜名次转马级（无排名或 >100 → t3 没有马）"""
    if not rank:
        return "t3"
    if rank <= 20:
        return "t0"
    if rank <= 60:
        return "t1"
    if rank <= 100:
        return "t2"
    return "t3"


# 选人 session 不再返回 summonerName，用 summonerId 补查 /lol-summoner 并缓存（避免每 2 秒重复请求）
_SUMMONER_NAME_CACHE = {}


def _resolve_summoner_name(client, summoner_id):
    """按 summonerId 解析召唤师名字，带缓存；查不到返回 None（调用方回退「我方 #N」）"""
    if not summoner_id:
        return None
    if summoner_id in _SUMMONER_NAME_CACHE:
        return _SUMMONER_NAME_CACHE[summoner_id]
    name = client.get_summoner_name(summoner_id)
    if name:
        _SUMMONER_NAME_CACHE[summoner_id] = name
    return name


def _set_champ_select_inactive(error: str = ""):
    """清空选人状态（只在状态变化时更新，避免每 2 秒重写前端）

    注意：只有「真的回到大厅 / 客户端未运行」才应该调用这里。
    选人结束进入对局时要走 _freeze_champ_select()，页面保留不清空。
    """
    # 这一局已经结束，本局英雄战绩缓存作废 ——
    # 否则下一局再摇到同一个英雄会命中缓存，显示少一场的旧统计。
    _MY_STATS_CACHE["champion_id"] = None
    _MY_STATS_CACHE["data"] = None
    if (CHAMP_SELECT.get("active") or CHAMP_SELECT.get("error") != error
            or CHAMP_SELECT.get("phase") != "idle"
            or CHAMP_SELECT.get("my_champion") or CHAMP_SELECT.get("my_stats")):
        CHAMP_SELECT.update({
            "active": False,
            "phase": "idle",
            "my_team": [],
            "bench": [],
            "bench_enabled": False,
            "hero_map": {},
            "hero_map_ts": 0,
            "my_champion": None,
            "my_stats": None,
            "updated_at": "",
            "error": error,
        })


def _refresh_my_stats(force: bool = False):
    """计算「我本局英雄」的历史战绩，供选人阶段与对局中直接查看。

    结果按 champion_id 缓存：同一英雄重复调用直接复用，换英雄才重查。
    """
    champ = CHAMP_SELECT.get("my_champion") or {}
    cid = champ.get("champion_id")
    if not cid or cid <= 0:
        CHAMP_SELECT["my_stats"] = None
        return
    if not force and _MY_STATS_CACHE.get("champion_id") == cid:
        CHAMP_SELECT["my_stats"] = _MY_STATS_CACHE.get("data")
        return
    data = None
    try:
        from database import get_champion_detail
        data = get_champion_detail(cid)
        logger.info(f"已载入本局英雄战绩: {champ.get('champion_cn') or cid} "
                    f"({(data or {}).get('games', 0)} 场)")
    except Exception as e:
        logger.warning(f"计算本局英雄战绩失败: {e}")
    _MY_STATS_CACHE["champion_id"] = cid
    _MY_STATS_CACHE["data"] = data
    CHAMP_SELECT["my_stats"] = data


def _freeze_champ_select():
    """选人结束、已进入对局：冻结面板，保留阵容并补算本局英雄战绩。

    与 _set_champ_select_inactive 相对 —— 这里不清空任何已选内容，
    只把 phase 切到 locked 并算一次英雄战绩（重复调用不会重复算）。
    """
    import time
    if CHAMP_SELECT.get("phase") == "locked":
        return  # 已冻结过，避免每 2 秒重复查库
    CHAMP_SELECT.update({
        "active": True,
        "phase": "locked",
        "error": "",
        "updated_at": time.strftime("%H:%M:%S"),
    })
    _refresh_my_stats()
    logger.info("选人结束，实时面板已冻结保留（对局中可查看本局英雄战绩）")


def _build_hero_map():
    """构建英雄名次映射（云端优先，本地降级），30 秒缓存避免频繁查库"""
    import time
    now = time.time()
    if CHAMP_SELECT["hero_map"] and now - CHAMP_SELECT["hero_map_ts"] < 30:
        return
    hero_map = {}
    try:
        from cloud_db import get_cloud_champion_rank_map
        hero_map = get_cloud_champion_rank_map() or {}
    except Exception as e:
        logger.warning(f"构建云端英雄名次失败: {e}")
    if not hero_map:
        # 本地降级：用本地英雄统计按场均伤害排序
        try:
            from database import get_champion_stats_all_players
            local = get_champion_stats_all_players() or []
            local.sort(key=lambda c: c.get("avg_damage", 0), reverse=True)
            for idx, c in enumerate(local):
                cid = c.get("champion_id")
                if cid is None:
                    continue
                hero_map[cid] = {
                    "rank": idx + 1,
                    "champion_name": c.get("champion_name", ""),
                    "champion_cn": get_champion_name(cid, c.get("champion_name")),
                    "games": c.get("games", 0),
                    "avg_damage": c.get("avg_damage", 0),
                    "win_rate": round(c.get("wins", 0) / max(c.get("games", 1), 1) * 100, 1),
                }
        except Exception as e:
            logger.warning(f"构建本地英雄名次失败: {e}")
    CHAMP_SELECT["hero_map"] = hero_map
    CHAMP_SELECT["hero_map_ts"] = now


def champ_select_worker():
    """程序启动后后台线程：每 2 秒轮询 LCU 选人 session。
    进入选人阶段自动打开 /champ-select 页面（每次选人只弹一次）。
    客户端断开自动重连。"""
    import time
    import webbrowser
    time.sleep(3)  # 等服务启动
    client = None
    global _CHAMP_SELECT_OPENED, _CHAMP_SELECT_GONE
    while True:
        time.sleep(2)
        try:
            if client is None:
                client = LCUClient()
                if not client.connect():
                    client = None
                    _set_champ_select_inactive("未检测到游戏客户端")
                    continue

            session = client.get_champ_select_session()
            if session is None:
                # 选人 session 消失，要区分两种情况：
                #   ① 选人刚结束、已进入对局 → 面板冻结保留，继续展示本局英雄战绩
                #   ② 回到大厅 / 匹配中     → 清空，等下一局
                phase = client.get_gameflow_phase()
                if phase in IN_GAME_PHASES and CHAMP_SELECT.get("my_team"):
                    _CHAMP_SELECT_GONE = 0
                    _freeze_champ_select()
                    continue
                # 既不在选人也不在对局：连续两轮确认后才清空，
                # 避免选人结束瞬间 gameflow 尚未切到 GameStart 时误清面板。
                _CHAMP_SELECT_GONE += 1
                if _CHAMP_SELECT_GONE < 2:
                    continue
                _CHAMP_SELECT_GONE = 0
                _set_champ_select_inactive("")
                _CHAMP_SELECT_OPENED = False  # 重置，下次选人可再弹窗
                continue

            # 在选人阶段：构建英雄名次（30s 缓存）+ 解析我方 5 人
            _build_hero_map()
            my_team = []
            local_cell = session.get("localPlayerCellId")
            for m in session.get("myTeam", []):
                cid = m.get("championId") or 0
                cell = m.get("cellId")
                summoner_name = m.get("summonerName") \
                    or _resolve_summoner_name(client, m.get("summonerId"))
                base = {
                    "cell_id": cell,
                    "is_self": cell == local_cell,
                    "summoner_name": summoner_name
                    or (f"我方 #{cell + 1}" if cell is not None else ""),
                }
                if cid <= 0:
                    # 未选英雄：占位，前端显示"待选择"
                    my_team.append({
                        **base,
                        "champion_id": 0,
                        "champion_name": "",
                        "champion_cn": "待选择",
                        "rank": None,
                        "games": 0,
                        "avg_damage": 0,
                        "win_rate": None,
                        "tier": "t3",
                    })
                    continue
                hero = CHAMP_SELECT["hero_map"].get(cid, {})
                my_team.append({
                    **base,
                    "champion_id": cid,
                    "champion_name": hero.get("champion_name", ""),
                    "champion_cn": hero.get("champion_cn")
                    or get_champion_name(cid, hero.get("champion_name")),
                    "rank": hero.get("rank"),
                    "games": hero.get("games", 0),
                    "avg_damage": hero.get("avg_damage", 0),
                    "win_rate": hero.get("win_rate", 0),
                    "tier": _tier_of(hero.get("rank")),
                })
            # 大乱斗待选池（benchChampions：全队共享的待选英雄，含名次资料）
            bench = []
            for b in session.get("benchChampions") or []:
                cid = b.get("championId") if isinstance(b, dict) else b
                if not cid or cid <= 0:
                    continue
                hero = CHAMP_SELECT["hero_map"].get(cid, {})
                bench.append({
                    "champion_id": cid,
                    "champion_name": hero.get("champion_name", ""),
                    "champion_cn": hero.get("champion_cn")
                    or get_champion_name(cid, hero.get("champion_name")),
                    "rank": hero.get("rank"),
                    "games": hero.get("games", 0),
                    "avg_damage": hero.get("avg_damage", 0),
                    "win_rate": hero.get("win_rate", 0),
                    "tier": _tier_of(hero.get("rank")),
                })
            # 记录「我」当前选定的英雄（选人阶段可能反复换，取最后一次；未选时不覆盖）
            self_champ = next(
                (m for m in my_team if m.get("is_self") and m.get("champion_id")), None
            )
            if self_champ:
                CHAMP_SELECT["my_champion"] = {
                    "champion_id": self_champ["champion_id"],
                    "champion_name": self_champ.get("champion_name", ""),
                    "champion_cn": self_champ.get("champion_cn", ""),
                    "rank": self_champ.get("rank"),
                    "games": self_champ.get("games", 0),
                    "avg_damage": self_champ.get("avg_damage", 0),
                    "win_rate": self_champ.get("win_rate"),
                    "tier": self_champ.get("tier", "t3"),
                }
                # 选人阶段就先把战绩备好（按英雄缓存，换英雄才重查）
                _refresh_my_stats()

            CHAMP_SELECT.update({
                "active": True,
                "phase": "selecting",
                "my_team": my_team,
                "bench": bench,
                "bench_enabled": bool(session.get("benchEnabled")),
                "updated_at": time.strftime("%H:%M:%S"),
                "error": "",
            })

            # 进入选人阶段自动打开实时对战页面（每次选人只弹一次）
            if not _CHAMP_SELECT_OPENED:
                _CHAMP_SELECT_OPENED = True
                logger.info("检测到选人阶段，自动打开实时对战面板")
                try:
                    webbrowser.open("http://127.0.0.1:5000/champ-select")
                except Exception as e:
                    logger.warning(f"自动打开选人页面失败: {e}")
        except ConnectionError:
            client = None
            _set_champ_select_inactive("游戏客户端已断开")
        except Exception as e:
            logger.warning(f"选人阶段检查异常: {e}")
            time.sleep(5)  # 异常后放慢节奏，避免高频重试


def auto_update_worker():
    """程序启动后后台线程：增量拉取新对局并自动上传云端。
    状态流转：pending → updating → done / no_client / error
    """
    import time
    try:
        AUTO_UPDATE_RESULT.update({"status": "updating", "message": "正在更新战绩..."})
        time.sleep(3)  # 等服务启动

        client = LCUClient()
        if not client.connect():
            logger.info("自动更新：未检测到游戏客户端")
            AUTO_UPDATE_RESULT.update({
                "status": "no_client",
                "message": "未检测到游戏客户端，请打开客户端后手动拉取",
            })
            return

        # 尝试加载增幅中文名（失败不阻塞）
        try:
            from augment_cn import load_augment_map_from_lcu
            load_augment_map_from_lcu(client)
        except Exception:
            pass

        summoner = client.get_current_summoner()
        if not summoner:
            logger.info("自动更新：无法获取召唤师信息")
            AUTO_UPDATE_RESULT.update({
                "status": "no_client",
                "message": "未检测到游戏客户端，请打开客户端后手动拉取",
            })
            return

        puuid = summoner.get("puuid")
        insert_summoner(summoner)

        # 增量拉取最近 100 场（本地已有的会跳过）
        known_ids = get_all_game_ids()
        matches = client.fetch_all_matches(puuid=puuid, max_games=100, known_game_ids=known_ids)

        if not matches:
            AUTO_UPDATE_RESULT.update({"status": "done", "new_games": 0, "message": "没有新的对局"})
            logger.info("自动更新：没有新的对局")
            return

        details = client.fetch_game_details_batch(matches)
        new_game_ids = []
        for detail in details:
            if insert_game(detail, puuid):
                new_game_ids.append(detail.get("gameId"))

        # 只重算新增对局的伤害排名
        if new_game_ids:
            update_all_damage_ranks(game_ids=new_game_ids)

        # 自动上传云端
        uploaded = 0
        upload_msg = ""
        if new_game_ids:
            try:
                up = _upload_games_to_cloud(game_ids=new_game_ids)
                uploaded = up.get("uploaded", 0)
                upload_msg = f"，云端新增{uploaded}场"
            except Exception as e:
                logger.warning(f"自动上传云端失败: {e}")
                upload_msg = "，云端上传失败"

        AUTO_UPDATE_RESULT.update({
            "status": "done",
            "new_games": len(new_game_ids),
            "uploaded": uploaded,
            "message": f"已自动更新 {len(new_game_ids)} 场新对局{upload_msg}",
        })
        logger.info(f"自动更新完成：新增 {len(new_game_ids)} 场对局{upload_msg}")
    except Exception as e:
        logger.exception("自动更新失败")
        AUTO_UPDATE_RESULT.update({"status": "error", "error": str(e), "message": f"自动更新失败: {e}"})


def get_friend_puuids() -> "set | None":
    """获取当前用户 + 好友的 puuid 集合（用于玩家排行好友筛选）。
    游戏客户端未开 → 返回 None（表示无法筛选好友）。"""
    from database import get_summoner
    summoner = get_summoner()
    own = {summoner["puuid"]} if summoner and summoner.get("puuid") else set()

    client = LCUClient()
    if not client.connect():
        return None
    friends = client.get_friends()
    return own | {f["puuid"] for f in friends if f.get("puuid")}


@app.route("/api/auto-update-status")
def api_auto_update_status():
    """API: 自动更新结果（前端用于显示提示条）"""
    return jsonify(AUTO_UPDATE_RESULT)


@app.route("/api/auto-update-ack", methods=["POST"])
def api_auto_update_ack():
    """API: 前端确认已提醒（提示条只弹一次，程序重启后才再提醒）"""
    AUTO_UPDATE_RESULT["acknowledged"] = True
    return jsonify({"ok": True})


@app.route("/api/settings")
def api_settings():
    """API: 设置面板数据（日志文件路径、是否有控制台窗口可恢复）"""
    has_console = False
    if sys.platform == "win32":
        try:
            import ctypes
            has_console = bool(ctypes.windll.kernel32.GetConsoleWindow())
        except Exception:
            has_console = False
    return jsonify(log_file=LOG_FILE, has_console=has_console)


@app.route("/api/open-log", methods=["POST"])
def api_open_log():
    """API: 用系统默认程序打开日志文件（记事本等）"""
    try:
        os.startfile(LOG_FILE)
        return jsonify({"ok": True})
    except Exception as e:
        logger.exception("打开日志文件失败")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/open-terminal", methods=["POST"])
def api_open_terminal():
    """API: 恢复被隐藏的控制台窗口（SW_SHOW），用于查看实时日志输出"""
    if sys.platform != "win32":
        return jsonify({"ok": False, "error": "仅支持 Windows"}), 400
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if not hwnd:
            return jsonify({"ok": False, "error": "当前没有控制台窗口（可能以无窗口方式启动）"}), 400
        ctypes.windll.user32.ShowWindow(hwnd, 5)  # SW_SHOW
        return jsonify({"ok": True})
    except Exception as e:
        logger.exception("恢复控制台窗口失败")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/check-update")
def api_check_update():
    """API: 版本更新检查结果（前端启动时拉取一次，用于显示新版本横幅）"""
    return jsonify(UPDATE_NOTICE)


@app.route("/api/update/download")
def api_update_download():
    """API: 下载新版本 zip（从云端 MySQL 读 BLOB 转发给浏览器）"""
    from io import BytesIO
    from flask import send_file
    from cloud_db import get_update_zip

    update_id = request.args.get("id", type=int)
    if not update_id:
        return jsonify({"error": "缺少参数 id"}), 400
    try:
        data = get_update_zip(update_id)
        if not data:
            return jsonify({"error": "更新包不存在或云端不可用"}), 404
        return send_file(
            BytesIO(data["zip_data"]),
            mimetype="application/zip",
            as_attachment=True,
            download_name=data.get("zip_name") or "小熊海克斯复盘-更新.zip",
        )
    except Exception as e:
        logger.exception("下载更新包失败")
        return jsonify({"error": str(e)}), 500


@app.route("/api/champ-select")
def api_champ_select():
    """API: 实时选人面板数据（前端每 2 秒轮询）"""
    return jsonify(CHAMP_SELECT)


@app.route("/champ-select")
def champ_select_page():
    """页面: 实时选人面板（匹配进入选人阶段自动打开）"""
    return render_template("champ_select.html")


@app.route("/api/ranking/players")
def api_ranking_players():
    """API: 玩家伤害排行（云端优先）"""
    try:
        start = request.args.get("start")
        end = request.args.get("end")
        ranking = get_cloud_player_ranking(limit=50, start_date=start, end_date=end)
        if ranking is not None:
            return jsonify({"source": "cloud", "ranking": ranking})
        # 降级到本地
        from database import get_player_damage_ranking
        local = get_player_damage_ranking(limit=50, start_date=start, end_date=end)
        return jsonify({"source": "local", "ranking": local})
    except Exception as e:
        logger.exception("获取玩家排行失败")
        return jsonify({"source": "local", "ranking": [], "error": str(e)})


@app.route("/api/ranking/champions")
def api_ranking_champions():
    """API: 英雄伤害排行（云端优先）"""
    try:
        start = request.args.get("start")
        end = request.args.get("end")
        ranking = get_cloud_champion_ranking(limit=50, start_date=start, end_date=end)
        if ranking is not None:
            return jsonify({"source": "cloud", "ranking": ranking})
        # 降级到本地
        from database import get_champion_stats_all_players
        local = get_champion_stats_all_players()
        for c in local:
            cn = get_champion_name(c.get("champion_id", 0), c.get("champion_name"))
            c["champion_cn"] = cn
            c["win_rate"] = round(c.get("wins", 0) / max(c.get("games", 1), 1) * 100, 1)
        return jsonify({"source": "local", "ranking": local})
    except Exception as e:
        logger.exception("获取英雄排行失败")
        return jsonify({"source": "local", "ranking": [], "error": str(e)})


@app.route("/api/cloud-stats")
def api_cloud_stats():
    """API: 云端数据统计"""
    return jsonify(get_upload_stats())


if __name__ == "__main__":
    init_database()
    # 启动自动增量更新线程（静默拉取新对局并上传云端）
    # debug reloader 下子进程会重新执行本模块，用 WERKZEUG_RUN_MAIN 避免重复启动
    if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        import threading
        threading.Thread(target=auto_update_worker, daemon=True, name="auto-update").start()
        threading.Thread(target=update_notice_worker, daemon=True, name="update-notice").start()
        threading.Thread(target=champ_select_worker, daemon=True, name="champ-select").start()
        print(f"程序启动后将自动检查并更新新对局（需游戏客户端运行中）")
        print(f"程序启动后将检查云端是否有新版本")
    print(f"启动海克斯大乱斗伤害复盘分析器...")
    print(f"访问地址: http://127.0.0.1:5000")
    app.run(debug=True, host="127.0.0.1", port=5000)
