name: Build Windows installer
on:
  workflow_dispatch:
  push:
    branches: [main, master]
jobs:
  build:
    runs-on: windows-2022
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          architecture: x64
      - name: Install Python dependencies
        run: python -m pip install -r requirements.txt
      - name: Build portable exe
        run: python build.py
      - name: Install Inno Setup
        shell: powershell
        run: choco install innosetup --yes --no-progress
      - name: Build setup exe
        shell: powershell
        run: '& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss'
      - uses: actions/upload-artifact@v4
        with:
          name: TwitchSmartClipper-Windows10-Installer
          path: installer_output/*.exe
          if-no-files-found: error
