@echo off
setlocal EnableDelayedExpansion

set "SLUGGIES_SOURCE=StartTools.bat"
set SLUGGIES_LAUNCHER=python start.py
if exist "%~dp0sluggies-dat-tools.exe" set SLUGGIES_LAUNCHER="%~dp0sluggies-dat-tools.exe"

:menu
echo.
echo ==================
echo Sluggers Dat Tools
echo ==================
echo.
echo [1] Extract all models, icons ^& 'untangle' textures (for Dolphin texture loader)
echo [2] Extract all models
echo [3] Extract player icons
echo [4] Patch all 6 unused-character icons into game files
echo.
echo [5] Patch .sluggies model or .png texture into game files
echo [6] UnPatch .sluggies model in game files
echo. 
echo [7] Manually resize available hammerspace (extra model data storage) - usually not necessary
echo [8] Import edited icon sheets (.\2_Output_Models\_ICONS\sheets\)
echo.
set "tools_choice="
set /p "tools_choice=Enter option (or type exit to quit): "
echo.

if /i "!tools_choice!"=="exit" goto :eof

if "!tools_choice!"=="1" (
    set "SLUGGIES_MENU_SELECTION=1 - Full export with untangling + icon routes + icon export"
    set "SLUGGIES_MODEL_FILES="
    set "SLUGGIES_ICON_SHARED_MODE="
    call !SLUGGIES_LAUNCHER! --export --untangle
    if errorlevel 1 goto :after_command
    call !SLUGGIES_LAUNCHER! --prepare-icon-routes --no-overwrite-copy
    if errorlevel 1 goto :after_command
    call !SLUGGIES_LAUNCHER! --export-icons --use-output
    if errorlevel 1 goto :after_command
    call !SLUGGIES_LAUNCHER! --add-custom-icons
    goto :after_command
)
if "!tools_choice!"=="2" (
    set "SLUGGIES_MENU_SELECTION=2 - Export all models"
    set "SLUGGIES_MODEL_FILES="
    set "SLUGGIES_ICON_SHARED_MODE="
    call !SLUGGIES_LAUNCHER! --export
    goto :after_command
)
if "!tools_choice!"=="3" (
    set "SLUGGIES_MENU_SELECTION=3 - Export player icons only"
    set "SLUGGIES_MODEL_FILES="
    set "SLUGGIES_ICON_SHARED_MODE="
    call !SLUGGIES_LAUNCHER! --export-icons
    goto :after_command
)
if "!tools_choice!"=="4" (
    set "SLUGGIES_MENU_SELECTION=4 - Patch custom icons"
    set "SLUGGIES_MODEL_FILES="
    set "SLUGGIES_ICON_SHARED_MODE="
    call !SLUGGIES_LAUNCHER! --add-custom-icons
    set "tools_result=!errorlevel!"
    if not "!tools_result!"=="0" (
        echo.
        echo Custom icon patching failed with exit code !tools_result!.
    ) else (
        echo.
        echo Custom icon patching completed successfully.
    )
    goto :after_command
)
if "!tools_choice!"=="5" (
    set "SLUGGIES_MENU_SELECTION=5 - Patch model(s)"
    set "SLUGGIES_ICON_SHARED_MODE="
    set "model_files="
    set /p "model_files=Enter file name(s): "
    set "SLUGGIES_MODEL_FILES=!model_files!"
    call !SLUGGIES_LAUNCHER! --patch !model_files!
    goto :after_command
)
if "!tools_choice!"=="6" (
    set "SLUGGIES_MENU_SELECTION=6 - Unpatch model(s)"
    set "SLUGGIES_ICON_SHARED_MODE="
    set "model_files="
    set /p "model_files=Enter file name(s): "
    set "SLUGGIES_MODEL_FILES=!model_files!"
    call !SLUGGIES_LAUNCHER! --unpatch !model_files!
    goto :after_command
)
if "!tools_choice!"=="7" (
    set "SLUGGIES_MENU_SELECTION=7 - Resize hammerspace"
    set "SLUGGIES_MODEL_FILES="
    set "SLUGGIES_ICON_SHARED_MODE="
    call !SLUGGIES_LAUNCHER! -hs
    goto :after_command
)
if "!tools_choice!"=="8" (
    set "SLUGGIES_MENU_SELECTION=8 - Reimport icon sheets"
    set "SLUGGIES_MODEL_FILES="
    echo Shared image handling:
    echo   [1] strict ^(fail on conflicting shared-image edits^)
    echo   [2] first-page ^(force lowest texture index per shared group^)
    set "icon_shared_mode="
    set /p "icon_shared_mode=Choose mode [1/2]: "
    set "SLUGGIES_ICON_SHARED_MODE=!icon_shared_mode!"
    if "!icon_shared_mode!"=="2" (
        call !SLUGGIES_LAUNCHER! --patch-icons --shared-mode first-page
    ) else (
        call !SLUGGIES_LAUNCHER! --patch-icons
    )
    goto :after_command
)

set "SLUGGIES_MENU_SELECTION=Invalid option: !tools_choice!"
set "SLUGGIES_MODEL_FILES="
set "SLUGGIES_ICON_SHARED_MODE="
echo Invalid option. Returning to the menu.

:after_command
echo.
pause
goto :menu
