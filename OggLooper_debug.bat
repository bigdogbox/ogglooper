@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo  OggLooper diagnostic
echo ============================================
echo.
echo [1] where pythonw
where pythonw
echo.
echo [2] where python
where python
echo.
echo [3] python --version
python --version
echo.
echo [4] tkinter check
python -c "import tkinter; print('tkinter OK')"
echo.
echo [5] launch app with console (errors shown below)
echo --------------------------------------------
python "%~dp0ogglooper.py"
echo --------------------------------------------
echo exit code: %ERRORLEVEL%
echo.
echo Copy everything above when reporting an issue.
pause
