# KalshiEdge dataset build monitor (read-only)
# Windows PowerShell 5.1 / Windows 11. Monitors an existing build; does not start, stop, or modify it.

$ErrorActionPreference = 'SilentlyContinue'
Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase, System.Xaml

$ProjectRoot = Join-Path $HOME 'Desktop\KalshiEdge'
$BootstrapRoot = Join-Path $ProjectRoot 'data\bootstrap'
$DerivedRoot = Join-Path $BootstrapRoot 'derived'
$LogsRoot = Join-Path $BootstrapRoot 'logs'
$DatasetPath = Join-Path $DerivedRoot 'features.parquet'
$ProvenancePath = Join-Path $DerivedRoot 'features.provenance.json'
$ManifestPath = Join-Path $BootstrapRoot 'manifests\dataset\features.parquet.manifest.json'

if (-not (Test-Path -LiteralPath $ProjectRoot)) {
    [System.Windows.MessageBox]::Show("Project folder not found:`n$ProjectRoot`n`nEdit `$ProjectRoot near the top of this script if your repository is in another location.", 'KalshiEdge Build Monitor', 'OK', 'Error') | Out-Null
    exit 1
}

[xml]$xaml = @'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="KalshiEdge | Dataset Build Monitor" Height="850" Width="1240"
        MinHeight="720" MinWidth="1000" WindowStartupLocation="CenterScreen"
        Background="#0B1018" Foreground="#E7EDF5" FontFamily="Segoe UI">
  <Window.Resources>
    <Style TargetType="TextBlock"><Setter Property="Foreground" Value="#E7EDF5"/></Style>
    <Style TargetType="DataGrid">
      <Setter Property="Background" Value="#111925"/><Setter Property="Foreground" Value="#E7EDF5"/>
      <Setter Property="RowBackground" Value="#111925"/><Setter Property="AlternatingRowBackground" Value="#172231"/>
      <Setter Property="GridLinesVisibility" Value="Horizontal"/><Setter Property="HorizontalGridLinesBrush" Value="#2A3748"/>
      <Setter Property="VerticalGridLinesBrush" Value="#2A3748"/><Setter Property="BorderBrush" Value="#2A3748"/>
      <Setter Property="HeadersVisibility" Value="Column"/><Setter Property="CanUserAddRows" Value="False"/>
      <Setter Property="IsReadOnly" Value="True"/><Setter Property="AutoGenerateColumns" Value="True"/>
      <Setter Property="RowHeaderWidth" Value="0"/><Setter Property="FontSize" Value="12"/>
    </Style>
  </Window.Resources>
  <Grid Margin="16">
    <Grid.RowDefinitions>
      <RowDefinition Height="Auto"/><RowDefinition Height="98"/><RowDefinition Height="260"/><RowDefinition Height="250"/><RowDefinition Height="Auto"/>
    </Grid.RowDefinitions>
    <DockPanel Grid.Row="0" Margin="0,0,0,14">
      <StackPanel DockPanel.Dock="Left">
        <TextBlock Text="KALSHIEDGE / DATASET BUILD" FontSize="21" FontWeight="SemiBold"/>
        <TextBlock Text="Read-only live monitor  •  Watches the existing bootstrap-build-dataset process" FontSize="12" Foreground="#9AAABD" Margin="0,4,0,0"/>
        <TextBlock x:Name="RootLabel" FontSize="11" Foreground="#70839A" Margin="0,3,0,0"/>
      </StackPanel>
      <Border DockPanel.Dock="Right" Background="#182738" CornerRadius="7" Padding="12,8" VerticalAlignment="Center">
        <TextBlock x:Name="StatusLabel" Text="INITIALIZING MONITOR…" FontWeight="SemiBold" FontSize="12"/>
      </Border>
    </DockPanel>
    <UniformGrid Grid.Row="1" Rows="1" Columns="4" Margin="0,0,0,14">
      <Border Background="#111925" BorderBrush="#253346" BorderThickness="1" CornerRadius="8" Padding="14" Margin="0,0,8,0">
        <StackPanel><TextBlock Text="BUILD PROCESS" FontSize="11" Foreground="#93A4B8"/><TextBlock x:Name="ProcessMetric" Text="Searching…" FontSize="21" FontWeight="SemiBold" Margin="0,7,0,0"/><TextBlock x:Name="ProcessSub" Text="" FontSize="11" Foreground="#93A4B8"/></StackPanel>
      </Border>
      <Border Background="#111925" BorderBrush="#253346" BorderThickness="1" CornerRadius="8" Padding="14" Margin="0,0,8,0">
        <StackPanel><TextBlock Text="PROCESS CPU" FontSize="11" Foreground="#93A4B8"/><TextBlock x:Name="CpuMetric" Text="—" FontSize="21" FontWeight="SemiBold" Margin="0,7,0,0"/><TextBlock x:Name="CpuSub" Text="Normalized across logical CPUs" FontSize="11" Foreground="#93A4B8"/></StackPanel>
      </Border>
      <Border Background="#111925" BorderBrush="#253346" BorderThickness="1" CornerRadius="8" Padding="14" Margin="0,0,8,0">
        <StackPanel><TextBlock Text="BUILD RAM" FontSize="11" Foreground="#93A4B8"/><TextBlock x:Name="RamMetric" Text="—" FontSize="21" FontWeight="SemiBold" Margin="0,7,0,0"/><TextBlock x:Name="RamSub" Text="Combined matched process memory" FontSize="11" Foreground="#93A4B8"/></StackPanel>
      </Border>
      <Border Background="#111925" BorderBrush="#253346" BorderThickness="1" CornerRadius="8" Padding="14">
        <StackPanel><TextBlock Text="ELAPSED SINCE PROCESS START" FontSize="11" Foreground="#93A4B8"/><TextBlock x:Name="ElapsedMetric" Text="—" FontSize="21" FontWeight="SemiBold" Margin="0,7,0,0"/><TextBlock x:Name="SampleSub" Text="Refreshes every 2 seconds" FontSize="11" Foreground="#93A4B8"/></StackPanel>
      </Border>
    </UniformGrid>
    <Grid Grid.Row="2" Margin="0,0,0,14">
      <Grid.ColumnDefinitions><ColumnDefinition Width="1.05*"/><ColumnDefinition Width="1.3*"/></Grid.ColumnDefinitions>
      <Border Grid.Column="0" Background="#111925" BorderBrush="#253346" BorderThickness="1" CornerRadius="8" Padding="12" Margin="0,0,8,0">
        <Grid><Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="*"/><RowDefinition Height="Auto"/></Grid.RowDefinitions>
          <TextBlock Text="CPU ACTIVITY TREND" FontSize="13" FontWeight="SemiBold" Margin="2,0,0,8"/>
          <Canvas x:Name="CpuCanvas" Grid.Row="1" ClipToBounds="True"/>
          <TextBlock Grid.Row="2" Text="Chart shows estimated CPU use for matching build processes as a share of all logical CPUs." FontSize="10" Foreground="#8091A6" TextWrapping="Wrap" Margin="2,6,2,0"/>
        </Grid>
      </Border>
      <Border Grid.Column="1" Background="#111925" BorderBrush="#253346" BorderThickness="1" CornerRadius="8" Padding="12">
        <Grid><Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="*"/></Grid.RowDefinitions>
          <TextBlock Text="MATCHED BUILD PROCESSES" FontSize="13" FontWeight="SemiBold" Margin="2,0,0,8"/>
          <DataGrid x:Name="ProcessGrid" Grid.Row="1" AlternationCount="2"/>
        </Grid>
      </Border>
    </Grid>
    <Grid Grid.Row="3" Margin="0,0,0,12">
      <Grid.ColumnDefinitions><ColumnDefinition Width="1.05*"/><ColumnDefinition Width="1.3*"/></Grid.ColumnDefinitions>
      <Border Grid.Column="0" Background="#111925" BorderBrush="#253346" BorderThickness="1" CornerRadius="8" Padding="12" Margin="0,0,8,0">
        <Grid><Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="*"/></Grid.RowDefinitions>
          <TextBlock Text="EXPECTED OUTPUTS" FontSize="13" FontWeight="SemiBold" Margin="2,0,0,8"/>
          <DataGrid x:Name="ArtifactGrid" Grid.Row="1" AlternationCount="2"/>
        </Grid>
      </Border>
      <Border Grid.Column="1" Background="#111925" BorderBrush="#253346" BorderThickness="1" CornerRadius="8" Padding="12">
        <Grid><Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="*"/></Grid.RowDefinitions>
          <DockPanel Margin="2,0,0,8"><TextBlock Text="LATEST BUILD LOG" FontSize="13" FontWeight="SemiBold"/><TextBlock x:Name="LogInfo" DockPanel.Dock="Right" Text="No log detected yet" FontSize="10" Foreground="#8091A6" HorizontalAlignment="Right"/></DockPanel>
          <TextBox x:Name="LogText" Grid.Row="1" Background="#0B1018" Foreground="#B9CADB" BorderBrush="#253346" FontFamily="Consolas" FontSize="11" IsReadOnly="True" TextWrapping="NoWrap" VerticalScrollBarVisibility="Auto" HorizontalScrollBarVisibility="Auto" AcceptsReturn="True"/>
        </Grid>
      </Border>
    </Grid>
    <DockPanel Grid.Row="4">
      <TextBlock Text="Monitor only: it does not start/stop the build, modify data, or touch the Phase 1 scheduled collector." FontSize="11" Foreground="#8191A5" VerticalAlignment="Center"/>
      <Button x:Name="RefreshButton" DockPanel.Dock="Right" Content="Refresh now" Padding="16,7" Margin="12,0,0,0" Background="#203247" Foreground="#E7EDF5" BorderBrush="#3A526E"/>
    </DockPanel>
  </Grid>
