@echo off
setlocal enabledelayedexpansion

echo ================================
echo   Scalp One-Click Build Script
echo ================================

REM --- Step 0: Move to script directory ---
cd /d %~dp0

REM --- Step 1: Sanity checks ---
echo Checking Node.js...
node -v >nul 2>&1
if errorlevel 1 (
  echo ❌ Node.js not found. Install Node.js first.
  pause
  exit /b 1
)

echo Checking npm...
npm -v >nul 2>&1
if errorlevel 1 (
  echo ❌ npm not found.
  pause
  exit /b 1
)

echo Checking Rust...
rustc --version >nul 2>&1
if errorlevel 1 (
  echo ❌ Rust not found. Install Rust (rustup).
  pause
  exit /b 1
)

echo ✅ Tooling OK
echo.

REM --- Step 2: Install dependencies ---
echo Installing npm dependencies...
npm install
if errorlevel 1 (
  echo ❌ npm install failed
  pause
  exit /b 1
)

REM --- Step 3: Build frontend (adjust if needed) ---
if exist "..\frontend\package.json" (
  echo Building frontend...
  cd ..\frontend
  npm install
  npm run build
  if errorlevel 1 (
    echo ❌ Frontend build failed
    pause
    exit /b 1
  )
  cd ..\desktop
) else (
  echo ℹ️ Frontend folder not found, skipping frontend build
)

REM --- Step 4: Clean previous build ---
echo Cleaning previous Tauri build...
rmdir /s /q src-tauri\target 2>nul

REM --- Step 5: Build Tauri app ---
echo Building Scalp app...
npm run tauri build
if errorlevel 1 (
  echo ❌ Tauri build failed
  pause
  exit /b 1
)

echo.
echo ✅ BUILD SUCCESSFUL
echo Output location:
echo src-tauri\target\release\bundle\
echo.

pause
endlocal
