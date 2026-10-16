<#
  splash.ps1 -- animation de demarrage « boot portable de JARVIS » (version riche).

  Reacteur ARC : grille HUD en fond, trois arcs concentriques qui tournent, des
  particules en orbite, un balayage « radar », un coeur a bloom qui pulse, un
  titre a halo, une barre de progression et un journal de boot. 100 % XAML/WPF,
  aucune dependance Python (tourne meme pendant l'installation).

      powershell -STA -File splash.ps1 -StatusFile <f> [-Subtitle "..."]
                 [-Accent "#00E5FF"] [-ReduceMotion] [-SettingsFile <portable.json>] [-ValidateOnly]

  -ReduceMotion (accessibilite) : fige les rotations, garde un fondu doux.
  -Accent / portable.json {"accent":"#RRGGBB","reduceMotion":true} : theme.
  Sentinelles dans le fichier d'etat : __READY__ (fondu+fermeture), __CLOSE__ (fermeture).
#>
[CmdletBinding()]
param(
    [string]$StatusFile,
    [string]$Subtitle = "BOOT PORTABLE",
    [string]$Accent = "#00E5FF",
    [switch]$ReduceMotion,
    [string]$SettingsFile,
    [switch]$ValidateOnly
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase

# Reglages optionnels (theme/accessibilite) depuis portable.json
if ($SettingsFile -and (Test-Path $SettingsFile)) {
    try {
        $s = Get-Content $SettingsFile -Raw | ConvertFrom-Json
        if ($s.accent) { $Accent = [string]$s.accent }
        if ($s.reduceMotion) { $ReduceMotion = [bool]$s.reduceMotion }
    } catch {}
}
if ($Accent -notmatch '^#[0-9A-Fa-f]{6}$') { $Accent = "#00E5FF" }
$A = $Accent.Substring(1)   # RRGGBB

# briques d'animation (vides en mode mouvement reduit)
$spin1 = $spin2 = $spin3 = $orbit = $radar = $pulseX = $pulseY = $bar = ""
if (-not $ReduceMotion) {
    $spin1 = '<DoubleAnimation Storyboard.TargetName="rotOuter" Storyboard.TargetProperty="Angle" From="0" To="360" Duration="0:0:11" RepeatBehavior="Forever"/>'
    $spin2 = '<DoubleAnimation Storyboard.TargetName="rotMid" Storyboard.TargetProperty="Angle" From="360" To="0" Duration="0:0:7" RepeatBehavior="Forever"/>'
    $spin3 = '<DoubleAnimation Storyboard.TargetName="rotInner" Storyboard.TargetProperty="Angle" From="0" To="360" Duration="0:0:4" RepeatBehavior="Forever"/>'
    $orbit = '<DoubleAnimation Storyboard.TargetName="rotOrbit" Storyboard.TargetProperty="Angle" From="0" To="360" Duration="0:0:3" RepeatBehavior="Forever"/>'
    $radar = '<DoubleAnimation Storyboard.TargetName="rotRadar" Storyboard.TargetProperty="Angle" From="0" To="360" Duration="0:0:2.6" RepeatBehavior="Forever"/>'
    $pulseX = '<DoubleAnimation Storyboard.TargetName="pulse" Storyboard.TargetProperty="ScaleX" From="0.86" To="1.14" Duration="0:0:1.0" AutoReverse="True" RepeatBehavior="Forever"/>'
    $pulseY = '<DoubleAnimation Storyboard.TargetName="pulse" Storyboard.TargetProperty="ScaleY" From="0.86" To="1.14" Duration="0:0:1.0" AutoReverse="True" RepeatBehavior="Forever"/>'
    $bar   = '<DoubleAnimation Storyboard.TargetName="barFill" Storyboard.TargetProperty="Width" From="20" To="360" Duration="0:0:2.2" AutoReverse="True" RepeatBehavior="Forever"/>'
}

$xaml = @"
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        WindowStyle="None" AllowsTransparency="True" Background="Transparent"
        WindowStartupLocation="CenterScreen" Topmost="True" ResizeMode="NoResize"
        Width="640" Height="440" Opacity="0" Name="Win">
  <Window.Triggers>
    <EventTrigger RoutedEvent="FrameworkElement.Loaded">
      <BeginStoryboard>
        <Storyboard>
          <DoubleAnimation Storyboard.TargetName="Win" Storyboard.TargetProperty="Opacity" From="0" To="1" Duration="0:0:0.9"/>
          $spin1 $spin2 $spin3 $orbit $radar $pulseX $pulseY $bar
        </Storyboard>
      </BeginStoryboard>
    </EventTrigger>
  </Window.Triggers>
  <Border CornerRadius="20" BorderThickness="1" BorderBrush="#33$A" Background="#02060B">
    <Grid>
      <Grid.Background>
        <RadialGradientBrush GradientOrigin="0.5,0.4" Center="0.5,0.4" RadiusX="0.85" RadiusY="0.85">
          <GradientStop Color="#FF0A1B2A" Offset="0"/><GradientStop Color="#FF050A12" Offset="0.6"/><GradientStop Color="#FF01040A" Offset="1"/>
        </RadialGradientBrush>
      </Grid.Background>
      <Grid.RowDefinitions><RowDefinition Height="*"/><RowDefinition Height="Auto"/></Grid.RowDefinitions>
      <!-- grille HUD en fond -->
      <Rectangle Grid.Row="0" Grid.RowSpan="2" Opacity="0.10">
        <Rectangle.Fill>
          <DrawingBrush TileMode="Tile" Viewport="0,0,26,26" ViewportUnits="Absolute">
            <DrawingBrush.Drawing>
              <GeometryDrawing>
                <GeometryDrawing.Geometry><RectangleGeometry Rect="0,0,26,26"/></GeometryDrawing.Geometry>
                <GeometryDrawing.Pen><Pen Thickness="0.6"><Pen.Brush><SolidColorBrush Color="#$A"/></Pen.Brush></Pen></GeometryDrawing.Pen>
              </GeometryDrawing>
            </DrawingBrush.Drawing>
          </DrawingBrush>
        </Rectangle.Fill>
      </Rectangle>

      <Canvas Grid.Row="0" Width="240" Height="240" HorizontalAlignment="Center" VerticalAlignment="Center">
        <!-- halo -->
        <Ellipse Canvas.Left="40" Canvas.Top="40" Width="160" Height="160" Fill="#20$A"><Ellipse.Effect><BlurEffect Radius="55"/></Ellipse.Effect></Ellipse>
        <!-- balayage radar -->
        <Canvas Canvas.Left="120" Canvas.Top="120" RenderTransformOrigin="0,0">
          <Canvas.RenderTransform><RotateTransform x:Name="rotRadar" Angle="0"/></Canvas.RenderTransform>
          <Path Fill="#2A$A"><Path.Data><PathGeometry><PathFigure StartPoint="0,0"><LineSegment Point="104,-30"/><ArcSegment Point="104,30" Size="108,108" SweepDirection="Clockwise"/></PathFigure></PathGeometry></Path.Data></Path>
        </Canvas>
        <!-- anneau externe -->
        <Ellipse Canvas.Left="14" Canvas.Top="14" Width="212" Height="212" Stroke="#55$A" StrokeThickness="2" StrokeDashArray="2 6" RenderTransformOrigin="0.5,0.5">
          <Ellipse.RenderTransform><RotateTransform x:Name="rotOuter" Angle="0"/></Ellipse.RenderTransform></Ellipse>
        <!-- anneau median -->
        <Ellipse Canvas.Left="40" Canvas.Top="40" Width="160" Height="160" Stroke="#CC$A" StrokeThickness="3" StrokeDashArray="20 12" RenderTransformOrigin="0.5,0.5">
          <Ellipse.Effect><DropShadowEffect Color="#$A" BlurRadius="20" ShadowDepth="0"/></Ellipse.Effect>
          <Ellipse.RenderTransform><RotateTransform x:Name="rotMid" Angle="0"/></Ellipse.RenderTransform></Ellipse>
        <!-- anneau interne -->
        <Ellipse Canvas.Left="66" Canvas.Top="66" Width="108" Height="108" Stroke="#99FFFFFF" StrokeThickness="1" StrokeDashArray="1 5" RenderTransformOrigin="0.5,0.5">
          <Ellipse.RenderTransform><RotateTransform x:Name="rotInner" Angle="0"/></Ellipse.RenderTransform></Ellipse>
        <!-- particules en orbite -->
        <Canvas Canvas.Left="120" Canvas.Top="120" RenderTransformOrigin="0,0">
          <Canvas.RenderTransform><RotateTransform x:Name="rotOrbit" Angle="0"/></Canvas.RenderTransform>
          <Ellipse Canvas.Left="92" Canvas.Top="-3" Width="6" Height="6" Fill="#FFFFFFFF"><Ellipse.Effect><DropShadowEffect Color="#$A" BlurRadius="10" ShadowDepth="0"/></Ellipse.Effect></Ellipse>
          <Ellipse Canvas.Left="-96" Canvas.Top="-2" Width="4" Height="4" Fill="#CC$A"/>
          <Ellipse Canvas.Left="-4" Canvas.Top="86" Width="5" Height="5" Fill="#EEFFFFFF"/>
          <Ellipse Canvas.Left="-2" Canvas.Top="-94" Width="3" Height="3" Fill="#AA$A"/>
        </Canvas>
        <!-- coeur -->
        <Ellipse x:Name="coreGlow" Canvas.Left="86" Canvas.Top="86" Width="68" Height="68" Fill="#55$A"><Ellipse.Effect><BlurEffect Radius="30"/></Ellipse.Effect></Ellipse>
        <Ellipse Canvas.Left="90" Canvas.Top="90" Width="60" Height="60" RenderTransformOrigin="0.5,0.5">
          <Ellipse.Fill><RadialGradientBrush><GradientStop Color="#FFFFFFFF" Offset="0"/><GradientStop Color="#FF$A" Offset="0.55"/><GradientStop Color="#FF00A6D6" Offset="1"/></RadialGradientBrush></Ellipse.Fill>
          <Ellipse.Effect><DropShadowEffect Color="#$A" BlurRadius="45" ShadowDepth="0"/></Ellipse.Effect>
          <Ellipse.RenderTransform><ScaleTransform x:Name="pulse" ScaleX="1" ScaleY="1"/></Ellipse.RenderTransform></Ellipse>
      </Canvas>

      <StackPanel Grid.Row="1" Margin="0,0,0,30" HorizontalAlignment="Center">
        <TextBlock Text="J · A · R · V · I · S" FontFamily="Consolas" FontSize="32" FontWeight="Bold" Foreground="#EAF7FF" HorizontalAlignment="Center">
          <TextBlock.Effect><DropShadowEffect Color="#$A" BlurRadius="24" ShadowDepth="0"/></TextBlock.Effect></TextBlock>
        <TextBlock x:Name="sub" Text="BOOT PORTABLE" FontFamily="Consolas" FontSize="12" Foreground="#9FD8EC" HorizontalAlignment="Center" Margin="0,2,0,12"/>
        <Border Width="380" Height="5" CornerRadius="3" Background="#15FFFFFF" HorizontalAlignment="Center">
          <Border x:Name="barFill" Width="60" Height="5" CornerRadius="3" HorizontalAlignment="Left">
            <Border.Background><LinearGradientBrush StartPoint="0,0" EndPoint="1,0"><GradientStop Color="#00$A" Offset="0"/><GradientStop Color="#FF$A" Offset="1"/></LinearGradientBrush></Border.Background>
          </Border>
        </Border>
        <TextBlock x:Name="status" Text="Initialisation..." FontFamily="Consolas" FontSize="13" Foreground="#CFEFF9" HorizontalAlignment="Center" TextAlignment="Center" Width="560" TextWrapping="Wrap" Margin="0,12,0,0"/>
      </StackPanel>
    </Grid>
  </Border>
</Window>
"@

[xml]$xdoc = $xaml
$reader = New-Object System.Xml.XmlNodeReader $xdoc
$win = [Windows.Markup.XamlReader]::Load($reader)
$status = $win.FindName("status"); $win.FindName("sub").Text = $Subtitle

if ($ValidateOnly) {
    $need = @('status','sub','rotOuter','rotMid','rotInner','pulse','barFill')
    $miss = $need | Where-Object { $null -eq $win.FindName($_) }
    if ($miss.Count) { throw "elements manquants : $($miss -join ', ')" }
    Write-Output "splash XAML : charge OK (accent $Accent, reduceMotion $([bool]$ReduceMotion))"
    return
}
if (-not $StatusFile) { throw "StatusFile requis (ou -ValidateOnly)." }

$script:closing = $false
function Close-Nicely {
    if ($script:closing) { return }
    $script:closing = $true
    $fade = New-Object System.Windows.Media.Animation.DoubleAnimation(1, 0, (New-Object System.Windows.Duration ([TimeSpan]::FromSeconds(0.55))))
    $fade.Add_Completed({ $win.Close() })
    $win.BeginAnimation([System.Windows.Window]::OpacityProperty, $fade)
}
$timer = New-Object System.Windows.Threading.DispatcherTimer
$timer.Interval = [TimeSpan]::FromMilliseconds(180)
$timer.Add_Tick({
    try {
        if (Test-Path $StatusFile) {
            $lines = @(Get-Content -LiteralPath $StatusFile -ErrorAction SilentlyContinue | Where-Object { $_ -ne '' })
            if ($lines.Count) {
                $last = $lines[-1]
                if ($last -eq '__READY__') { $timer.Stop(); Close-Nicely; return }
                if ($last -eq '__CLOSE__') { $timer.Stop(); $win.Close(); return }
                $status.Text = $last
            }
        }
    } catch {}
})
$win.Add_Loaded({ $timer.Start() })
$win.Add_Closed({ $timer.Stop() })
[void]$win.ShowDialog()
