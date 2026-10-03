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
echo [1] Extract all models ^& 'untangle' textures (for Dolphin texture loader), choose a roster, extract icons
echo [2] Extract all models
echo [3] Extract player icons
echo.
echo [4] Patch .sluggie models or .png textures into game files
echo [5] UnPatch .sluggie model from game files
echo.
echo [6] Manually resize available hammerspace (extra model data storage) - usually not necessary
echo [7] Import edited icon sheets (.\2_Output_Models\_ICONS\sheets\)
echo [8] Repair unused characters' models (re-split them from their playable counterparts)
echo.
echo [9] Roster expansion: inject a roster configuration into 3_Output_Dat
echo.
set "tools_choice="
set /p "tools_choice=Enter option (or type exit to quit): "
echo.

if /i "!tools_choice!"=="exit" goto :eof

if "!tools_choice!"=="1" (
    set "SLUGGIES_MENU_SELECTION=1 - Full export with untangling + roster + icon export"
    set "SLUGGIES_MODEL_FILES="
    set "SLUGGIES_ICON_SHARED_MODE="
    call !SLUGGIES_LAUNCHER! --export --untangle
    if errorlevel 1 goto :after_command
    call :roster_menu
    if errorlevel 1 goto :after_command
    call !SLUGGIES_LAUNCHER! --export-icons --use-output
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
    set "SLUGGIES_MENU_SELECTION=4 - Patch model(s)"
    set "SLUGGIES_ICON_SHARED_MODE="
    set "model_files="
    set /p "model_files=Enter file name(s): "
    set "SLUGGIES_MODEL_FILES=!model_files!"
    call !SLUGGIES_LAUNCHER! --patch !model_files!
    goto :after_command
)
if "!tools_choice!"=="5" (
    set "SLUGGIES_MENU_SELECTION=5 - Unpatch model(s)"
    set "SLUGGIES_ICON_SHARED_MODE="
    set "model_files="
    set /p "model_files=Enter file name(s): "
    set "SLUGGIES_MODEL_FILES=!model_files!"
    call !SLUGGIES_LAUNCHER! --unpatch !model_files!
    goto :after_command
)
if "!tools_choice!"=="6" (
    set "SLUGGIES_MENU_SELECTION=6 - Resize hammerspace"
    set "SLUGGIES_MODEL_FILES="
    set "SLUGGIES_ICON_SHARED_MODE="
    call !SLUGGIES_LAUNCHER! -hs
    goto :after_command
)
if "!tools_choice!"=="7" (
    set "SLUGGIES_MENU_SELECTION=7 - Reimport icon sheets"
    set "SLUGGIES_MODEL_FILES="
    set "SLUGGIES_ICON_SHARED_MODE="
    call !SLUGGIES_LAUNCHER! --patch-icons
    goto :after_command
)

if "!tools_choice!"=="8" (
    set "SLUGGIES_MENU_SELECTION=8 - Re-split unused characters"
    set "SLUGGIES_MODEL_FILES="
    set "SLUGGIES_ICON_SHARED_MODE="
    call !SLUGGIES_LAUNCHER! --resplit-unused
    goto :after_command
)

if "!tools_choice!"=="9" (
    set "SLUGGIES_MENU_SELECTION=9 - Roster expansion"
    set "SLUGGIES_MODEL_FILES="
    set "SLUGGIES_ICON_SHARED_MODE="
    call :roster_menu
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

:roster_menu
rem Lists 1_Input\_RosterConfigurations\*.json and injects the chosen one (each choice replaces the previous
rem injection). Enter injects nothing. Sets errorlevel 1 when the injection failed.
set "roster_dir=1_Input\_RosterConfigurations"
for /f "delims==" %%V in ('set roster_cfg_ 2^>nul') do set "%%V="
set "roster_count=0"
echo   Roster configurations in !roster_dir! ^(each choice replaces the previous injection^):
for /f "delims=" %%F in ('dir /b /a-d /on "1_Input\_RosterConfigurations\*.json" 2^>nul') do (
    set /a roster_count+=1
    set "roster_cfg_!roster_count!=%%F"
    echo   [!roster_count!] %%F
)
if "!roster_count!"=="0" echo   ^(no .json files found^)
echo   [r] Reset the roster to vanilla
echo   [Enter] Skip ^(no roster changes^)
set "roster_mode="
set /p "roster_mode=Choose: "
set "roster_file="
if defined roster_mode for /f "delims=" %%N in ("!roster_mode!") do set "roster_file=!roster_cfg_%%N!"
if not defined roster_mode (
    echo   No roster configuration injected.
    exit /b 0
)
if /i "!roster_mode!"=="r" (
    call !SLUGGIES_LAUNCHER! --roster --remove
    exit /b !errorlevel!
)
if defined roster_file (
    call !SLUGGIES_LAUNCHER! --roster --config "!roster_dir!\!roster_file!"
    exit /b !errorlevel!
)
echo   Unknown choice: !roster_mode!
exit /b 1
