<#
  setup_wizard.ps1 -- assistant de premiere configuration de Matthew portable.

  Fenetre simple : coller une ou plusieurs cles Gemini (une par ligne), cocher
  eventuellement « chiffrer avec une phrase » (coffre), et enregistrer. Ecrit
  app\config\api_keys.json ({ "gemini_api_keys": [...] }). Le lanceur l'ouvre tout
  seul au premier demarrage s'il ne trouve ni api_keys.json ni api_keys.enc.

      powershell -ExecutionPolicy Bypass -File setup_wizard.ps1 [-ConfigDir <dir>] [-ValidateOnly]
#>
[CmdletBinding()]
param(
    [string]$ConfigDir,
    [switch]$ValidateOnly
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase

if (-not $ConfigDir) { $ConfigDir = Join-Path (Split-Path -Parent $PSScriptRoot) 'config' }

[xml]$xaml = @"
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Matthew portable — configuration" WindowStartupLocation="CenterScreen"
        Width="560" Height="520" ResizeMode="NoResize" Background="#0B1622">
  <Grid Margin="24">
    <Grid.RowDefinitions>
      <RowDefinition Height="Auto"/><RowDefinition Height="Auto"/><RowDefinition Height="*"/>
      <RowDefinition Height="Auto"/><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/>
    </Grid.RowDefinitions>
    <TextBlock Grid.Row="0" Text="J · A · R · V · I · S" FontFamily="Consolas" FontSize="24"
               FontWeight="Bold" Foreground="#EAF7FF"/>
    <TextBlock Grid.Row="1" Margin="0,6,0,10" Foreground="#9FDCEE" FontFamily="Consolas" FontSize="12"
               TextWrapping="Wrap"
               Text="Colle ta ou tes cle(s) Gemini, une par ligne. Plusieurs cles = plus de quota : Matthew bascule quand l'une est epuisee."/>
    <TextBox Grid.Row="2" x:Name="keys" AcceptsReturn="True" TextWrapping="Wrap"
             VerticalScrollBarVisibility="Auto" FontFamily="Consolas" FontSize="13"
             Background="#07101A" Foreground="#Dff4ff" BorderBrush="#2A4A5E" Padding="8"/>
    <CheckBox Grid.Row="3" x:Name="enc" Margin="0,12,0,0" Foreground="#BFEAF6" FontFamily="Consolas"
              IsChecked="True" Content="Chiffrer clés + mémoire avec une phrase de passe (recommandé)"/>
    <StackPanel Grid.Row="4" Orientation="Horizontal" Margin="0,10,0,0">
      <TextBlock Text="Thème :" Foreground="#9FDCEE" FontFamily="Consolas" VerticalAlignment="Center" Margin="0,0,8,0"/>
      <ComboBox x:Name="accent" Width="130" FontFamily="Consolas">
        <ComboBoxItem Content="Cyan" Tag="#00E5FF" IsSelected="True"/>
        <ComboBoxItem Content="Or" Tag="#FFC857"/>
        <ComboBoxItem Content="Vert" Tag="#5CF2A0"/>
        <ComboBoxItem Content="Violet" Tag="#B98CFF"/>
        <ComboBoxItem Content="Rouge" Tag="#FF5C6E"/>
      </ComboBox>
      <CheckBox x:Name="motion" Margin="18,0,0,0" Foreground="#BFEAF6" FontFamily="Consolas"
                VerticalAlignment="Center" Content="Mouvement réduit (accessibilité)"/>
    </StackPanel>
    <StackPanel Grid.Row="5" Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,16,0,0">
      <TextBlock x:Name="info" Foreground="#6FD3EE" FontFamily="Consolas" FontSize="11"
                 VerticalAlignment="Center" Margin="0,0,16,0"/>
      <Button x:Name="save" Content="Enregistrer" Width="130" Height="34"
              Background="#113A4E" Foreground="#EAF7FF" FontFamily="Consolas" BorderBrush="#2A6A86"/>
    </StackPanel>
  </Grid>
</Window>
"@

$reader = New-Object System.Xml.XmlNodeReader $xaml
$win = [Windows.Markup.XamlReader]::Load($reader)
$keys = $win.FindName("keys"); $enc = $win.FindName("enc")
$save = $win.FindName("save"); $info = $win.FindName("info")
$accent = $win.FindName("accent"); $motion = $win.FindName("motion")

if ($ValidateOnly) {
    $miss = @('keys','enc','save','info','accent','motion') | Where-Object { $null -eq $win.FindName($_) }
    if ($miss.Count) { throw "elements manquants : $($miss -join ', ')" }
    Write-Output "setup_wizard XAML : charge OK"
    return
}

# data\ de la cle = <keyRoot>\data ; keyRoot = parent de app ; app = parent de portable
$dataDir = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'data'
function Save-Settings {
    try {
        New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
        $acc = if ($accent.SelectedItem) { [string]$accent.SelectedItem.Tag } else { '#00E5FF' }
        @{ accent = $acc; reduceMotion = [bool]$motion.IsChecked } |
            ConvertTo-Json | Set-Content -LiteralPath (Join-Path $dataDir 'portable.json') -Encoding UTF8
    } catch {}
}

# pre-remplir si une config existe deja
$cfgFile = Join-Path $ConfigDir 'api_keys.json'
if (Test-Path $cfgFile) {
    try {
        $j = Get-Content $cfgFile -Raw | ConvertFrom-Json
        $existing = @()
        if ($j.gemini_api_keys) { $existing += $j.gemini_api_keys }
        if ($j.gemini_api_key)  { $existing += $j.gemini_api_key }
        $keys.Text = ($existing | Where-Object { $_ } | Select-Object -Unique) -join "`r`n"
    } catch {}
}

$save.Add_Click({
    $list = @($keys.Text -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' } | Select-Object -Unique)
    if (-not $list.Count) { $info.Text = "Aucune cle saisie."; return }
    New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
    $data = [ordered]@{ gemini_api_keys = $list }
    # conserver les autres champs existants
    if (Test-Path $cfgFile) {
        try {
            $old = Get-Content $cfgFile -Raw | ConvertFrom-Json
            foreach ($p in $old.PSObject.Properties) {
                if ($p.Name -notin @('gemini_api_keys','gemini_api_key')) { $data[$p.Name] = $p.Value }
            }
        } catch {}
    }
    ($data | ConvertTo-Json -Depth 6) | Set-Content -LiteralPath $cfgFile -Encoding UTF8
    Save-Settings
    $info.Text = "$($list.Count) cle(s) enregistree(s)."
    if ($enc.IsChecked) {
        $win.Hide()
        $app = Split-Path -Parent $PSScriptRoot
        # manifeste : cles + memoire (chiffrement total par defaut)
        @{ protect = @('config/api_keys.json','memory/long_term.json',
                       'memory/incidents.json','memory/cognition.db') } |
            ConvertTo-Json | Set-Content -LiteralPath (Join-Path $ConfigDir 'vault.json') -Encoding UTF8
        $keyPy = Join-Path (Split-Path -Parent $app) 'runtime\python\python.exe'
        $py = if (Test-Path $keyPy) { $keyPy } else { 'python' }
        & $py (Join-Path $PSScriptRoot 'vault.py') lock --root $app
    }
    $win.Close()
})

[void]$win.ShowDialog()
