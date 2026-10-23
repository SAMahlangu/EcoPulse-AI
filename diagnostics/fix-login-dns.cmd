@echo off
setlocal
rem This machine's Ethernet DNS resolver was unreachable. Public DNS resolved
rem the configured Supabase project successfully. Restore with restore-dns.cmd.
fltmc >nul 2>&1
if errorlevel 1 (
  echo Right-click this file and choose Run as administrator.
  pause
  exit /b 1
)

netsh interface ipv4 set dnsservers name="Ethernet" source=static address=1.1.1.1 validate=no
if errorlevel 1 goto failed
netsh interface ipv4 add dnsservers name="Ethernet" address=1.0.0.1 index=2 validate=no
if errorlevel 1 goto failed
rem Clear the unresponsive IPv6 DNS list; IPv6 networking stays enabled.
netsh interface ipv6 set dnsservers name="Ethernet" source=static address=none validate=no
if errorlevel 1 goto failed
ipconfig /flushdns
powershell.exe -NoProfile -Command "try { [System.Net.Dns]::GetHostAddresses('mzjcmfolqnxjvbcbusfd.supabase.co') | ForEach-Object { $_.IPAddressToString }; exit 0 } catch { Write-Host 'Supabase DNS resolution still failed.'; exit 1 }"
if errorlevel 1 goto failed
echo DNS repaired. Close and reopen your browser, then sign in again.
echo Original DHCP DNS can be restored with restore-dns.cmd as Administrator.
pause
exit /b 0

:failed
echo DNS repair did not complete. Restoring the original DHCP DNS settings.
call "%~dp0restore-dns.cmd"
pause
exit /b 1
