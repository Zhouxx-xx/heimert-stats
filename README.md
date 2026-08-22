# 海克斯大乱斗复盘分析器 Heimert Stats

> 英雄联盟「海克斯大乱斗」模式的桌面端数据复盘工具 —— 实时对局面板 · 伤害复盘 · 英雄数据统计 · 组队分路评分

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)]() [![Flask](https://img.shields.io/badge/Flask-Web仪表盘-green.svg)]() [![License](https://img.shields.io/badge/License-MIT-yellow.svg)]()

## ✨ 功能特性

- **实时对局面板** —— 对局进行中自动识别己方/敌方阵容，实时展示对局数据
- **伤害复盘** —— 对局结束后逐时间段回放伤害曲线，定位每一波团战的输出/承伤细节
- **英雄统计分析** —— 按英雄聚合场次、胜率、场均伤害等核心指标
- **复盘评分系统** —— 多场次组合复盘，自动计算玩家评分与团队贡献排名
- **组队分路管理** —— 支持多局组队分路记录、场次合并、队伍数据对比
- **本地优先 + 可选云同步** —— SQLite 本地存储保障离线可用，支持云端数据库同步备份
- **深浅色主题 / 中文英雄装备数据** —— 全中文界面，内置英雄、装备、海克斯强化符文汉化映射

## 🏗️ 技术架构

```
┌─────────────────────────────────────────────┐
│              Flask Web 仪表盘 (app.py)        │
│   实时面板 / 伤害复盘 / 英雄统计 / 复盘评分     │
├─────────────────────────────────────────────┤
│        LCU / SGP API 客户端 (lcu_client.py)   │
│  自动发现本地客户端端口 → 直连腾讯 SGP 服务器   │
├─────────────────────────────────────────────┤
│   数据层：SQLite 本地库 + 云端同步 (cloud_db)  │
│   分析层：analyzer.py / reviews.py            │
└─────────────────────────────────────────────┘
```

**技术要点：**

- 通过读取客户端进程启动参数自动获取 LCU 接口凭证，实现全自动无感连接
- 逆向分析国服客户端的 SGP（腾讯游戏服务平台）接口调用方式，绕开官方 Match-V5 接口对国服的 403 限制，直接拉取完整对局数据
- 前后端数据全部本地化处理，不依赖任何第三方数据服务

## 📦 技术栈

`Python 3.10+` · `Flask` · `SQLite` · `requests` · `PyInstaller 打包分发` · `HTML/CSS/JS`

## 🚀 快速开始

```bash
# 1. 克隆仓库
git clone https://github.com/Zhouxx-xx/heimert-stats.git
cd heimert-stats

# 2. 安装依赖
pip install flask requests

# 3. 启动（保持英雄联盟客户端处于登录状态）
python run.py
```

浏览器访问 `http://127.0.0.1:5000` 即可使用。

## 📁 目录结构

```
heimert-stats/
├── app.py              # Flask 主应用（仪表盘/复盘/评分路由）
├── run.py              # 启动入口
├── lcu_client.py       # LCU/SGP API 客户端
├── analyzer.py         # 伤害数据分析引擎
├── database.py         # SQLite 本地数据层
├── cloud_db.py         # 云端数据库同步
├── reviews.py          # 复盘评分系统
├── champion_cn.py      # 英雄中文名映射
├── item_cn.py          # 装备中文名映射
├── augment_cn.py       # 海克斯强化符文映射
├── templates/          # 前端页面
└── static/             # 样式与资源
```

## 📄 说明

- 本项目仅供个人学习与技术交流，数据均来自玩家本人客户端本地接口
- 与 Riot Games / 腾讯无任何关联

## 📜 License

MIT License
