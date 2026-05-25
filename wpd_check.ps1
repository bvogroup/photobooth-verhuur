$manager = New-Object -ComObject PortableDeviceManager
$count = 0
$manager.GetDevices($null, [ref]$count)
Write-Host "WPD devices: $count"
if ($count -gt 0) {
    $deviceIds = New-Object string[] $count
    $manager.GetDevices($deviceIds, [ref]$count)
    foreach ($id in $deviceIds) {
        Write-Host "  $id"
        if ($id -match 'VID_04A9') {
            Write-Host "  ^^ Canon camera gevonden in WPD!"
        }
    }
}
