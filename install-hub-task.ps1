<#
  install-hub-task.ps1 —— 把「每日签到」计划任务指向**签到台**（幂等，可反复跑）

  为什么需要这个脚本
  ------------------
  上游的 install-windows.ps1 注册的任务跑的是 `signin.py silent`（只有 WorkBuddy 站点）。
  签到台（signin_hub.py）要一次跑完所有站点，包括豆包 —— 那是需要 9222 调试端口的
  桌面端自动化，**不能**混进 signin.py 里。

  所以：本脚本只做一件事 —— 把 WorkBuddyAutoSignin 这个任务的**动作**换成
  `pythonw.exe signin_hub.py run all --json --log logs\hub.log`，
  **触发器、账号、状态全部保留**（任务不存在时才用 -Time 新建）。

  ★ 什么时候要跑：① 换机器 / 重装；② 有人重跑了上游 install-windows.ps1（它会把动作覆盖回去）。

  用法
  ----
      powershell -ExecutionPolicy Bypass -File install-hub-task.ps1
      powershell -ExecutionPolicy Bypass -File install-hub-task.ps1 -Time 00:05 -DryRun
      powershell -ExecutionPolicy Bypass -File install-hub-task.ps1 -NoRetry

  ★ 2026-11-16 追加：**按需补跑**任务（`<TaskName>Retry`，默认 12:30）
    原因：主任务每天只有 00:05 一次机会。11-16 那天豆包因为「页面还停在骨架屏」
    失败，白天就再没补上，白丢 200 额度。
    补跑动作是 `signin_hub.py retry` —— **只在今天确有 failed 时才真跑**；
    全成时它只读一行日志就退出，不会去弹豆包窗口抢焦点。
    这是个**独立任务**，不动主任务的触发器/动作。

  退出码：0 成功 / 1 找不到 pythonw / 2 注册失败
#>
[CmdletBinding()]
param(
    [string]$TaskName = 'WorkBuddyAutoSignin',
    [string]$Pythonw  = '',
    [string]$Time     = '00:05',
    [string]$RetryTime = '12:30',
    [switch]$NoRetry,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$hub  = Join-Path $root 'signin_hub.py'
$log  = Join-Path $root 'logs\hub.log'

if (-not (Test-Path $hub)) {
    Write-Host "[X] signin_hub.py not found: $hub" -ForegroundColor Red
    exit 1
}

# ---- 找 pythonw.exe（无控制台，任务里跑它才不会弹黑框）----
if (-not $Pythonw) {
    $cands = @(
        'C:\Program Files\Python314\pythonw.exe',
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python314\pythonw.exe')
    )
    $cmd = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if ($cmd) { $cands += $cmd.Source }
    foreach ($c in $cands) {
        if ($c -and (Test-Path $c)) { $Pythonw = $c; break }
    }
}
if (-not $Pythonw -or -not (Test-Path $Pythonw)) {
    Write-Host "[X] pythonw.exe not found. Pass -Pythonw <path>." -ForegroundColor Red
    exit 1
}

$arg = '"{0}" run all --json --log "{1}"' -f $hub, $log
$rarg = '"{0}" retry --json --log "{1}"' -f $hub, $log
Write-Host "task    : $TaskName"
Write-Host "pythonw : $Pythonw"
Write-Host "args    : $arg"
Write-Host "workdir : $root"
if (-not $NoRetry) {
    Write-Host "retry   : $TaskName`Retry @ $RetryTime  →  $rarg"
}

if ($DryRun) { Write-Host "(dry-run, nothing changed)"; exit 0 }

try {
    $act = New-ScheduledTaskAction -Execute $Pythonw -Argument $arg -WorkingDirectory $root
    $set = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 20) `
                                        -StartWhenAvailable -MultipleInstances IgnoreNew

    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        # ★ 只替换 action 与 settings，触发器/principal 原样保留
        Set-ScheduledTask -TaskName $TaskName -Action $act -Settings $set | Out-Null
        Write-Host "[OK] updated existing task (triggers kept)" -ForegroundColor Green
    } else {
        $at = [datetime]::ParseExact($Time, 'HH:mm', $null)
        $trg = New-ScheduledTaskTrigger -Daily -At $at
        Register-ScheduledTask -TaskName $TaskName -Action $act -Trigger $trg -Settings $set | Out-Null
        Write-Host "[OK] created new task (daily at $Time)" -ForegroundColor Green
    }

    # ---- 按需补跑任务（独立任务，失败不影响主任务）----
    if (-not $NoRetry) {
        $rname = "$TaskName`Retry"
        try {
            $rat  = [datetime]::ParseExact($RetryTime, 'HH:mm', $null)
            $ract = New-ScheduledTaskAction -Execute $Pythonw -Argument $rarg -WorkingDirectory $root
            $rtrg = New-ScheduledTaskTrigger -Daily -At $rat
            $rset = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 20) `
                                                -StartWhenAvailable -MultipleInstances IgnoreNew
            $rex = Get-ScheduledTask -TaskName $rname -ErrorAction SilentlyContinue
            if ($rex) {
                Set-ScheduledTask -TaskName $rname -Action $ract -Trigger $rtrg -Settings $rset | Out-Null
                Write-Host "[OK] updated retry task ($rname @ $RetryTime)" -ForegroundColor Green
            } else {
                Register-ScheduledTask -TaskName $rname -Action $ract -Trigger $rtrg -Settings $rset | Out-Null
                Write-Host "[OK] created retry task ($rname @ $RetryTime)" -ForegroundColor Green
            }
        }
        catch {
            # ★ 补跑任务建不成就降级说明，**不能**把主任务的结果一起判死
            Write-Host "[!] retry task NOT installed: $($_.Exception.Message)" -ForegroundColor Yellow
            Write-Host "    (主任务不受影响；可稍后用管理员身份重跑本脚本)" -ForegroundColor Yellow
        }
    }

    $t = Get-ScheduledTask -TaskName $TaskName
    Write-Host ""
    Write-Host "action  : $($t.Actions[0].Execute) $($t.Actions[0].Arguments)"
    Write-Host "workdir : $($t.Actions[0].WorkingDirectory)"
    Write-Host "limit   : $($t.Settings.ExecutionTimeLimit)"
    Write-Host "trigger : $(($t.Triggers | ForEach-Object { $_.StartBoundary }) -join '; ')"
    $rt = Get-ScheduledTask -TaskName "$TaskName`Retry" -ErrorAction SilentlyContinue
    if ($rt) {
        Write-Host "retry   : $($rt.Actions[0].Arguments)"
        Write-Host "rtrigger: $(($rt.Triggers | ForEach-Object { $_.StartBoundary }) -join '; ')"
    }
    Write-Host ""
    Write-Host "verify  : Start-ScheduledTask -TaskName $TaskName ; then tail '$log'" -ForegroundColor Cyan
    exit 0
}
catch {
    Write-Host "[X] failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 2
}
