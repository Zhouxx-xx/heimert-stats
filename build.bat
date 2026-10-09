@echo off
chcp 65001 >nul
title 构建海克斯大乱斗分析器

echo ========================================
echo   🏗️  海克斯大乱斗分析器 - 打包构建
echo ========================================
echo.

:: 检查 Python
py -3 --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 Python3，请先安装 Python
    pause
    exit /b 1
)

:: 清理旧构建
echo [1/4] 清理旧构建文件...
if exist "..\dist" rmdir /s /q "..\dist"
if exist "build" rmdir /s /q "build"
echo      OK

echo [2/4] 检查 PyInstaller...
py -3 -m pip show pyinstaller >nul 2>&1
if %errorlevel% neq 0 (
    echo   正在安装 PyInstaller...
    py -3 -m pip install pyinstaller
)
echo      OK

echo [3/4] 开始打包，可能需要 1-2 分钟...
py -3 -m PyInstaller ^
    --onedir ^
    --console ^
    --add-data "templates;templates" ^
    --add-data "static;static" ^
    --name "海克斯大乱斗分析器" ^
    --distpath "../dist" ^
    --workpath "build" ^
    --noconfirm ^
    run.py

if %errorlevel% neq 0 (
    echo [错误] 打包失败！请检查上方错误信息
    pause
    exit /b 1
)
echo      OK

echo [4/4] 准备分发目录...
set DIST_DIR=..\dist\海克斯大乱斗分析器
if not exist "%DIST_DIR%\data" mkdir "%DIST_DIR%\data"

:: 复制使用说明
if exist "使用说明.txt" (
    copy /y "使用说明.txt" "%DIST_DIR%\使用说明.txt" >nul 2>&1
)
echo      OK

echo.
echo ========================================
echo   ✅ 打包完成！
echo.
echo   输出目录: %DIST_DIR%
echo   可执行文件: 海克斯大乱斗分析器.exe
echo.
echo   将此文件夹完整发给朋友即可使用
echo ========================================
echo.
pause
