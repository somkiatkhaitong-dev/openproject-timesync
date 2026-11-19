#define MyAppName "TimesheetSync"
; build_installer.bat อ่านเลขจาก version.py แล้วส่งเข้ามาทาง /DMyAppVersion=... เพื่อไม่ให้
; เลขในแอปกับในตัวติดตั้งหลุดจากกัน — ค่าด้านล่างใช้เฉพาะตอนเรียก ISCC เองตรงๆ
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif
#define MyAppPublisher "Internal Tools"
#define MyAppExeName "TimesheetSync.exe"
#define MyAppCLIExeName "TimesheetSyncCLI.exe"

[Setup]
AppId={{B6C6F5C1-3E1A-4B8E-9C1D-7F2A6D9E1234}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=installer_output
; ใส่เลขเวอร์ชันในชื่อไฟล์ ตัวติดตั้งของแต่ละรุ่นจะได้ไม่ทับกันใน installer_output
; เก็บของเก่าไว้ถอยกลับได้ และคนรับไฟล์รู้ว่าถืออยู่รุ่นไหนตั้งแต่ยังไม่เปิด
OutputBaseFilename=TimesheetSync-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "สร้างไอคอนบน Desktop"; GroupDescription: "ทางลัดเพิ่มเติม:"

[Files]
Source: "dist\TimesheetSync.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\TimesheetSyncCLI.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "config.example.yaml"; DestDir: "{app}"; Flags: ignoreversion
; ไม่แพ็ก config.yaml ของเครื่อง dev ไปด้วย — ไฟล์นั้นมี last_file ชี้ไป path บนเครื่องเรา
; และการมีไฟล์อยู่ทำให้ผู้ใช้ใหม่ไม่ได้รับคำแนะนำหลังติดตั้ง แอปรองรับกรณีไม่มี config อยู่แล้ว
; (app.py ใช้ default_config() แล้วให้กรอกผ่านหน้า login)
Source: "timesheet_template.xlsx"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{#MyAppName} (CLI/Task Scheduler)"; Filename: "{app}\{#MyAppCLIExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; log ถูกสร้างหลังติดตั้ง installer จึงไม่รู้จัก ต้องสั่งลบเองตอนถอน
Type: filesandordirs; Name: "{app}\logs"

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  ConfigPath: String;
begin
  if (CurStep = ssPostInstall) and (not WizardSilent()) then
  begin
    ConfigPath := ExpandConstant('{app}\config.yaml');
    if not FileExists(ConfigPath) then
    begin
      MsgBox('ติดตั้งเสร็จแล้ว' + #13#10 + #13#10 +
        'เปิดโปรแกรมแล้วกรอก OpenProject URL กับ API key ที่หน้าเข้าสู่ระบบได้เลย ' +
        'ไม่ต้องแก้ไฟล์ตั้งค่าเอง (ติ๊ก "จำไว้สำหรับครั้งถัดไป" เพื่อไม่ต้องกรอกซ้ำ)' + #13#10 + #13#10 +
        'ถ้าเข้าสู่ระบบไม่ผ่าน ให้กดปุ่ม "ตรวจสอบระบบ" ที่หน้าเดียวกัน แล้วส่งผลให้ผู้ดูแล',
        mbInformation, MB_OK);
    end;
  end;
end;
