Unicode true
ManifestSupportedOS all
RequestExecutionLevel user
SetCompressor /SOLID lzma
!include "MUI2.nsh"
!include "x64.nsh"
!include "WinVer.nsh"

!define APP "清除JPG HDR小工具"
!define UNINSTALLKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\ClearJPGHDR"
Name "${APP}"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\Programs\ClearJPGHDR"
Icon "${ICONFILE}"
UninstallIcon "${ICONFILE}"
BrandingText "${APP}"
VIProductVersion "${VERSION}.0"
VIAddVersionKey ProductName "${APP}"
VIAddVersionKey FileDescription "${APP} 安装程序"
VIAddVersionKey FileVersion "${VERSION}"
VIAddVersionKey LegalCopyright "Kikuku10"

!define MUI_ICON "${ICONFILE}"
!define MUI_UNICON "${ICONFILE}"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\ClearJPGHDR.exe"
!define MUI_FINISHPAGE_RUN_TEXT "打开设置"
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

Function .onInit
  ${IfNot} ${AtLeastWin10}
    MessageBox MB_OK|MB_ICONSTOP "需要 Windows 10 或更新版本。" /SD IDOK
    SetErrorLevel 1
    Abort
  ${EndIf}
  ${If} ${IsNativeARM64}
  ${AndIfNot} ${AtLeastWin11}
    MessageBox MB_OK|MB_ICONSTOP "ARM64 电脑需要 Windows 11 才能运行本工具的 x64 处理程序。" /SD IDOK
    SetErrorLevel 1
    Abort
  ${EndIf}
  ${If} ${IsNativeIA32}
    MessageBox MB_OK|MB_ICONSTOP "此安装包需要支持 x64 应用的 Windows。" /SD IDOK
    SetErrorLevel 1
    Abort
  ${EndIf}
FunctionEnd

Section
  SetShellVarContext current
  SetRegView 64
  SetOutPath "$INSTDIR"
  File /r "${PAYLOAD}/*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "${UNINSTALLKEY}" "DisplayName" "${APP}"
  WriteRegStr HKCU "${UNINSTALLKEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINSTALLKEY}" "Publisher" "Kikuku10"
  WriteRegStr HKCU "${UNINSTALLKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALLKEY}" "DisplayIcon" "$INSTDIR\ClearJPGHDR.exe"
  WriteRegStr HKCU "${UNINSTALLKEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${UNINSTALLKEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKCU "${UNINSTALLKEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALLKEY}" "NoRepair" 1
  CreateShortcut "$SMPROGRAMS\${APP}.lnk" "$INSTDIR\ClearJPGHDR.exe"
  ClearErrors
  ExecWait '"$INSTDIR\ClearJPGHDR.exe" --register' $0
  IfErrors registerError
  StrCmp $0 0 complete
registerError:
  MessageBox MB_OK|MB_ICONSTOP "无法完成资源管理器菜单设置，请重新运行安装程序。" /SD IDOK
  SetErrorLevel 1
  Abort
complete:
SectionEnd

Section "Uninstall"
  SetShellVarContext current
  SetRegView 64
  ReadRegStr $1 HKCU "${UNINSTALLKEY}" "InstallLocation"
  StrCmp $1 "$INSTDIR" expected
  MessageBox MB_OK|MB_ICONSTOP "此卸载程序不在已注册的安装位置。" /SD IDOK
  SetErrorLevel 1
  Abort
expected:
  ClearErrors
  ExecWait '"$INSTDIR\ClearJPGHDR.exe" --unregister' $0
  IfErrors unregisterError
  StrCmp $0 0 removeFiles
unregisterError:
  MessageBox MB_OK|MB_ICONSTOP "无法注销资源管理器菜单，卸载已停止。" /SD IDOK
  SetErrorLevel 1
  Abort
removeFiles:
  !include "${REMOVEFILES}"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  Delete "$SMPROGRAMS\${APP}.lnk"
  DeleteRegKey HKCU "${UNINSTALLKEY}"
  Goto complete
filesLocked:
  MessageBox MB_OK|MB_ICONSTOP "文件仍在使用。请关闭小工具和相关资源管理器窗口，稍后重新卸载。" /SD IDOK
  SetErrorLevel 1
  Abort
complete:
SectionEnd