</Window>
'@

$reader = New-Object System.Xml.XmlNodeReader $xaml
$window = [Windows.Markup.XamlReader]::Load($reader)
$script:WindowRef = $window
$script:RootLabel = $window.FindName('RootLabel')
$script:StatusLabel = $window.FindName('StatusLabel')
$script:ProcessMetric = $window.FindName('ProcessMetric')
$script:ProcessSub = $window.FindName('ProcessSub')
$script:CpuMetric = $window.FindName('CpuMetric')
$script:CpuSub = $window.FindName('CpuSub')
$script:RamMetric = $window.FindName('RamMetric')
$script:RamSub = $window.FindName('RamSub')
$script:ElapsedMetric = $window.FindName('ElapsedMetric')
$script:SampleSub = $window.FindName('SampleSub')
$script:CpuCanvas = $window.FindName('CpuCanvas')
$script:ProcessGrid = $window.FindName('ProcessGrid')
$script:ArtifactGrid = $window.FindName('ArtifactGrid')
$script:LogInfo = $window.FindName('LogInfo')
$script:LogText = $window.FindName('LogText')
$script:RefreshButton = $window.FindName('RefreshButton')
$script:RootLabel.Text = $ProjectRoot
$script:PrevCpu = @{}
$script:PrevSample = Get-Date
$script:CpuHistory = New-Object 'System.Collections.Generic.List[double]'
$script:SeenBuilder = $false
$script:BuildStart = $null
$script:LastLogPath = ''
$script:LastLogWrite = [datetime]::MinValue
$script:LastCpuPercent = 0.0
$script:TickBusy = $false
$script:LogicalCpuCount = [Math]::Max(1, [Environment]::ProcessorCount)

