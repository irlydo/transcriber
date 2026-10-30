; Inno Setup script for the Transcriber installer. Run via tools/build.py, which passes AppVersion.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName "Transcriber"
#define AppExe "Transcriber.exe"

[Setup]
; Keep this AppId forever: it's how upgrades find the existing install.
AppId={{13DB05FE-6FD4-470B-B22D-0010B63AE2A5}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Installs for the current user without admin rights; the user can still pick "all users".
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist
OutputBaseFilename=Transcriber-Setup-{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
ShowLanguageDialog=auto
; Setup's RedirectionGuard is inherited by the app it launches (after installing or updating), which then
; can't follow the symlinks in the Hugging Face model cache: "[WinError 448] ... untrusted mount point".
RedirectionGuard=no

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "de"; MessagesFile: "compiler:Languages\German.isl"
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"
Name: "it"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "nl"; MessagesFile: "compiler:Languages\Dutch.isl"
Name: "pl"; MessagesFile: "compiler:Languages\Polish.isl"
Name: "pt"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "ja"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "ko"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "zh"; MessagesFile: "ChineseSimplified.isl"

[CustomMessages]
en.RemoveData=Also delete downloaded speech models and GPU support? They can use several GB of disk space.%n%nChoose No to keep them for a later reinstall.
de.RemoveData=Auch die heruntergeladenen Sprachmodelle und die GPU-Unterstützung löschen? Sie können mehrere GB Speicherplatz belegen.%n%nWähle „Nein“, um sie für eine spätere Neuinstallation zu behalten.
es.RemoveData=¿Eliminar también los modelos de voz descargados y el soporte de GPU? Pueden ocupar varios GB.%n%nElige No para conservarlos para una futura reinstalación.
fr.RemoveData=Supprimer aussi les modèles vocaux téléchargés et le support GPU ? Ils peuvent occuper plusieurs Go.%n%nChoisissez Non pour les conserver en vue d'une réinstallation.
it.RemoveData=Eliminare anche i modelli vocali scaricati e il supporto GPU? Possono occupare diversi GB.%n%nScegli No per conservarli per una futura reinstallazione.
nl.RemoveData=Ook de gedownloade spraakmodellen en GPU-ondersteuning verwijderen? Die kunnen meerdere GB innemen.%n%nKies Nee om ze te bewaren voor een latere herinstallatie.
pl.RemoveData=Usunąć też pobrane modele mowy i obsługę GPU? Mogą zajmować kilka GB.%n%nWybierz Nie, aby zachować je na wypadek ponownej instalacji.
pt.RemoveData=Excluir também os modelos de fala baixados e o suporte a GPU? Eles podem ocupar vários GB.%n%nEscolha Não para mantê-los para uma reinstalação futura.
ja.RemoveData=ダウンロードした音声モデルと GPU サポートも削除しますか？数 GB の容量を使用している場合があります。%n%n再インストールに備えて残す場合は「いいえ」を選んでください。
ko.RemoveData=다운로드한 음성 모델과 GPU 지원도 삭제할까요? 몇 GB의 공간을 차지할 수 있습니다.%n%n나중에 다시 설치할 때 사용하려면 [아니요]를 선택하세요.
zh.RemoveData=是否同时删除已下载的语音模型和 GPU 支持？它们可能占用数 GB 磁盘空间。%n%n选择“否”可保留它们，以便日后重新安装。

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\Transcriber\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Clear out the previous version's files so upgrades don't leave stale libraries behind.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
; Automatic updates run the installer silently (see transcriber/updater.py): start the new version afterwards.
Filename: "{app}\{#AppExe}"; Flags: nowait runasoriginaluser; Check: WizardSilent

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  // Models and GPU support live in %LOCALAPPDATA%\Transcriber, outside the install folder.
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
    if MsgBox(CustomMessage('RemoveData'), mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
    begin
      DelTree(ExpandConstant('{localappdata}\Transcriber'), True, True, True);
      DelTree(ExpandConstant('{userappdata}\Transcriber'), True, True, True);
    end;
end;
