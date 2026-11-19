@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
set "SRC_DIR=%~dp0"
set "INSTALL_DIR=%LOCALAPPDATA%\TimesheetSync"

echo ==========================================
echo   ติดตั้ง TimesheetSync
echo ==========================================
echo ปลายทาง: %INSTALL_DIR%
echo.

if not exist "%SRC_DIR%TimesheetSync.exe" (
  echo [ERROR] ไม่พบ TimesheetSync.exe ในโฟลเดอร์เดียวกับ install.bat นี้
  pause
  exit /b 1
)

if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"

echo กำลังคัดลอกไฟล์โปรแกรม...
copy /Y "%SRC_DIR%TimesheetSync.exe" "%INSTALL_DIR%\" >nul
if exist "%SRC_DIR%TimesheetSyncCLI.exe" copy /Y "%SRC_DIR%TimesheetSyncCLI.exe" "%INSTALL_DIR%\" >nul
if exist "%SRC_DIR%timesheet_template.xlsx" copy /Y "%SRC_DIR%timesheet_template.xlsx" "%INSTALL_DIR%\" >nul

if exist "%INSTALL_DIR%\config.yaml" (
  echo พบ config.yaml เดิมอยู่แล้ว - ไม่แตะต้อง
) else if exist "%SRC_DIR%config.yaml" (
  copy /Y "%SRC_DIR%config.yaml" "%INSTALL_DIR%\" >nul
) else if exist "%SRC_DIR%config.example.yaml" (
  copy /Y "%SRC_DIR%config.example.yaml" "%INSTALL_DIR%\config.example.yaml" >nul
)

echo กำลังสร้าง shortcut ที่ Desktop และ Start Menu...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $target = Join-Path $env:LOCALAPPDATA 'TimesheetSync\TimesheetSync.exe'; $d = $ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'TimesheetSync.lnk')); $d.TargetPath = $target; $d.WorkingDirectory = Split-Path $target; $d.Save(); $sdir = Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs'; $s = $ws.CreateShortcut((Join-Path $sdir 'TimesheetSync.lnk')); $s.TargetPath = $target; $s.WorkingDirectory = Split-Path $target; $s.Save()"

echo.
echo ==========================================
echo   ติดตั้งเสร็จแล้ว
echo ==========================================
echo เปิดโปรแกรมได้จาก Desktop หรือ Start Menu: TimesheetSync
echo (ไม่ต้องสิทธิ์ Admin - ติดตั้งเฉพาะผู้ใช้นี้ที่ %INSTALL_DIR%)

if not exist "%INSTALL_DIR%\config.yaml" (
  echo.
  echo [สำคัญ] ยังไม่มี config.yaml ใช้งานจริงไม่ได้จนกว่าจะตั้งค่า
  echo ไปที่ %INSTALL_DIR% แล้วคัดลอก config.example.yaml เป็น config.yaml
  echo แล้วกรอก openproject.url / api_key / activity_mapping ก่อนใช้งาน
)
echo.
pause
