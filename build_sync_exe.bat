@echo off
setlocal
cd /d %~dp0

if not exist .venv (
  echo Creating virtual environment...
  python -m venv .venv
)

call .venv\Scripts\activate.bat
pip install --quiet -r requirements.txt
pip install --quiet pyinstaller

pyinstaller --onefile --noconfirm --name TimesheetSyncCLI --icon "assets\icon.ico" sync_timesheet.py

echo.
echo Build complete: dist\TimesheetSyncCLI.exe
echo ใช้กับ Windows Task Scheduler ได้เลย เช่น:
echo   TimesheetSyncCLI.exe --file "C:\path\to\timesheet.xlsx"
echo ต้องมี config.yaml อยู่ข้าง .exe (หรือระบุ --config เอง)
pause
