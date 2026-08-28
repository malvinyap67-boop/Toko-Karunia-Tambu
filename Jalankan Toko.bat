@echo off
title Toko Karunia Tambu - Sistem Manajemen
color 0A
echo.
echo  ==========================================
echo   Toko Karunia Tambu - Sistem Manajemen
echo  ==========================================
echo.
echo  Server sedang dimulai...
echo  Setelah server jalan, browser akan terbuka otomatis.
echo  Tekan CTRL+C untuk menghentikan server.
echo.

:: Tunggu sebentar lalu buka browser
start "" cmd /c "timeout /t 2 /nobreak >nul && start http://127.0.0.1:5000"

:: Jalankan Flask
python app.py

pause
