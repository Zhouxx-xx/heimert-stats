"""
装备 ID → 中文名映射
数据来源: https://ddragon.leagueoflegends.com/cdn/14.24.1/data/zh_CN/item.json
运行时下载，下载失败时使用内置的常用装备表作为兜底。
"""

import logging
import os
import sys
import json
import urllib.request
import re

logger = logging.getLogger(__name__)

# ---------- Data Dragon 下载 ----------

DD_VER = "14.24.1"  # 应与 game_version 匹配
ITEM_URL = f"https://ddragon.leagueoflegends.com/cdn/{DD_VER}/data/zh_CN/item.json"
if getattr(sys, 'frozen', False):
    _BASE_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(_BASE_DIR, ".item_cache.json")

_ITEM_MAP: dict[int, str] = {}
_loaded = False


def _download_item_map() -> dict:
    """从 Data Dragon 下载装备数据"""
    try:
        req = urllib.request.Request(ITEM_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        items = {}
        for iid, info in data.get("data", {}).items():
            items[int(iid)] = info.get("name", f"装备{iid}")
        # 缓存到本地
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False)
        except Exception:
            pass
        logger.info(f"从 Data Dragon 下载 {len(items)} 件装备")
        return items
    except Exception as e:
        logger.warning(f"下载装备数据失败: {e}")
        return {}


def _load_cached() -> dict:
    """加载本地缓存"""
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _init():
    global _ITEM_MAP, _loaded
    if _loaded:
        return
    _loaded = True
    _ITEM_MAP = _download_item_map() or _load_cached() or _builtin_map()