function Update-CpuChart {
    $canvas = $script:CpuCanvas
    $canvas.Children.Clear()
    $width = [Math]::Max(10, $canvas.ActualWidth)
    $height = [Math]::Max(10, $canvas.ActualHeight)
    $gridBrush = New-Object System.Windows.Media.SolidColorBrush ([System.Windows.Media.ColorConverter]::ConvertFromString('#263445'))
    for ($i = 0; $i -le 4; $i++) {
        $y = ($height - 12) * $i / 4 + 6
        $line = New-Object System.Windows.Shapes.Line
        $line.X1 = 0; $line.X2 = $width; $line.Y1 = $y; $line.Y2 = $y
        $line.Stroke = $gridBrush; $line.StrokeThickness = 1
        $canvas.Children.Add($line) | Out-Null
    }
    if ($script:CpuHistory.Count -lt 2) { return }
    $points = New-Object System.Windows.Media.PointCollection
    $plotW = $width - 8; $plotH = $height - 16
    $count = $script:CpuHistory.Count
    for ($i = 0; $i -lt $count; $i++) {
        $x = 4 + ($plotW * $i / [Math]::Max(1, ($count - 1)))
        $val = [Math]::Max(0, [Math]::Min(100, $script:CpuHistory[$i]))
        $y = $height - 8 - ($plotH * $val / 100)
        $points.Add([System.Windows.Point]::new($x, $y))
    }
    $polyline = New-Object System.Windows.Shapes.Polyline
    $polyline.Points = $points
    $polyline.Stroke = New-Object System.Windows.Media.SolidColorBrush ([System.Windows.Media.ColorConverter]::ConvertFromString('#55C7A5'))
    $polyline.StrokeThickness = 2.5
    $canvas.Children.Add($polyline) | Out-Null
}

