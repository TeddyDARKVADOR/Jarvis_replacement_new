<#
  farewell.ps1 -- animation de retrait de JARVIS portable (extinction du reacteur).

  Sequence « power-down » : flash du coeur, onde de choc qui se propage, anneaux
  qui se dispersent et s'effacent, le coeur s'eteint, puis le message
  « JARVIS se retire — rien laisse sur ce PC ». Fermeture auto (~3 s).

      powershell -STA -File farewell.ps1 [-Message "..."] [-Accent "#00E5FF"]
                 [-ReduceMotion] [-SettingsFile <portable.json>] [-ValidateOnly]
#>
[CmdletBinding()]
param(
    [string]$Message = "JARVIS se retire — rien laissé sur ce PC.",
    [string]$MessageFile,
    [string]$Accent = "#00E5FF",
    [switch]$ReduceMotion,
    [string]$SettingsFile,
    [switch]$ValidateOnly
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase

if ($MessageFile -and (Test-Path $MessageFile)) {
    try {
        $m = (Get-Content -LiteralPath $MessageFile -Raw -Encoding UTF8).Trim()
        if ($m) { $Message = $m }
    } catch {}
}
if ($SettingsFile -and (Test-Path $SettingsFile)) {
    try { $s = Get-Content $SettingsFile -Raw | ConvertFrom-Json
          if ($s.accent) { $Accent = [string]$s.accent }
          if ($s.reduceMotion) { $ReduceMotion = [bool]$s.reduceMotion } } catch {}
}
if ($Accent -notmatch '^#[0-9A-Fa-f]{6}$') { $Accent = "#00E5FF" }
$A = $Accent.Substring(1)
$dur = if ($ReduceMotion) { "0:0:1.2" } else { "0:0:2.6" }

# storyboard riche, ou simple fondu en mouvement reduit
if ($ReduceMotion) {
    $anim = @"
          <DoubleAnimation Storyboard.TargetName="Win" Storyboard.TargetProperty="Opacity" From="0" To="1" Duration="0:0:0.5"/>
          <DoubleAnimation Storyboard.TargetName="Win" Storyboard.TargetProperty="Opacity" From="1" To="0" BeginTime="0:0:2.0" Duration="0:0:0.6"/>
"@
} else {
    $anim = @"
          <DoubleAnimation Storyboard.TargetName="Win" Storyboard.TargetProperty="Opacity" From="0" To="1" Duration="0:0:0.4"/>
          <DoubleAnimation Storyboard.TargetName="flash" Storyboard.TargetProperty="Opacity" From="0" To="0.9" BeginTime="0:0:0.3" Duration="0:0:0.12" AutoReverse="True"/>
          <DoubleAnimation Storyboard.TargetName="shockS" Storyboard.TargetProperty="ScaleX" From="0.2" To="3.2" BeginTime="0:0:0.35" Duration="0:0:1.1"/>
          <DoubleAnimation Storyboard.TargetName="shockS" Storyboard.TargetProperty="ScaleY" From="0.2" To="3.2" BeginTime="0:0:0.35" Duration="0:0:1.1"/>
          <DoubleAnimation Storyboard.TargetName="shock" Storyboard.TargetProperty="Opacity" From="0.8" To="0" BeginTime="0:0:0.35" Duration="0:0:1.1"/>
          <DoubleAnimation Storyboard.TargetName="pulse" Storyboard.TargetProperty="ScaleX" From="1" To="0" BeginTime="0:0:0.45" Duration="0:0:1.2"/>
          <DoubleAnimation Storyboard.TargetName="pulse" Storyboard.TargetProperty="ScaleY" From="1" To="0" BeginTime="0:0:0.45" Duration="0:0:1.2"/>
          <DoubleAnimation Storyboard.TargetName="ringS" Storyboard.TargetProperty="ScaleX" From="1" To="1.6" BeginTime="0:0:0.4" Duration="0:0:1.4"/>
          <DoubleAnimation Storyboard.TargetName="ringS" Storyboard.TargetProperty="ScaleY" From="1" To="1.6" BeginTime="0:0:0.4" Duration="0:0:1.4"/>
          <DoubleAnimation Storyboard.TargetName="rings" Storyboard.TargetProperty="Opacity" From="1" To="0" BeginTime="0:0:0.5" Duration="0:0:1.3"/>
          <DoubleAnimation Storyboard.TargetName="spin" Storyboard.TargetProperty="Angle" From="0" To="150" Duration="0:0:2.0"/>
          <DoubleAnimation Storyboard.TargetName="msg" Storyboard.TargetProperty="Opacity" From="0" To="1" BeginTime="0:0:1.0" Duration="0:0:0.6"/>
          <DoubleAnimation Storyboard.TargetName="Win" Storyboard.TargetProperty="Opacity" From="1" To="0" BeginTime="0:0:2.5" Duration="0:0:0.5"/>
"@
}

$xaml = @"
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        WindowStyle="None" AllowsTransparency="True" Background="Transparent"
        WindowStartupLocation="CenterScreen" Topmost="True" ResizeMode="NoResize"
        Width="640" Height="440" Opacity="0" Name="Win">
  <Window.Triggers><EventTrigger RoutedEvent="FrameworkElement.Loaded"><BeginStoryboard><Storyboard>
    $anim
  </Storyboard></BeginStoryboard></EventTrigger></Window.Triggers>
  <Border CornerRadius="20" BorderThickness="1" BorderBrush="#33$A" Background="#02060B">
    <Grid>
      <Grid.Background><RadialGradientBrush GradientOrigin="0.5,0.42" Center="0.5,0.42" RadiusX="0.8" RadiusY="0.8">
        <GradientStop Color="#FF0A1B2A" Offset="0"/><GradientStop Color="#FF01040A" Offset="1"/></RadialGradientBrush></Grid.Background>
      <Grid.RowDefinitions><RowDefinition Height="*"/><RowDefinition Height="Auto"/></Grid.RowDefinitions>
      <Canvas Grid.Row="0" Width="240" Height="240" HorizontalAlignment="Center" VerticalAlignment="Center">
        <!-- onde de choc -->
        <Ellipse x:Name="shock" Canvas.Left="90" Canvas.Top="90" Width="60" Height="60" Opacity="0" Stroke="#$A" StrokeThickness="2" RenderTransformOrigin="0.5,0.5">
          <Ellipse.RenderTransform><ScaleTransform x:Name="shockS" ScaleX="1" ScaleY="1"/></Ellipse.RenderTransform></Ellipse>
        <!-- anneaux -->
        <Canvas x:Name="rings" Width="240" Height="240" RenderTransformOrigin="0.5,0.5">
          <Canvas.RenderTransform><TransformGroup><RotateTransform x:Name="spin" Angle="0"/><ScaleTransform x:Name="ringS" ScaleX="1" ScaleY="1" CenterX="120" CenterY="120"/></TransformGroup></Canvas.RenderTransform>
          <Ellipse Canvas.Left="14" Canvas.Top="14" Width="212" Height="212" Stroke="#55$A" StrokeThickness="2" StrokeDashArray="2 6"/>
          <Ellipse Canvas.Left="40" Canvas.Top="40" Width="160" Height="160" Stroke="#CC$A" StrokeThickness="3" StrokeDashArray="20 12"><Ellipse.Effect><DropShadowEffect Color="#$A" BlurRadius="18" ShadowDepth="0"/></Ellipse.Effect></Ellipse>
        </Canvas>
        <!-- flash -->
        <Ellipse x:Name="flash" Canvas.Left="80" Canvas.Top="80" Width="80" Height="80" Opacity="0" Fill="#FFFFFFFF"><Ellipse.Effect><BlurEffect Radius="30"/></Ellipse.Effect></Ellipse>
        <!-- coeur -->
        <Ellipse Canvas.Left="90" Canvas.Top="90" Width="60" Height="60" RenderTransformOrigin="0.5,0.5">
          <Ellipse.Fill><RadialGradientBrush><GradientStop Color="#FFFFFFFF" Offset="0"/><GradientStop Color="#FF$A" Offset="0.55"/><GradientStop Color="#FF00A6D6" Offset="1"/></RadialGradientBrush></Ellipse.Fill>
          <Ellipse.Effect><DropShadowEffect Color="#$A" BlurRadius="40" ShadowDepth="0"/></Ellipse.Effect>
          <Ellipse.RenderTransform><ScaleTransform x:Name="pulse" ScaleX="1" ScaleY="1"/></Ellipse.RenderTransform></Ellipse>
      </Canvas>
      <StackPanel Grid.Row="1" Margin="0,0,0,40" HorizontalAlignment="Center">
        <TextBlock Text="J · A · R · V · I · S" FontFamily="Consolas" FontSize="26" FontWeight="Bold" Foreground="#EAF7FF" HorizontalAlignment="Center">
          <TextBlock.Effect><DropShadowEffect Color="#$A" BlurRadius="18" ShadowDepth="0"/></TextBlock.Effect></TextBlock>
        <TextBlock x:Name="msg" Opacity="0" FontFamily="Consolas" FontSize="13" Foreground="#9FDCEE" HorizontalAlignment="Center" TextAlignment="Center" Width="560" TextWrapping="Wrap" Margin="0,8,0,0"/>
      </StackPanel>
    </Grid>
  </Border>
</Window>
"@

[xml]$xdoc = $xaml
$win = [Windows.Markup.XamlReader]::Load((New-Object System.Xml.XmlNodeReader $xdoc))
$win.FindName("msg").Text = $Message
if ($ReduceMotion) { $win.FindName("msg").Opacity = 1 }

if ($ValidateOnly) {
    $miss = @('rings','spin','pulse','msg','shock','flash') | Where-Object { $null -eq $win.FindName($_) }
    if ($miss.Count) { throw "elements manquants : $($miss -join ', ')" }
    Write-Output "farewell XAML : charge OK (accent $Accent, reduceMotion $([bool]$ReduceMotion))"
    return
}
$timer = New-Object System.Windows.Threading.DispatcherTimer
$timer.Interval = [TimeSpan]::FromSeconds(3.1)
$timer.Add_Tick({ $timer.Stop(); $win.Close() })
$win.Add_Loaded({ $timer.Start() })
[void]$win.ShowDialog()
