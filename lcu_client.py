"""
LCU (League Client Update) API 客户端 + 腾讯 SGP API 客户端

连接方式（重要）：
1. 通过 psutil 查 LeagueClientUx.exe 进程命令行，获取端口和密码
   （不用 lockfile！腾讯版 LCU 端口/密码在 Ux 进程的命令行参数里）
2. 连接 LCU API，拿到 entitlementsToken 和 leagueSessionToken
3. 用 Token 直接调腾讯 SGP 服务器（lol.qq.com:21019）拿对战历史
   SGP 不走 Riot Match-V5，不会被 403 拦截
"""

import subprocess
import re
import time
import json
import base64
import logging
from datetime import datetime

import requests
import urllib3
from pathlib import Path
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

# 禁用 SSL 警告（LCU 自签名证书 + SGP 服务器）
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 海克斯大乱斗 (ARAM Mayhem) queueId
MAYHEM_QUEUE_IDS = [2400, 2401, 2402, 2403, 2404, 2405]
ARAM_QUEUE_ID = 450

# ─── 腾讯 SGP 服务器配置（从 Akari 扒的） ─────────────────────────

# rso_platform_id → SGP 服务器映射
# 命令行里是 HN1，SGP 配置里是 TENCENT_HN1
SGP_SERVER_IDS = {
    "HN1":    "TENCENT_HN1",
    "HN10":   "TENCENT_HN10",
    "TJ100":  "TENCENT_TJ100",
    "TJ101":  "TENCENT_TJ101",
    "NJ100":  "TENCENT_NJ100",
    "GZ100":  "TENCENT_GZ100",
    "CQ100":  "TENCENT_CQ100",
    "BGP2":   "TENCENT_BGP2",
}

SGP_SERVERS = {
    "TENCENT_HN1":   {"url": "https://hn1-k8s-sgp.lol.qq.com:21019",    "name": "艾欧尼亚"},
    "TENCENT_HN10":  {"url": "https://hn10-k8s-sgp.lol.qq.com:21019",   "name": "黑色玫瑰"},
    "TENCENT_TJ100": {"url": "https://tj100-sgp.lol.qq.com:21019",      "name": "联盟四区"},
    "TENCENT_TJ101": {"url": "https://tj101-sgp.lol.qq.com:21019",      "name": "联盟五区"},
    "TENCENT_NJ100": {"url": "https://nj100-sgp.lol.qq.com:21019",      "name": "联盟一区"},
    "TENCENT_GZ100": {"url": "https://gz100-sgp.lol.qq.com:21019",      "name": "联盟二区"},
    "TENCENT_CQ100": {"url": "https://cq100-sgp.lol.qq.com:21019",      "name": "联盟三区"},
    "TENCENT_BGP2":  {"url": "https://bgp2-k8s-sgp.lol.qq.com:21019",   "name": "峡谷之巅"},
}

# ─── 命令行参数正则 ─────────────────────────────────────────────

PORT_RE             = re.compile(r'--app-port=(\d+)')
AUTH_RE             = re.compile(r'--remoting-auth-token=([\w-]+)')
PID_RE              = re.compile(r'--app-pid=(\d+)')
RSO_PLATFORM_ID_RE  = re.compile(r'--rso_platform_id=([\w-]+)')
RIOT_CLIENT_PORT_RE = re.compile(r'--riotclient-app-port=(\d+)')
RIOT_CLIENT_AUTH_RE = re.compile(r'--riotclient-auth-token=([\w-]+)')


# ═══════════════════════════════════════════════════════════════
# 第一步：通过 psutil 发现 LCU 连接信息
# ═══════════════════════════════════════════════════════════════

