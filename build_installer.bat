@echo off
setlocal
cd /d %~dp0

where iscc >nul 2>nul
if %errorlevel% neq 0 (
  if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" (
    set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
  ) else if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" (
    set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
  ) else (
    echo [ERROR] ไม่พบ Inno Setup Compiler ^(ISCC.exe^)
    echo ติดตั้งก่อนด้วย: winget install JRSoftware.InnoSetup
    echo หรือดาวน์โหลดจาก https://jrsoftware.org/isdl.php
    pause
    exit /b 1
  )
) else (
  set "ISCC=iscc"
)

if not exist "dist\TimesheetSync.exe" (
  echo [ERROR] ไม่พบ dist\TimesheetSync.exe — รัน build_exe.bat ก่อน
  pause
  exit /b 1
)
if not exist "dist\TimesheetSyncCLI.exe" (
  echo [ERROR] ไม่พบ dist\TimesheetSyncCLI.exe — รัน build_sync_exe.bat ก่อน
  pause
  exit /b 1
)

rem เลขเวอร์ชันมีแหล่งเดียวคือ version.py — ส่งเข้า ISCC แทนการแก้เลขใน installer.iss ให้ตรงกันเอง
rem อ่านผ่านไฟล์ชั่วคราวแทน for /f เพราะ backtick ของ for /f ตีความเครื่องหมายคำพูดซ้อนใน -c ไม่ถูก
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
set "VERFILE=%TEMP%\timesheetsync-version.txt"
%PY% -c "from version import APP_VERSION; print(APP_VERSION)" > "%VERFILE%"
set /p APPVER=<"%VERFILE%"
del "%VERFILE%" >nul 2>nul

if not defined APPVER (
  echo [ERROR] อ่านเลขเวอร์ชันจาก version.py ไม่สำเร็จ
  pause
  exit /b 1
)

echo Building installer version %APPVER% ...
"%ISCC%" /DMyAppVersion=%APPVER% installer.iss
if %errorlevel% neq 0 (
  echo [ERROR] ISCC ล้มเหลว
  pause
  exit /b 1
)

echo.
echo Build complete: installer_output\TimesheetSync-Setup-%APPVER%.exe
echo ไฟล์เดียวจบ แจกให้ใครก็เปิดติดตั้งได้ทันที ไม่ต้องใช้สิทธิ์ Admin
pause
