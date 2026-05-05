@echo off
echo Sluggers Dat Tools
echo ==================
echo [1] Extract All Models
echo [2] Patch model into game files
echo [3] UnPatch model in game files
echo [4] Create or resize hammerspace (extra model data storage)
echo.
set /p "choice=Enter option: "
echo.

if "%choice%"=="1" (
    python export.py
    goto end
)
if "%choice%"=="2" (
    set /p "files=Enter file name(s): "
    python patch.py %files%
    goto end
)
if "%choice%"=="3" (
    set /p "files=Enter file name(s): "
    python patch.py -u %files%
    goto end
)
if "%choice%"=="4" (
    python patch.py -hs
    goto end
)

echo Invalid option. Exiting.

:end
echo.
pause