def psutil_find_lcu() -> Optional[dict]:
    """
    用 psutil 查 LeagueClientUx.exe 进程的命令行，解析出端口 + 密码。
    
    腾讯版 LCU：
    - LCU API 在 LeagueClientUx.exe 上（不是 LeagueClient.exe）
    - 命令行参数里有 --app-port=58149 和 --remoting-auth-token=xxx
    - WMIC 读不到命令行（被 TP 屏蔽），但 psutil 可以
    """
    try:
        import psutil
    except ImportError:
        logger.error("需要 psutil 库：pip install psutil")
        return None

    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = proc.info["name"]
            if name != "LeagueClientUx.exe":
                continue

            cmdline = proc.cmdline()
            cmdstr = " ".join(cmdline)

            port_m = PORT_RE.search(cmdstr)
            auth_m = AUTH_RE.search(cmdstr)
            pid_m  = PID_RE.search(cmdstr)

            if port_m and auth_m:
                info = {
                    "port": int(port_m.group(1)),
                    "password": auth_m.group(1),
                    "pid": int(pid_m.group(1)) if pid_m else proc.info["pid"],
                    "protocol": "https",
                }
                # 可选字段
                if rso := RSO_PLATFORM_ID_RE.search(cmdstr):
                    info["rso_platform_id"] = rso.group(1)
                if rc_port := RIOT_CLIENT_PORT_RE.search(cmdstr):
                    info["riotclient_port"] = int(rc_port.group(1))
                if rc_auth := RIOT_CLIENT_AUTH_RE.search(cmdstr):
                    info["riotclient_auth"] = rc_auth.group(1)

                logger.info(
                    f"🔍 psutil 发现 LCU (LeagueClientUx PID={proc.info['pid']}) → "
                    f"端口 {info['port']}, "
                    f"区服 {info.get('rso_platform_id', '?')}"
                )
                return info
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        except Exception as e:
            logger.debug(f"读取进程命令行异常: {e}")
            continue

    logger.warning("未找到 LeagueClientUx.exe 进程，请确保游戏正在运行")
    return None


# 备用方案：读 lockfile（原逻辑保留，覆盖更多路径）
def find_lcu_by_lockfile() -> Optional[dict]:
    """遍历常见路径读 lockfile"""
    common = [
        Path("D:/Program Files/英雄联盟(26)/LeagueClient"),
        Path("D:/Program Files/英雄联盟/LeagueClient"),
        Path("C:/Program Files/英雄联盟(26)/LeagueClient"),
        Path("C:/Program Files/英雄联盟/LeagueClient"),
        Path("D:/腾讯游戏/英雄联盟/LeagueClient"),
        Path("C:/Program Files/Riot Games/League of Legends"),
    ]
    for base in common:
        lf = base / "lockfile"
        if lf.exists() and lf.stat().st_size > 0:
            try:
                parts = lf.read_text().strip().split(":")
                if len(parts) >= 5:
                    logger.info(f"📄 lockfile 发现 LCU → 端口 {parts[2]}")
                    return {
                        "port": int(parts[2]),
                        "password": parts[3],
                        "pid": int(parts[1]),
                        "protocol": parts[4],
                    }
            except Exception:
                continue
    return None


# ═══════════════════════════════════════════════════════════════
# 第二步：腾讯 SGP API 客户端（直接调 lol.qq.com）
# ═══════════════════════════════════════════════════════════════

