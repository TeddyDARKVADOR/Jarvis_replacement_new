<#
  ask_launch.ps1 -- demande elegante « Lancer JARVIS sur ce PC ? ».

  Fenetre WPF (carte sombre, coeur ARC pulse, deux boutons) qui remplace la
  MessageBox. Renvoie le choix par CODE DE SORTIE : 0 = Oui, 1 = Non.
  Entree = Oui, Echap = Non. Facultatif -TimeoutSeconds => Non automatique.

      powershell -STA -File ask_launch.ps1 -Drive "E:" -Volume "Ventoy" [-Accent "#00E5FF"]
                 [-TimeoutSeconds 0] [-ReduceMotion] [-ValidateOnly]
#>
[CmdletBinding()]
param(
    [string]$Drive = "E:",
    [string]$Volume = "",
    [string]$Accent = "#00E5FF",
    [int]$TimeoutSeconds = 0,
    [switch]$ReduceMotion,
    [switch]$ValidateOnly
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase
if ($Accent -notmatch '^#[0-9A-Fa-f]{6}$') { $Accent = "#00E5FF" }
$A = $Accent.Substring(1)
$pulse = if ($ReduceMotion) { "" } else {
  '<DoubleAnimation Storyboard.TargetName="pz" Storyboard.TargetProperty="ScaleX" From="0.85" To="1.15" Duration="0:0:1.0" AutoReverse="True" RepeatBehavior="Forever"/><DoubleAnimation Storyboard.TargetName="pz" Storyboard.TargetProperty="ScaleY" From="0.85" To="1.15" Duration="0:0:1.0" AutoReverse="True" RepeatBehavior="Forever"/>' }

$xaml = @"
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        WindowStyle="None" AllowsTransparency="True" Background="Transparent"
        WindowStartupLocation="CenterScreen" Topmost="True" ResizeMode="NoResize"
        Width="440" Height="300" Opacity="0" Name="Win">
  <Window.Triggers><EventTrigger RoutedEvent="FrameworkElement.Loaded"><BeginStoryboard><Storyboard>
    <DoubleAnimation Storyboard.TargetName="Win" Storyboard.TargetProperty="Opacity" From="0" To="1" Duration="0:0:0.35"/>
    $pulse
  </Storyboard></BeginStoryboard></EventTrigger></Window.Triggers>
  <Border CornerRadius="18" BorderThickness="1" BorderBrush="#44$A" Background="#060D15">
    <Grid Margin="26">
      <Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/><RowDefinition Height="*"/><RowDefinition Height="Auto"/></Grid.RowDefinitions>
      <StackPanel Grid.Row="0" Orientation="Horizontal" HorizontalAlignment="Center">
        <Canvas Width="54" Height="54" VerticalAlignment="Center" Margin="0,0,14,0">
          <Ellipse Canvas.Left="7" Canvas.Top="7" Width="40" Height="40" Stroke="#AA$A" StrokeThickness="2" StrokeDashArray="3 4"/>
          <Ellipse Canvas.Left="17" Canvas.Top="17" Width="20" Height="20" RenderTransformOrigin="0.5,0.5">
            <Ellipse.Fill><RadialGradientBrush><GradientStop Color="#FFFFFFFF" Offset="0"/><GradientStop Color="#FF$A" Offset="1"/></RadialGradientBrush></Ellipse.Fill>
            <Ellipse.Effect><DropShadowEffect Color="#$A" BlurRadius="22" ShadowDepth="0"/></Ellipse.Effect>
            <Ellipse.RenderTransform><ScaleTransform x:Name="pz" ScaleX="1" ScaleY="1"/></Ellipse.RenderTransform></Ellipse>
        </Canvas>
        <TextBlock Text="J · A · R · V · I · S" FontFamily="Consolas" FontSize="22" FontWeight="Bold" Foreground="#EAF7FF" VerticalAlignment="Center">
          <TextBlock.Effect><DropShadowEffect Color="#$A" BlurRadius="18" ShadowDepth="0"/></TextBlock.Effect></TextBlock>
      </StackPanel>
      <TextBlock Grid.Row="1" Margin="0,16,0,0" Text="Clé JARVIS détectée" FontFamily="Consolas" FontSize="15" Foreground="#CFEFF9" HorizontalAlignment="Center"/>
      <TextBlock Grid.Row="2" x:Name="info" Margin="0,8,0,0" FontFamily="Consolas" FontSize="12" Foreground="#8FC9DD" HorizontalAlignment="Center" TextAlignment="Center" TextWrapping="Wrap"/>
      <StackPanel Grid.Row="3" Orientation="Horizontal" HorizontalAlignment="Center" Margin="0,8,0,0">
        <Button x:Name="yes" Width="150" Height="40" Margin="0,0,12,0" Foreground="#041018" FontFamily="Consolas" FontSize="13" FontWeight="Bold" BorderThickness="0" Cursor="Hand">
          <Button.Content>Oui, lancer</Button.Content>
          <Button.Background><LinearGradientBrush StartPoint="0,0" EndPoint="1,1"><GradientStop Color="#FF$A" Offset="0"/><GradientStop Color="#FFFFFFFF" Offset="1.4"/></LinearGradientBrush></Button.Background>
        </Button>
        <Button x:Name="no" Width="110" Height="40" Foreground="#CFEFF9" FontFamily="Consolas" FontSize="13" Background="#10202C" BorderBrush="#2A4A5E" BorderThickness="1" Cursor="Hand" Content="Non"/>
      </StackPanel>
    </Grid>
  </Border>
</Window>
"@

[xml]$xdoc = $xaml
$win = [Windows.Markup.XamlReader]::Load((New-Object System.Xml.XmlNodeReader $xdoc))
$info = $win.FindName("info")
$info.Text = "Lancer JARVIS sur ce PC depuis $Drive $Volume ?`nRien ne sera installé sur l'ordinateur."
$yes = $win.FindName("yes"); $no = $win.FindName("no")

if ($ValidateOnly) {
    $miss = @('yes','no','info','pz') | Where-Object { $null -eq $win.FindName($_) }
    if ($miss.Count) { throw "elements manquants : $($miss -join ', ')" }
    Write-Output "ask_launch XAML : charge OK (accent $Accent)"
    return
}

$script:choice = 1   # defaut Non
$yes.Add_Click({ $script:choice = 0; $win.Close() })
$no.Add_Click({ $script:choice = 1; $win.Close() })
$win.Add_KeyDown({ param($s,$e)
    if ($e.Key -eq 'Return' -or $e.Key -eq 'Enter') { $script:choice = 0; $win.Close() }
    elseif ($e.Key -eq 'Escape') { $script:choice = 1; $win.Close() } })
if ($TimeoutSeconds -gt 0) {
    $t = New-Object System.Windows.Threading.DispatcherTimer
    $t.Interval = [TimeSpan]::FromSeconds($TimeoutSeconds)
    $t.Add_Tick({ $t.Stop(); $script:choice = 1; $win.Close() })
    $win.Add_Loaded({ $t.Start() })
}
[void]$win.ShowDialog()
exit $script:choice
