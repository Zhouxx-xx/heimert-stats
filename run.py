#!/usr/bin/env python3
"""
海克斯大乱斗 - 伤害复盘分析器

启动脚本：
  python run.py           # 启动 Web 仪表盘
  python run.py --fetch   # 只拉取数据（不启动 Web）
  python run.py --update  # 只更新排名（不启动 Web）
"""

import sys
import os
import logging
import webbrowser
import threading

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── 输出编码兜底：控制台/管道/重定向下 emoji 打印都不崩溃 ──
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── 判断是否 PyInstaller 打包 ──
FROZEN = getattr(sys, "frozen", False)


def _hide_console():
    """Web 模式下隐藏当前控制台窗口（双击启动不弹黑窗）；--show 参数可关闭此行为"""
    if sys.platform != "win32" or "--show" in sys.argv:
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:
        pass


def main():
    from database import init_database, get_all_games_count
    init_database()

    if "--fetch" in sys.argv:
        from lcu_client import LCUClient
        from database import insert_game, insert_summoner
        from analyzer import update_all_damage_ranks
        from augment_cn import load_augment_map_from_lcu

        client = LCUClient()
        if not client.connect():
            logger.error("无法连接到英雄联盟客户端，请确保游戏正在运行")
            return

        load_augment_map_from_lcu(client)

        summoner = client.get_current_summoner()
        if not summoner:
            logger.error("无法获取召唤师信息")
            return

        puuid = summoner.get("puuid")
        insert_summoner(summoner)
        logger.info(f"当前召唤师: {summoner.get('displayName')}")

        matches = client.fetch_all_matches(puuid=puuid, max_games=100)
        if not matches:
            logger.info("没有找到新的对局")
            return

        logger.info(f"获取到 {len(matches)} 场对局，正在获取详情...")
        details = client.fetch_game_details_batch(matches)

        new_games = 0
        for detail in details:
            if insert_game(detail, puuid):
                new_games += 1

        logger.info(f"新增 {new_games} 场对局")
        update_all_damage_ranks()
        logger.info(f"数据库中共 {get_all_games_count()} 场对局")
        return

    if "--update" in sys.argv:
        from analyzer import update_all_damage_ranks
        updated = update_all_damage_ranks()
        logger.info(f"已更新 {updated} 场对局的伤害排名")
        return

    # 默认：启动 Web 仪表盘
    _hide_console()  # 默认隐藏控制台窗口，设置面板可随时恢复
    from app import app, auto_update_worker, update_notice_worker, champ_select_worker, AUTO_UPDATE_RESULT

    if FROZEN:
        # 打包模式下修改控制台标题
        try:
            os.system("title 海克斯大乱斗分析器 v1.0")
        except Exception:
            pass

    print()
    print("=" * 50)
    print("   🎮 海克斯大乱斗 - 伤害复盘分析器")
    print("=" * 50)
    print(f"   数据目录: {os.path.join(os.path.dirname(sys.argv[0]), 'data')}")
    print()
    total = get_all_games_count()
    if total > 0:
        print(f"   ✅ 已有数据: {total} 场对局")
    else:
        print(f"   ⚠️  还没有数据，启动后请访问 /fetch 页面拉取")
    print()
    print(f"   🔄 正在更新战绩...")
    print()
    print(f"   🌐 浏览器已自动打开...")
    print(f"   如果未自动打开，请手动访问:")
    print(f"      http://127.0.0.1:5000")
    print()
    print(f"   💡 关闭此窗口即可停止服务")
    print("=" * 50)
    print()

    # 后台线程：自动增量更新战绩（游戏客户端未开则提示手动拉取）
    threading.Thread(target=auto_update_worker, daemon=True, name="auto-update").start()

    # 后台线程：静默检查云端是否有新版本（有则横幅+navbar 徽章提示）
    threading.Thread(target=update_notice_worker, daemon=True, name="update-notice").start()

    # 后台线程：实时选人面板（匹配进入选人阶段自动打开 /champ-select）
    threading.Thread(target=champ_select_worker, daemon=True, name="champ-select").start()

    # 延迟 1 秒后自动打开浏览器
    threading.Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:5000")).start()

    app.run(debug=False, host="127.0.0.1", port=5000)


if __name__ == "__main__":
    main()