class SgpApi:
    """
    腾讯 SGP API 客户端。

    不需要 LCU 代理，拿着 entitlementsToken 直接调腾讯服务器。
    不走 Riot Match-V5，不会被 403 拦截。
    """

    USER_AGENT = "LeagueOfLegendsClient/14.13.596.7996 (rcp-be-lol-match-history)"
    TIMEOUT = 15

    def __init__(self):
        self._token: Optional[str] = None
        self._server_id: Optional[str] = None  # 如 "TENCENT_HN1"
        self._sub_id: Optional[str] = None     # 如 "HN1"

    @property
    def ready(self) -> bool:
        return bool(self._token and self._server_id)

    def setup(self, token: str, rso_platform_id: str):
        """配置 SGP 客户端"""
        self._token = token
        # 映射 HN1 → TENCENT_HN1
        sgp_id = SGP_SERVER_IDS.get(rso_platform_id.upper())
        if sgp_id:
            self._server_id = sgp_id
            self._sub_id = rso_platform_id.upper()
            logger.info(f"🌐 SGP 就绪 → {SGP_SERVERS[sgp_id]['name']} ({sgp_id})")
        else:
            # 非腾讯服，直接用原始 ID
            self._server_id = rso_platform_id
            self._sub_id = rso_platform_id
            logger.info(f"🌐 SGP 就绪 → {rso_platform_id}")

    def _url(self) -> str:
        srv = SGP_SERVERS.get(self._server_id or "")
        if not srv:
            raise ValueError(f"未知 SGP 服务器: {self._server_id}")
        return srv["url"]

    # ── 对战历史列表 ──────────────────────────────────────────

    def get_match_history(self, puuid: str, start: int = 0, count: int = 20) -> Optional[list]:
        """获取对战历史列表，返回 [{json: {...}}, ...]"""
        if not self.ready:
            logger.error("SGP 未就绪（缺 token 或 server_id）")
            return None

        try:
            resp = requests.get(
                f"{self._url()}/match-history-query/v1/products/lol/player/{puuid}/SUMMARY",
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "User-Agent": self.USER_AGENT,
                },
                params={"startIndex": start, "count": count},
                timeout=self.TIMEOUT,
                verify=False,
            )
            if resp.status_code == 200:
                return resp.json().get("games", [])
            logger.warning(f"SGP 历史列表 [{resp.status_code}]: {resp.text[:150]}")
        except Exception as e:
            logger.error(f"SGP 历史列表异常: {e}")
        return None

    # ── 对局详情（含完整参与者数据） ───────────────────────────

    def get_game_details(self, game_id: int) -> Optional[dict]:
        """
        获取对局详情。
        使用 SUMMARY 端点（含完整参与者数据），返回 LCU 兼容格式。
        ⚠️ DETAILS 端点是 timeline 格式（帧数据），没有参与者统计！必须用 SUMMARY。
        """
        if not self.ready:
            return None

        try:
            resp = requests.get(
                f"{self._url()}/match-history-query/v1/products/lol/{self._sub_id}_{game_id}/SUMMARY",
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "User-Agent": self.USER_AGENT,
                },
                timeout=self.TIMEOUT,
                verify=False,
            )
            if resp.status_code == 200:
                return self._sgp_details_to_lcu(resp.json(), game_id)
            logger.warning(f"SGP 对局详情 [{resp.status_code}] game={game_id}: {resp.text[:150]}")
        except Exception as e:
            logger.error(f"SGP 对局详情异常 game={game_id}: {e}")
        return None

    # ── 格式转换（SGP → LCU，使 database.py 能直接处理） ──────

    def _sgp_details_to_lcu(self, sgp_data: dict, game_id: int) -> Optional[dict]:
        """
        将 SGP SUMMARY 响应转换为 LCU 兼容格式。
        SGP 用的字段名和 LCU/Riot API 不同，需要映射：

        SGP 字段                  → LCU stats 字段
        ─────────────────────────────────────────
        spell1Id / spell2Id       → summoner1Id / summoner2Id
        totalDamageShieldedOnTeammates → totalDamageShieldedToOthers
        totalTimeCCDealt          → timeCCingOthers
        """
        try:
            # SGP DETAILS 返回的 json 字段里有完整 game 数据
            game = sgp_data.get("json", sgp_data)
            participants = game.get("participants", [])
            teams = game.get("teams", [])

            # ── teamId → win 映射 ──
            team_win = {}
            for t in teams:
                team_win[t["teamId"]] = t.get("win", False)

            # ── 构建 participantIdentities ──
            participant_identities = []
            for p in participants:
                participant_identities.append({
                    "participantId": p["participantId"],
                    "player": {
                        "accountId": 0,
                        "currentAccountId": 0,
                        "currentPlatformId": game.get("platformId", ""),
                        "matchHistoryUri": "",
                        "platformId": game.get("platformId", ""),
                        "profileIcon": p.get("profileIcon", 0),
                        "puuid": p.get("puuid", ""),
                        "summonerId": p.get("summonerId", 0),
                        "summonerName": p.get("summonerName", ""),
                        "tagLine": p.get("riotIdTagline", ""),
                        "gameName": p.get("riotIdGameName", ""),
                    }
                })

            # ── 构建 participants（LCU stats 内嵌格式） ──
            lcu_participants = []
            for p in participants:
                tid = p.get("teamId", 100)
                p_win = team_win.get(tid, False)

                # 符文展开
                perks_flat = {}
                style_idx = 0
                for style in p.get("perks", {}).get("styles", []):
                    for sel in style.get("selections", []):
                        key = f"perk{style_idx}"
                        perks_flat[key] = sel.get("perk", 0)
                        perks_flat[f"{key}Var1"] = sel.get("var1", 0)
                        perks_flat[f"{key}Var2"] = sel.get("var2", 0)
                        perks_flat[f"{key}Var3"] = sel.get("var3", 0)
                        style_idx += 1

                lcu_participants.append({
                    "championId": p.get("championId", 0),
                    "championName": p.get("championName", ""),
                    "participantId": p["participantId"],
                    "spell1Id": p.get("spell1Id", 0),
                    "spell2Id": p.get("spell2Id", 0),
                    "teamId": tid,
                    "playerAugment1": p.get("playerAugment1", 0),
                    "playerAugment2": p.get("playerAugment2", 0),
                    "playerAugment3": p.get("playerAugment3", 0),
                    "playerAugment4": p.get("playerAugment4", 0),
                    "stats": {
                        "win": p_win,
                        "kills": p.get("kills", 0),
                        "deaths": p.get("deaths", 0),
                        "assists": p.get("assists", 0),
                        "championId": p.get("championId", 0),
                        "totalDamageDealtToChampions": p.get("totalDamageDealtToChampions", 0),
                        "totalDamageDealt": p.get("totalDamageDealt", 0),
                        "totalDamageTaken": p.get("totalDamageTaken", 0),
                        "physicalDamageDealtToChampions": p.get("physicalDamageDealtToChampions", 0),
                        "magicDamageDealtToChampions": p.get("magicDamageDealtToChampions", 0),
                        "trueDamageDealtToChampions": p.get("trueDamageDealtToChampions", 0),
                        "goldEarned": p.get("goldEarned", 0),
                        "totalMinionsKilled": p.get("totalMinionsKilled", 0),
                        "neutralMinionsKilled": p.get("neutralMinionsKilled", 0),
                        "totalHeal": p.get("totalHeal", 0),
                        "totalDamageShieldedToOthers": p.get("totalDamageShieldedOnTeammates", 0),
                        "wardsPlaced": p.get("wardsPlaced", 0),
                        "wardsKilled": p.get("wardsKilled", 0),
                        "visionScore": p.get("visionScore", 0),
                        "doubleKills": p.get("doubleKills", 0),
                        "tripleKills": p.get("tripleKills", 0),
                        "quadraKills": p.get("quadraKills", 0),
                        "pentaKills": p.get("pentaKills", 0),
                        "largestKillingSpree": p.get("largestKillingSpree", 0),
                        "timeCCingOthers": p.get("totalTimeCCDealt", 0),
                        # SGP 的 spell1Id/spell2Id 就是召唤师技能 ID（不是 summoner1Id）
                        "summoner1Id": p.get("spell1Id", 0),
                        "summoner2Id": p.get("spell2Id", 0),
                        "item0": p.get("item0", 0),
                        "item1": p.get("item1", 0),
                        "item2": p.get("item2", 0),
                        "item3": p.get("item3", 0),
                        "item4": p.get("item4", 0),
                        "item5": p.get("item5", 0),
                        "item6": p.get("item6", 0),
                        **perks_flat,
                    },
                })

            # ── 组装最终结果 ──
            return {
                "gameId": game.get("gameId", game_id),
                "queueId": game.get("queueId", 0),
                "gameMode": game.get("gameMode", ""),
                "gameType": game.get("gameType", ""),
                "mapId": game.get("mapId", 0),
                "gameCreation": game.get("gameCreation", 0),
                "gameDuration": game.get("gameDuration", 0),
                "gameVersion": game.get("gameVersion", ""),
                "platformId": game.get("platformId", ""),
                "participants": lcu_participants,
                "participantIdentities": participant_identities,
            }
        except Exception as e:
            logger.exception(f"SGP→LCU 格式转换失败 game={game_id}: {e}")
            return None


