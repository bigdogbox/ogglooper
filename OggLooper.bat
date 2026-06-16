@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM Python があればアプリを起動
where pythonw >nul 2>nul
if %ERRORLEVEL%==0 (
    start "" pythonw "%~dp0ogglooper.py"
    goto :eof
)
where python >nul 2>nul
if %ERRORLEVEL%==0 (
    start "" python "%~dp0ogglooper.py"
    goto :eof
)

REM --- Python 未検出: 同意を取って winget でインストール ---
echo Pythonのインストールが必要です。wingetで公式版をインストールします。よろしいですか？
set /p "ANS=(y/N): "
if /i not "%ANS%"=="y" goto manual

where winget >nul 2>nul
if not %ERRORLEVEL%==0 goto nowinget

echo.
echo winget で Python をインストールしています...
winget install -e --id Python.Python.3.13 --scope user --accept-package-agreements --accept-source-agreements
echo.
echo インストールが完了しました。OggLooper.bat をもう一度起動してください。
echo (PATH を反映するため、いったん閉じて開き直す必要があります)
pause
goto :eof

:nowinget
echo.
echo winget が見つかりませんでした。お手数ですが手動でインストールしてください:
echo   https://www.python.org/downloads/windows/
echo インストール時に「Add python.exe to PATH」にチェックを入れてください。
pause
goto :eof

:manual
echo.
echo インストールをキャンセルしました。
echo 手動で入れる場合: https://www.python.org/downloads/windows/
echo インストール時に「Add python.exe to PATH」にチェックを入れてください。
pause
goto :eof
