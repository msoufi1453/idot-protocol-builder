@echo off
rem Starts the iDOT protocol builder with the first Python found that has tkinter.
cd /d "%~dp0"
for %%P in ("%LOCALAPPDATA%\anaconda3-1\python.exe" "%LOCALAPPDATA%\anaconda3\python.exe" "%USERPROFILE%\anaconda3\python.exe" "%ProgramData%\anaconda3\python.exe" "py" "python") do (
    %%P -c "import tkinter" >nul 2>&1 && (
        %%P idot_gui.py || pause
        exit /b
    )
)
echo Could not find a Python 3 installation with tkinter. Install Python from python.org and try again.
pause