# ═══════════════════════════════════════════════════════════════
# 第三步：LCU 客户端（连接 + 拿 Token + 中转 API 请求）
# ═══════════════════════════════════════════════════════════════

class LCUClient:
    """
    英雄联盟客户端 API 客户端。

    连接流程：
    1. psutil 查 LeagueClientUx.exe 命令行 → 拿端口 + 密码
    2. 连接 LCU → 拿 entitlementsToken + leagueSessionToken
    3. 用 Token 走 SGP API（后续对战历史全走 SGP）
    """

    def __init__(self):
        self.credentials = None
        self.base_url = None
        self.auth_header = None

        # Tokens（用于 SGP API）
        self._entitlements_token = None
        self._league_session_token = None
        self._rso_platform_id = None

        # SGP API 实例
        self.sgp = SgpApi()

        # 缓存
        self._summoner_cache = None

    # ── 连接 ──────────────────────────────────────────────────

    def connect(self) -> bool:
        """连接 LCU，获取 tokens，初始化 SGP 客户端"""
        # 1. psutil 查找（主方案，腾讯版必用）
        creds = psutil_find_lcu()
        if not creds:
            # 2. lockfile 兜底（国际服用）
            creds = find_lcu_by_lockfile()

        if not creds:
            logger.warning("未找到英雄联盟客户端连接信息，请确保游戏正在运行")
            return False

        self.credentials = creds
        proto = creds.get("protocol", "https")
        self.base_url = f"{proto}://127.0.0.1:{creds['port']}"
        auth_str = f"riot:{creds['password']}"
        self.auth_header = {
            "Authorization": f"Basic {base64.b64encode(auth_str.encode()).decode()}"
        }
        logger.info(f"✅ 已连接 LCU (端口 {creds['port']})")

        # 3. 拿 tokens
        self._rso_platform_id = creds.get("rso_platform_id", "")
        self._fetch_tokens()

        # 4. 如果拿到了 entitlementsToken，配置 SGP
        if self._entitlements_token and self._rso_platform_id:
            self.sgp.setup(self._entitlements_token, self._rso_platform_id)
        elif self._entitlements_token:
            logger.warning(f"未检测到区服信息，SGP 不可用")
        else:
            logger.warning("未获取到 entitlementsToken，SGP 不可用")

        return True

    # ── 获取 Tokens ───────────────────────────────────────────

    def _fetch_tokens(self):
        """从 LCU 获取 entitlementsToken 和 leagueSessionToken"""
        try:
            data = self._request("GET", "/entitlements/v1/token")
            # ⚠️ accessToken 才是带 lol scope 的 OAuth token，token 字段权限不足
            if data and data.get("accessToken"):
                self._entitlements_token = data["accessToken"]
                logger.info("🔑 已获取 entitlementsToken (accessToken)")
        except Exception as e:
            logger.warning(f"获取 entitlementsToken 失败: {e}")

        try:
            data = self._request("GET", "/lol-league-session/v1/league-session-token")
            # ⚠️ 这接口返回的是纯 JSON 字符串 "xxx"，不是 {"token": "xxx"}
            if isinstance(data, str):
                self._league_session_token = data
                logger.info("🔑 已获取 leagueSessionToken (string)")
            elif data and isinstance(data, dict) and data.get("token"):
                self._league_session_token = data["token"]
                logger.info("🔑 已获取 leagueSessionToken")
        except Exception as e:
            logger.warning(f"获取 leagueSessionToken 失败: {e}")

    # ── 底层 HTTP 请求 ────────────────────────────────────────

    def _request(self, method: str, endpoint: str, **kwargs) -> Optional[dict]:
        """发请求到 LCU API"""
        if not self.base_url or not self.auth_header:
            raise ConnectionError("未连接到 LCU，请先调用 connect()")

        url = f"{self.base_url}{endpoint}"
        headers = {**self.auth_header, **kwargs.pop("headers", {})}

        try:
            resp = requests.request(
                method, url, headers=headers,
                verify=False, timeout=10, **kwargs,
            )
            if resp.status_code in (200, 201):
                return resp.json()
            elif resp.status_code == 204:
                return {}
            elif resp.status_code == 404:
                logger.warning(f"LCU 资源不存在: {endpoint}")
                return None
            else:
                logger.warning(f"LCU 请求 [{resp.status_code}]: {endpoint}")
                return None
        except requests.exceptions.ConnectionError:
            logger.error(f"连接 LCU 失败: {endpoint}")
            return None
        except Exception as e:
            logger.error(f"LCU 请求异常: {e}")
            return None

    # ── 召唤师信息 ────────────────────────────────────────────

    def get_current_summoner(self) -> Optional[dict]:
        """获取当前登录的召唤师信息"""
        if self._summoner_cache:
            return self._summoner_cache
        data = self._request("GET", "/lol-summoner/v1/current-summoner")
        if data:
            self._summoner_cache = data
        return data

    def get_summoner_name(self, summoner_id) -> Optional[str]:
        """按 summonerId 查询召唤师名字（新版 LCU 选人 session 不再返回 summonerName，
        需用 /lol-summoner/v1/summoners/{id} 补查）。失败返回 None。"""
        if not summoner_id:
            return None
        data = self._request("GET", f"/lol-summoner/v1/summoners/{summoner_id}")
        if isinstance(data, dict):
            return (data.get("displayName") or data.get("name")
                    or data.get("gameName") or None)
        return None

    def get_friends(self) -> List[Dict]:
        """
        获取好友列表（/lol-chat/v1/friends）。
        返回: [{puuid, game_name}]；失败返回 []。
        """
        try:
            data = self._request("GET", "/lol-chat/v1/friends")
            if not isinstance(data, list):
                return []
            friends = []
            for f in data:
                puuid = f.get("puuid")
                if not puuid:
                    continue
                friends.append({
                    "puuid": puuid,
                    "game_name": f.get("gameName") or f.get("name") or "",
                })
            return friends
        except Exception as e:
            logger.warning(f"获取好友列表失败: {e}")
            return []

    # ── 对战历史（SGP 优先 → LCU 回退） ─────────────────────────

    def get_champ_select_session(self) -> Optional[dict]:
        """
        获取当前选人阶段 session（/lol-champ-select/v1/session）。
        返回: 选人 session dict；不在选人阶段（404 等）返回 None；
              LCU 连接断开时抛出 ConnectionError（调用方据此重新连接）。
        """
        if not self.base_url or not self.auth_header:
            raise ConnectionError("未连接到 LCU，请先调用 connect()")
        url = f"{self.base_url}/lol-champ-select/v1/session"
        try:
            resp = requests.get(url, headers=self.auth_header, verify=False, timeout=5)
        except requests.exceptions.ConnectionError:
            raise ConnectionError("LCU 连接断开") from None
        except Exception as e:
            logger.warning(f"查询选人 session 异常: {e}")
            return None
        if resp.status_code == 200:
            return resp.json()
        return None  # 404 = 不在选人阶段

    def fetch_all_matches(self, puuid: str = None, max_games: int = 100, known_game_ids: set = None) -> list:
        """
        拉取对战历史。
        优先走 SGP API（腾讯直连），回退到 LCU 代理。
        只返回海克斯大乱斗的对局。
        known_game_ids: 本地已有的 game_id 集合。列表仍完整翻页（翻页开销极小），
                        但只返回未入库的新对局（增量拉取优化，跳过详情拉取）。
        """
        if not puuid:
            summoner = self.get_current_summoner()
            if not summoner:
                logger.error("无法获取召唤师信息")
                return []
            puuid = summoner.get("puuid")

        if self.sgp.ready:
            return self._fetch_via_sgp(puuid, max_games, known_game_ids)

        logger.info("SGP 不可用，尝试 LCU 代理")
        return self._fetch_via_lcu(puuid, max_games, known_game_ids)

    def _fetch_via_sgp(self, puuid: str, max_games: int, known_game_ids: set = None) -> list:
        """通过 SGP API 拉取"""
        all_games = []
        seen_ids = set()
        page_size = 20
        known_game_ids = known_game_ids or set()

        for offset in range(0, max_games, page_size):
            games = self.sgp.get_match_history(puuid, offset, page_size)
            if not games:
                break

            for g in games:
                game_json = g.get("json", g)
                gid = game_json.get("gameId")
                if not gid or gid in seen_ids:
                    continue
                seen_ids.add(gid)

                queue_id = game_json.get("queueId", 0)
                if queue_id not in MAYHEM_QUEUE_IDS:
                    continue
                # 已入库的对局不返回（增量）
                if gid in known_game_ids:
                    continue
                all_games.append(game_json)

            if len(games) < page_size:
                break

        logger.info(f"SGP 获取 {len(all_games)} 场新大乱斗")
        return all_games

    def _fetch_via_lcu(self, puuid: str, max_games: int, known_game_ids: set = None) -> list:
        """通过 LCU 代理拉取"""
        all_games = []
        seen_ids = set()
        page_size = 20
        known_game_ids = known_game_ids or set()

        for offset in range(0, max_games, page_size):
            games = self._lcu_match_history(puuid, offset, offset + page_size - 1)
            if not games:
                break

            for g in games:
                gid = g.get("gameId")
                if gid and gid not in seen_ids:
                    seen_ids.add(gid)
                    queue_id = g.get("queueId", 0)
                    if queue_id not in MAYHEM_QUEUE_IDS:
                        continue
                    # 已入库的对局不返回（增量）
                    if gid in known_game_ids:
                        continue
                    all_games.append(g)

            if len(games) < page_size:
                break

        logger.info(f"LCU 获取 {len(all_games)} 场新海克斯大乱斗")
        return all_games

    def _lcu_match_history(self, puuid: str, beg: int, end: int) -> list:
        """LCU 对战历史接口"""
        endpoint = f"/lol-match-history/v1/products/lol/{puuid}/matches?begIndex={beg}&endIndex={end}"
        data = self._request("GET", endpoint)
        if data:
            return data.get("games", {}).get("games", data.get("games", []))
        return []

    # ── 对局详情（SGP 优先 → LCU 回退） ─────────────────────────

    def fetch_game_details_batch(self, games: list) -> list:
        """
        批量获取对局详情。
        SGP 优先，LCU 回退。
        """
        detailed = []
        for game in games:
            gid = game.get("gameId")

            if self.sgp.ready:
                details = self.sgp.get_game_details(gid)
                if details:
                    detailed.append(details)
                    continue

            # LCU 回退
            details = self._request("GET", f"/lol-match-history/v1/games/{gid}")
            if details:
                detailed.append(details)
            else:
                detailed.append(game)

        return detailed


