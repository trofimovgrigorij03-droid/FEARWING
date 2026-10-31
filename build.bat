@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
py -m PyInstaller --noconfirm --clean --onefile --windowed --name PORT_BOT_CHILIAD port_bot.py
if errorlevel 1 goto error
copy /Y config.json dist\config.json >nul
copy /Y README_RU.txt dist\README_RU.txt >nul
echo.
echo Готово: dist\PORT_BOT_CHILIAD.exe
echo Рядом с EXE должен лежать config.json.
pause
exit /b 0
:error
echo Ошибка сборки. Проверьте сообщения выше.
pause
exit /b 1
