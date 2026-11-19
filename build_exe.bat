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

pyinstaller --onefile --windowed --noconfirm --name TimesheetSync ^
  --icon "assets\icon.ico" ^
  --add-data "assets;assets" ^
  app.py

echo.
echo Build complete: dist\TimesheetSync.exe
echo แนะนำให้ก็อปปี้ config.example.yaml (เปลี่ยนชื่อเป็น config.yaml และกรอกข้อมูล)
echo กับ timesheet_template.xlsx ไปไว้ในโฟลเดอร์เดียวกับ TimesheetSync.exe ก่อนแจกจ่าย
pause
