@echo off
chcp 65001 >nul
setlocal
set "INSTALL_DIR=%LOCALAPPDATA%\TimesheetSync"

echo จะลบ TimesheetSync ออกจากเครื่องนี้ (%INSTALL_DIR%)
echo รวมถึง config.yaml - สำรองไว้ก่อนถ้ายังต้องใช้
set /p CONFIRM="พิมพ์ Y แล้ว Enter เพื่อยืนยัน: "
if /i not "%CONFIRM%"=="Y" (
  echo ยกเลิกการถอนการติดตั้ง
  pause
  exit /b 0
)

echo กำลังลบ shortcut...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path ([Environment]::GetFolderPath('Desktop')) 'TimesheetSync.lnk'); $sdir = Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs'; Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $sdir 'TimesheetSync.lnk')"

echo กำลังลบไฟล์โปรแกรม...
if exist "%INSTALL_DIR%" rmdir /s /q "%INSTALL_DIR%"

echo ถอนการติดตั้งเสร็จแล้ว
pause