function Update-Monitor {
    if ($script:TickBusy) { return }
    $script:TickBusy = $true
    try {
        $now = Get-Date
        $cim = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
            $_.CommandLine -and $_.CommandLine -match 'bootstrap-build-dataset' -and $_.CommandLine -match 'KalshiEdge'
        })
        $processRows = @()
        $currentCpu = @{}
        $totalCpuDelta = 0.0
        $totalMemory = [int64]0
        $earliestStart = $null
        foreach ($wp in $cim) {
            $gp = Get-Process -Id $wp.ProcessId -ErrorAction SilentlyContinue
            if (-not $gp) { continue }
            $cpuSeconds = 0.0
            if ($null -ne $gp.CPU) { $cpuSeconds = [double]$gp.CPU }
            $memBytes = [int64]$gp.WorkingSet64
            $totalMemory += $memBytes
            $currentCpu[[string]$gp.Id] = $cpuSeconds
            if ($script:PrevCpu.ContainsKey([string]$gp.Id)) {
                $delta = $cpuSeconds - [double]$script:PrevCpu[[string]$gp.Id]
                if ($delta -gt 0) { $totalCpuDelta += $delta }
            }
            $startTimeText = ''
            try {
                $st = $gp.StartTime
                $startTimeText = $st.ToString('HH:mm:ss')
                if ($null -eq $earliestStart -or $st -lt $earliestStart) { $earliestStart = $st }
            } catch {}
            $cmd = [string]$wp.CommandLine
            if ($cmd.Length -gt 92) { $cmd = '…' + $cmd.Substring($cmd.Length - 91) }
            $processRows += [PSCustomObject]@{
                PID = $gp.Id
                ParentPID = $wp.ParentProcessId
                Process = $gp.ProcessName
                'CPU seconds' = [Math]::Round($cpuSeconds, 1)
                'RAM MB' = [Math]::Round($memBytes / 1MB, 1)
                Started = $startTimeText
                Command = $cmd
            }
        }
        $elapsedSample = [Math]::Max(0.1, ($now - $script:PrevSample).TotalSeconds)
        $cpuPercent = [Math]::Max(0, [Math]::Min(100, ($totalCpuDelta / $elapsedSample / $script:LogicalCpuCount) * 100))
        $script:PrevCpu = $currentCpu
        $script:PrevSample = $now

        if ($processRows.Count -gt 0) {
            $script:SeenBuilder = $true
            if ($null -eq $script:BuildStart -or ($null -ne $earliestStart -and $earliestStart -lt $script:BuildStart)) { $script:BuildStart = $earliestStart }
            $script:StatusLabel.Text = 'BUILDER RUNNING'
            $script:StatusLabel.Foreground = New-Object System.Windows.Media.SolidColorBrush ([System.Windows.Media.ColorConverter]::ConvertFromString('#55D6A4'))
            $script:ProcessMetric.Text = "$($processRows.Count) active"
            $script:ProcessSub.Text = 'Matched command: bootstrap-build-dataset'
        } elseif ($script:SeenBuilder) {
            $script:StatusLabel.Text = 'BUILDER EXITED — VERIFY RESULTS'
            $script:StatusLabel.Foreground = New-Object System.Windows.Media.SolidColorBrush ([System.Windows.Media.ColorConverter]::ConvertFromString('#FFC66D'))
            $script:ProcessMetric.Text = 'Not detected'
            $script:ProcessSub.Text = 'Check window #1 for exit code'
        } else {
            $script:StatusLabel.Text = 'WAITING FOR BUILD PROCESS'
            $script:StatusLabel.Foreground = New-Object System.Windows.Media.SolidColorBrush ([System.Windows.Media.ColorConverter]::ConvertFromString('#FFC66D'))
            $script:ProcessMetric.Text = 'Not detected'
            $script:ProcessSub.Text = 'Confirm build is still running in window #1'
        }
        $script:CpuMetric.Text = ('{0:N1}%' -f $cpuPercent)
        $script:RamMetric.Text = ('{0:N0} MB' -f ($totalMemory / 1MB))
        if ($script:BuildStart) {
            $dur = $now - $script:BuildStart
            $script:ElapsedMetric.Text = '{0:00}:{1:00}:{2:00}' -f [Math]::Floor($dur.TotalHours), $dur.Minutes, $dur.Seconds
        } else { $script:ElapsedMetric.Text = '—' }
        $script:ProcessGrid.ItemsSource = $processRows
        $script:CpuHistory.Add([double]$cpuPercent)
        while ($script:CpuHistory.Count -gt 90) { $script:CpuHistory.RemoveAt(0) }
        Update-CpuChart

        $artifactSpecs = @(
            [PSCustomObject]@{ Name = 'features.parquet'; Path = $DatasetPath },
            [PSCustomObject]@{ Name = 'features.provenance.json'; Path = $ProvenancePath },
            [PSCustomObject]@{ Name = 'features.parquet.manifest.json'; Path = $ManifestPath }
        )
        $artifactRows = @()
        foreach ($a in $artifactSpecs) {
            $f = Get-Item -LiteralPath $a.Path -ErrorAction SilentlyContinue
            if ($f) {
                $artifactRows += [PSCustomObject]@{ Artifact = $a.Name; Status = 'PRESENT'; 'Size MB' = [Math]::Round($f.Length / 1MB, 2); Modified = $f.LastWriteTime.ToString('HH:mm:ss') }
            } else {
                $artifactRows += [PSCustomObject]@{ Artifact = $a.Name; Status = 'NOT FOUND YET'; 'Size MB' = '—'; Modified = '—' }
            }
        }
        $script:ArtifactGrid.ItemsSource = $artifactRows

        $log = Get-ChildItem -LiteralPath $LogsRoot -Filter 'dataset-build-*.log' -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($log) {
            $script:LogInfo.Text = "$($log.Name)  •  $($log.LastWriteTime.ToString('HH:mm:ss'))  •  $([Math]::Round($log.Length / 1KB,1)) KB"
            if ($script:LastLogPath -ne $log.FullName -or $script:LastLogWrite -ne $log.LastWriteTime) {
                $script:LastLogPath = $log.FullName
                $script:LastLogWrite = $log.LastWriteTime
                $lines = @(Get-Content -LiteralPath $log.FullName -Tail 18 -ErrorAction SilentlyContinue)
                $script:LogText.Text = ($lines -join [Environment]::NewLine)
                $script:LogText.ScrollToEnd()
            }
        } else {
            $script:LogInfo.Text = 'No build log detected in data\bootstrap\logs'
            if (-not $script:LastLogPath) { $script:LogText.Text = "No log file found yet.`r`n`r`nThe monitor does not create a log or change the running build." }
        }
    } finally { $script:TickBusy = $false }
}

$timer = New-Object System.Windows.Threading.DispatcherTimer
$timer.Interval = [TimeSpan]::FromSeconds(2)
$timer.Add_Tick({ Update-Monitor })
$script:RefreshButton.Add_Click({ Update-Monitor })
$window.Add_ContentRendered({ Update-Monitor; $timer.Start() })
$window.Add_Closed({ $timer.Stop() })
$window.ShowDialog() | Out-Null