def _builtin_map() -> dict:
    """兜底：常用装备 → 中文名"""
    return {
        # 鞋子
        1001: "鞋子",
        3006: "狂战士胫甲",
        3009: "轻灵之靴",
        3011: "法师之靴",
        3020: "水银之靴",
        3047: "铁板靴",
        3111: "明朗之靴",
        3158: "符文附魔·秒表",
        # 坦克/战士
        3053: "斯特拉克的挑战护手",
        3065: "振奋盔甲",
        3026: "冰霜之心",
        3083: "荆刺背心",
        3082: "亡者的板甲",
        3143: "兰顿之兆",
        3110: "冰川之心",
        3742: "巨像的勇气",
        3748: "深渊面具",
        4003: "生命药水",
        4401: "爆破鬼才炸药包",
        # AD
        3031: "无尽之刃",
        3036: "最后的轻语",
        3033: "多米尼克领主的致意",
        3153: "破败王者之刃",
        3072: "贪欲九头蛇",
        3074: "巨型九头蛇",
        3124: "魔切",
        3508: "朔极之矛",
        3139: "纳沃利迅刃",
        3094: "幽梦之灵",
        3142: "凡性的提醒",
        6670: "收集者",
        6672: "海妖杀手",
        6673: "幻影之舞",
        6692: "天赋：传说：欢欣",
        6693: "天赋：传说：坚韧",
        6694: "天赋：传说：血统",
        6695: "天赋：传说：急速",
        6696: "天赋：传说：护盾",
        6698: "天赋：传说：韧",
        6699: "天赋：传说：急速",
        # AP
        1052: "增幅典籍",
        1058: "无用大棒",
        1026: "爆裂魔杖",
        3137: "虚空之杖",
        3089: "灭世者的死亡之帽",
        3165: "莫雷洛秘典",
        3115: "世界之柱",
        3157: "中娅沙漏",
        3100: "卢登的激荡",
        3102: "女妖面纱",
        3027: "冰霜之心",
        4642: "时光之杖",
        4641: "炽天使之拥",
        4643: "永恒之刃",
        4644: "暗夜收割者",
        4645: "魔女之刃",
        4646: "巫妖之祸",
        4629: "恶魔之拥",
        4632: "峡谷制造者",
        4630: "海克斯科技火箭腰带",
        4633: "海克斯科技枪刃",
        4634: "海克斯科技原型腰带",
        4635: "海克斯科技射线枪",
        4636: "海克斯科技射线枪",
        # 辅助
        2301: "辅助装",
        3097: "钢铁护肩",
        3117: "攻击辅助装",
        3222: "米凯尔的坩埚",
        3190: "基克的聚合",
        3107: "炽热香炉",
        3105: "流水法杖",
        3114: "禁忌之像",
        3113: "和谐之圣杯",
        3905: "大天使之杖",
        # 防御
        3010: "催化神石",
        3024: "冰川护甲",
        3057: "燃烧宝石",
        3067: "净蚀",
        3068: "日炎圣盾",
        3070: "耀光",
        3071: "三相之力",
        3078: "黑切",
        3085: "守望者铠甲",
        3112: "护甲鞋",
        3121: "守护天使",
        3140: "布甲鞋",
        3155: "魔抗鞋",
        3181: "碎片之盾",
        3193: "高天螺旋",
        3745: "石像鬼石板甲",
        3812: "死亡之舞",
        4403: "破舰者",
        6630: "巨蛇之牙",
        6631: "赛瑞尔达的怨恨",
        6632: "暗行者之爪",
        # 基础物品
        2003: "生命药水",
        2009: "总攻击力药水",
        2010: "总魔法药水",
        2031: "复合药剂",
        2033: "腐败药水",
        2055: "控制守卫",
        2138: "活力药水",
        2139: "钢铁合剂",
        2140: "愤怒合剂",
        2141: "智慧合剂",
        3340: "监视图腾",
        3341: "扫描透镜",
        3363: "远见改造",
        3364: "神谕透镜",
        3401: "圣物之盾",
        # 普通
        1011: "巨人腰带",
        1028: "幽魂斗篷",
        1029: "锁子甲",
        1031: "棘刺背心",
        1033: "暴风之剑",
        1035: "大腰带",
        1036: "长剑",
        1037: "暴击手套",
        1038: "暴风大剑",
        1039: "法术用书",
        1040: "反曲之弓",
        1041: "短刃",
        1042: "匕首",
        1043: "短剑",
        1053: "吸血鬼节杖",
        1054: "多兰之盾",
        1055: "多兰之刃",
        1056: "多兰之戒",
        1057: "负极斗篷",
        1058: "无用大棒",
        1062: "死刑宣告",
        1082: "黑暗封印",
        1083: "诅咒之刃",
        2031: "复合药剂",
        2033: "腐败药水",
        # 神话/传说
        4004: "狂徒铠甲",
        4005: "帝国指令",
        4007: "星界驱驰",
        6035: "致命节奏",
        6333: "死亡之舞",
        6610: "挺进破坏者",
        6616: "峡谷制造者",
        6617: "暗夜收割者",
        6620: "黑洞",
        6621: "虚空之女",
        6691: "卢安娜的飓风",
        # 警用物品
        2051: "守护者印章",
        2052: "守护者号角",
        2053: "守护者战锤",
        2054: "守护者法杖",
        2055: "守护者之盾",
        2056: "守护者之刃",
        2057: "守护者之弓",
        2058: "守护者药水",
        # 奥恩升级
        4001: "生命药水",
    }


def get_item_name(item_id: int) -> str:
    """获取装备中文名"""
    _init()
    if item_id is None or item_id == 0:
        return ""
    name = _ITEM_MAP.get(item_id)
    if name:
        return name
    return f"#{item_id}"


def get_item_build(items: list) -> list:
    """
    获取装备列表（按顺序，去掉空/0）。
    items: [item0, item1, item2, item3, item4, item5, item6]
    return: [(item_id, name), ...]
    """
    result = []
    for iid in items:
        if iid and iid > 0:
            # 跳过守卫物品 (3340, 3341, 3363, 3364) 和饰品
            if iid in (3340, 3341, 3363, 3364, 2055):
                continue
            result.append((iid, get_item_name(iid)))
    return result
