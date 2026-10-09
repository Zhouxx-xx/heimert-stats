"""
发布工具：把新的分发版 zip 上传到云端 app_updates 公告表（BLOB 方案）。

用法:
    py -3 upload_update.py <zip路径> <版本号> [标题] [说明]
    py -3 upload_update.py <zip路径> <版本号> [标题] @说明文件.txt

示例:
    py -3 upload_update.py "..\\dist\\小熊海克斯复盘-分发版.zip" 1.2.0 "新增好友筛选" "@notes.txt"
    py -3 upload_update.py "..\\dist\\小熊海克斯复盘-分发版.zip" 1.2.0 "新增好友筛选" "1. 玩家排行可只看好友\\n2. 英雄排行支持加载更多"

注意:
    1. 上传前请确认 app.py 里的 CURRENT_VERSION 已改成 <版本号>，
       否则用户端永远收不到提示（云端版本必须 > 用户本地版本）。
    2. zip 不要太大（几十 MB 内），下载走云端数据库外网带宽。
    3. 本脚本只给发布者自己用，不要打进分发包。
"""

import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cloud_db import get_cloud, ensure_update_table  # noqa: E402


def read_app_current_version() -> str:
    """读取 app.py 里的 CURRENT_VERSION 常量（用于发布前校验）"""
    app_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")
    try:
        with open(app_py, encoding="utf-8") as f:
            m = re.search(r'CURRENT_VERSION\s*=\s*"([^"]+)"', f.read())
        return m.group(1) if m else ""
    except Exception:
        return ""


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    zip_path = sys.argv[1]
    version = sys.argv[2]
    title = sys.argv[3] if len(sys.argv) > 3 else ""
    notes_arg = sys.argv[4] if len(sys.argv) > 4 else ""

    # 说明：支持 @文件 或 内联文本（\n 转义）
    if notes_arg.startswith("@"):
        notes_file = notes_arg[1:]
        if not os.path.isfile(notes_file):
            print(f"错误: 找不到说明文件 {notes_file}")
            sys.exit(1)
        with open(notes_file, encoding="utf-8") as f:
            notes = f.read().strip()
    else:
        notes = notes_arg.replace("\\n", "\n")

    if not os.path.isfile(zip_path):
        print(f"错误: 找不到文件 {zip_path}")
        sys.exit(1)

    with open(zip_path, "rb") as f:
        zip_data = f.read()
    zip_name = os.path.basename(zip_path)
    size_mb = len(zip_data) / 1024 / 1024

    print(f"上传: {zip_name} ({size_mb:.1f} MB), 版本 {version}")
    if title:
        print(f"标题: {title}")
    if notes:
        print(f"说明:\n{notes}")

    # 版本一致性校验（只是提醒，不强制）
    app_version = read_app_current_version()
    if app_version and app_version != version:
        print(f"\n⚠️  警告: app.py 里 CURRENT_VERSION = \"{app_version}\"，"
              f"与本次上传版本 {version} 不一致！")
        print("   用户端会提示新版本的条件是: 云端版本 > 用户本地 CURRENT_VERSION")
        print("   请确认 app.py 的 CURRENT_VERSION 已改成本次发布版本后重新打包分发。")
    elif not app_version:
        print("\n⚠️  警告: 无法读取 app.py 的 CURRENT_VERSION，跳过一致性校验。")

    if not ensure_update_table():
        print("错误: 云端数据库连接失败或建表失败，请检查网络/凭据")
        sys.exit(1)

    try:
        with get_cloud() as conn:
            if conn is None:
                print("错误: 云端数据库连接失败")
                sys.exit(1)
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO app_updates (version, title, notes, zip_data, zip_name)
                    VALUES (%s, %s, %s, %s, %s)
                """, (version, title, notes, zip_data, zip_name))
                new_id = cur.lastrowid
    except Exception as e:
        print(f"错误: 插入失败: {e}")
        print("提示: 如果报 max_allowed_packet 相关错误，请调大云端实例的 max_allowed_packet")
        sys.exit(1)

    print(f"\n✅ 发布成功！公告 id={new_id}, 版本 {version}")
    print("用户端下次启动会自动提示下载该版本。")


if __name__ == "__main__":
    main()
