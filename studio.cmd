@echo off
rem One-command bring-up for docloom studio (cmd.exe / double-click wrapper).
rem Delegates to studio.ps1 next to this file; forwards any arguments.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0studio.ps1" %*