# ═══════════════════════════════════════════════════════════════
# 辅助工具
# ═══════════════════════════════════════════════════════════════

def wait_for_client(timeout: int = 120, interval: int = 3) -> Optional[LCUClient]:
    """等待英雄联盟客户端启动"""
    client = LCUClient()
    start = time.time()
    while time.time() - start < timeout:
        if client.connect():
            return client
        logger.info(f"⏳ 等待客户端启动... ({int(time.time() - start)}s)")
        time.sleep(interval)
    logger.error(f"❌ 等待客户端启动超时 ({timeout}s)")
    return None


# ═══════════════════════════════════════════════════════════════
# 独立测试
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    client = LCUClient()
    if not client.connect():
        print("❌ 无法连接")
        exit(1)

    summoner = client.get_current_summoner()
    if summoner:
        puuid = summoner.get("puuid")
        print(f"\n👤 召唤师: {summoner.get('displayName')} (PUUID: {puuid})")
        print(f"   游戏名: {summoner.get('gameName')}#{summoner.get('tagLine')}")
        print(f"   区服: {client._rso_platform_id}")
        print(f"   entitlementsToken: {'✅' if client._entitlements_token else '❌'}")
        print(f"   SGP 就绪: {'✅' if client.sgp.ready else '❌'}")

        # 测试 SGP 拉取对战历史
        matches = client.fetch_all_matches(puuid=puuid, max_games=20)
        if matches:
            print(f"\n📊 拉取到 {len(matches)} 场大乱斗:")
            for m in matches:
                qid = m.get("queueId", 0)
                if qid in MAYHEM_QUEUE_IDS:
                    qname = "海克斯大乱斗"
                elif qid == ARAM_QUEUE_ID:
                    qname = "极地大乱斗"
                else:
                    qname = f"队列({qid})"
                dur = m.get("gameDuration", 0)
                gid = m.get("gameId", "?")
                print(f"   GameID:{gid} {qname} {dur//60}分")

            # 测试获取第1场详情
            if client.sgp.ready:
                print(f"\n📋 测试获取第1场详情...")
                detail = client.sgp.get_game_details(matches[0]["gameId"])
                if detail:
                    parts = detail.get("participants", [])
                    print(f"   对局 {detail['gameId']}: {len(parts)} 名参与者")
                    for p in parts[:5]:
                        s = p["stats"]
                        print(f"   #{p['participantId']} champ={s.get('championId')} "
                              f"{s.get('kills')}/{s.get('deaths')}/{s.get('assists')} "
                              f"伤害={s.get('totalDamageDealtToChampions')}")
                else:
                    print("   ❌ 获取详情失败")
        else:
            print("⚠️ 没有找到大乱斗对局")
    else:
        print("❌ 无法获取召唤师信息")
