This is a version of python and pip embedded for Windows.

The specific version is: https://www.python.org/ftp/python/3.12.6/python-3.12.6-embed-amd64.zip

It was installed using the powershell script here: https://github.com/jtmoon79/PythonEmbed4Win with the fix described here: https://github.com/jtmoon79/PythonEmbed4Win/pull/8 on a Windows 11 system.  It will not work on MacOS.

If you want to add the capability of using ffmpeg, often used in code that manipulates sound files, then you can download the latest windows release of ffmpeg from here:

https://github.com/BtbN/FFmpeg-Builds/releases

Specifically this build: ffmpeg-master-latest-win64-gpl, for example: https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip

And then unzip that downloaded zip somewhere on your computer and move the ffmpeg.exe, ffplay.exe and ffprobe.exe files from /bin into the python-embedded folder in this skill.

The ffmpeg files are not included by default because they would add about 400mb or so to the skill size.