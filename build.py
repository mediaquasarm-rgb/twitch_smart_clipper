import subprocess,sys
import imageio_ffmpeg
from pathlib import Path
exe=imageio_ffmpeg.get_ffmpeg_exe()
cmd=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onefile','--windowed','--name','TwitchSmartClipper','--collect-all','yt_dlp','--collect-all','imageio_ffmpeg','--add-binary',f'{exe};imageio_ffmpeg/binaries','app.py']
subprocess.check_call(cmd)
