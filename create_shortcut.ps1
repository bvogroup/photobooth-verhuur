$WshShell = New-Object -ComObject WScript.Shell
$StartupPath = [System.IO.Path]::Combine($env:APPDATA, "Microsoft\Windows\Start Menu\Programs\Startup\Bootharoo.lnk")
$Shortcut = $WshShell.CreateShortcut($StartupPath)
$Shortcut.TargetPath = "C:\Users\User\AppData\Local\Programs\Python\Python314\pythonw.exe"
$Shortcut.Arguments = "C:\Photobooth\main.py"
$Shortcut.WorkingDirectory = "C:\Photobooth"
$Shortcut.Description = "Bootharoo Photobooth"
$Shortcut.Save()
Write-Host "Startup shortcut created at: $StartupPath"
