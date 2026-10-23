@echo off
rem Restore the original DHCP-provided DNS settings for Ethernet.
rem Run this file as Administrator.
netsh interface ipv4 set dnsservers name="Ethernet" source=dhcp
netsh interface ipv6 set dnsservers name="Ethernet" source=dhcp
ipconfig /flushdns
