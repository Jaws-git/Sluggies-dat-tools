@echo off
setlocal
rem The target game directory is stored in the "sluggiespath" file next to this
rem script. On first run it is created by asking the user for the path.

set "src_dir=%~dp0"
set "pathfile=%src_dir%sluggiespath"

if exist "%pathfile%" (
	set /p dir=<"%pathfile%"
)

if not defined dir goto ask_path

rem A path is available - just run the copy.
goto do_copy

:ask_path
echo Please enter the path to your unpacked copy of Mario Super Sluggers (US) main folder:
echo (this only has to be done once)
set /p "dir="
if not defined dir (
	echo No path entered. Aborting.
	exit /b 1
)
echo %dir%>"%pathfile%"

:do_copy
if not exist "%dir%\" (
	echo WARNING: The target path does not exist.
	echo Update the "sluggiespath" file in this folder and try again.
	exit /b 1
)

if exist "%src_dir%dt_na.dat" (
	copy /Y "%src_dir%dt_na.dat" "%dir%\DATA\files\dt_na.dat" >nul
)

if exist "%src_dir%main.dol" (
	copy /Y "%src_dir%main.dol" "%dir%\DATA\sys\main.dol" >nul
)

if exist "%src_dir%fst.bin" (
	copy /Y "%src_dir%fst.bin" "%dir%\DATA\sys\fst.bin" >nul
)

exit /b 0