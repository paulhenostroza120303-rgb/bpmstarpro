; ========================================
;  BPMStart Pro - NSIS Installer
; ========================================

!include "MUI2.nsh"
!include "FileFunc.nsh"

; --------------------
; General Settings
; --------------------
Name "BPMStart Pro"
OutFile "BPMStartPro_Setup.exe"
InstallDir "$LOCALAPPDATA\BPMStartPro"
InstallDirRegKey HKCU "Software\BPMStartPro" "InstallDir"
RequestExecutionLevel user
Unicode True

; --------------------
; Version Info
; --------------------
VIProductVersion "2.0.0.0"
VIAddVersionKey "ProductName" "BPMStart Pro"
VIAddVersionKey "FileDescription" "BPMStart Pro - Descarga y separa musica"
VIAddVersionKey "LegalCopyright" "BPMStart"
VIAddVersionKey "FileVersion" "2.0.0"

; --------------------
; MUI Settings
; --------------------
!define MUI_ABORTWARNING
!define MUI_ICON "dist\BPMStartPro\_internal\static\icon.ico"
!define MUI_UNICON "dist\BPMStartPro\_internal\static\icon.ico"
!define MUI_WELCOMEPAGE_TITLE "BPMStart Pro - Instalador"
!define MUI_WELCOMEPAGE_TEXT "Este asistente instalara BPMStart Pro en su computadora.$\r$\n$\r$\nBPMStart Pro le permite descargar musica de YouTube y separar pistas (vocales, bateria, bajo, guitarra, etc.) con inteligencia artificial.$\r$\n$\r$\nHaga clic en Siguiente para continuar."
!define MUI_FINISHPAGE_RUN "$INSTDIR\BPMStartPro.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Ejecutar BPMStart Pro"
!define MUI_FINISHPAGE_LINK "Visitar BPMStart"
!define MUI_FINISHPAGE_LINK_LOCATION "https://bpmstart.vercel.app"

; --------------------
; Pages
; --------------------
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

; --------------------
; Languages
; --------------------
!insertmacro MUI_LANGUAGE "Spanish"

; --------------------
; Installer Sections
; --------------------
Section "BPMStart Pro (Principal)" SecMain
    SetOutPath "$INSTDIR"

    ; Main executable
    File "dist\BPMStartPro\BPMStartPro.exe"

    ; All internal files (maintain _internal folder structure)
    SetOutPath "$INSTDIR\_internal"
    File /r "dist\BPMStartPro\_internal\*.*"

    ; Store install dir
    WriteRegStr HKCU "Software\BPMStartPro" "InstallDir" "$INSTDIR"

    ; Create uninstaller
    WriteUninstaller "$INSTDIR\Uninstall.exe"

    ; Add to Programs list
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\BPMStartPro" \
        "DisplayName" "BPMStart Pro"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\BPMStartPro" \
        "UninstallString" '"$INSTDIR\Uninstall.exe"'
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\BPMStartPro" \
        "InstallLocation" "$INSTDIR"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\BPMStartPro" \
        "DisplayIcon" '"$INSTDIR\BPMStartPro.exe"'
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\BPMStartPro" \
        "Publisher" "BPMStart"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\BPMStartPro" \
        "DisplayVersion" "2.0.0"

    ; Get installed size
    ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
    IntFmt $0 "0x%08X" $0
    WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\BPMStartPro" \
        "EstimatedSize" "$0"
SectionEnd

Section "Acceso Directo - Escritorio" SecDesktop
    CreateShortcut "$DESKTOP\BPMStart Pro.lnk" "$INSTDIR\BPMStartPro.exe"
SectionEnd

Section "Acceso Directo - Menu Inicio" SecStartMenu
    CreateDirectory "$SMPROGRAMS\BPMStart Pro"
    CreateShortcut "$SMPROGRAMS\BPMStart Pro\BPMStart Pro.lnk" "$INSTDIR\BPMStartPro.exe"
    CreateShortcut "$SMPROGRAMS\BPMStart Pro\Desinstalar.lnk" "$INSTDIR\Uninstall.exe"
SectionEnd

; --------------------
; Component Descriptions
; --------------------
!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
    !insertmacro MUI_DESCRIPTION_TEXT ${SecMain} "Archivos principales de BPMStart Pro."
    !insertmacro MUI_DESCRIPTION_TEXT ${SecDesktop} "Crear acceso directo en el escritorio."
    !insertmacro MUI_DESCRIPTION_TEXT ${SecStartMenu} "Crear carpeta en el Menu Inicio."
!insertmacro MUI_FUNCTION_DESCRIPTION_END

; --------------------
; Uninstaller Section
; --------------------
Section "Uninstall"
    ; Remove files
    Delete "$INSTDIR\BPMStartPro.exe"
    Delete "$INSTDIR\Uninstall.exe"
    RMDir /r "$INSTDIR\_internal"
    RMDir "$INSTDIR"

    ; Remove shortcuts
    Delete "$DESKTOP\BPMStart Pro.lnk"
    Delete "$SMPROGRAMS\BPMStart Pro\BPMStart Pro.lnk"
    Delete "$SMPROGRAMS\BPMStart Pro\Desinstalar.lnk"
    RMDir "$SMPROGRAMS\BPMStart Pro"

    ; Remove registry keys
    DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\BPMStartPro"
    DeleteRegKey HKCU "Software\BPMStartPro"
SectionEnd
