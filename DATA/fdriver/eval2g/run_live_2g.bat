@echo off
rem fdriver 2g live snapshot (method_2g_v1). Called by Windows Task Scheduler (task: fdriver_2g_live_snapshot).
rem Quarters = last completed quarter and the next one (chosen inside live_2g). Extra args pass through (e.g. --dry).
rem After the snapshot, the 2g radar output is rebuilt from the latest record.
rem Log: OneDrive\INVESTMENT\<kr>\fdriver\2g\live\logs\snapshot_<yyyymmdd_hhmmss>.log
chcp 65001 >nul
setlocal
set "ROOT=%~dp0..\..\.."
set "PY=C:\Users\82108\AppData\Local\Programs\Python\Python39\python.exe"
set "PYTHONPATH=%ROOT%"
set "PYTHONIOENCODING=utf-8"
cd /d "%ROOT%"
for /f "usebackq tokens=1,2" %%A in (`%PY% -c "import datetime as d;from DATA.fdriver.config import output_path as o;print(d.datetime.now().strftime('%%Y%%m%%d_%%H%%M%%S'),o('2g','live','logs'))"`) do (
    set "TS=%%A"
    set "LOGDIR=%%B"
)
if not defined TS (
    echo %date% %time% log setup failed >> "%~dp0run_live_2g_error.log"
    exit /b 2
)
set "LOG=%LOGDIR%\snapshot_%TS%.log"
echo start %TS% args %* > "%LOG%"
%PY% -W ignore -m DATA.fdriver.eval2g.live_2g %* >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo snapshot exit %RC% >> "%LOG%"
if "%RC%"=="0" (
    %PY% -W ignore -m DATA.fdriver.eval2g.radar_2g >> "%LOG%" 2>&1
    echo radar exit %ERRORLEVEL% >> "%LOG%"
)
echo exit %RC% >> "%LOG%"
exit /b %RC%
