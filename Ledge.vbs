' Ledge launcher: starts Ledge without a console window.
' If the environment has not been set up yet, offers to run setup.bat.
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
Set ws = CreateObject("WScript.Shell")
ws.CurrentDirectory = dir
venv = dir & "\.venv"
pyw = venv & "\Scripts\pythonw.exe"
' .venv 存在但依赖没装进去（setup.bat 中途失败、或 pip 装到了全局 Python）也要提示，否则会静默报 No module named 'PySide6'
If fso.FileExists(pyw) And Not fso.FolderExists(venv & "\Lib\site-packages\PySide6") Then
    msg = ".venv 里还没有装好依赖（找不到 PySide6）。" & vbCrLf & vbCrLf & _
          "现在运行 setup.bat 把依赖装进 .venv 吗？" & vbCrLf & _
          "（装完后再双击 Ledge.vbs 启动）"
    If MsgBox(msg, vbYesNo + vbQuestion, "Ledge") = vbYes Then
        ws.Run """" & dir & "\setup.bat""", 1, False
    End If
    WScript.Quit
End If
If Not fso.FileExists(pyw) Then
    msg = "还没有安装运行环境（找不到 .venv）。" & vbCrLf & vbCrLf & _
          "现在运行 setup.bat 自动安装吗？" & vbCrLf & _
          "（需要先装好 Python 3.10 或更新版本；安装完成后再双击 Ledge.vbs 启动）"
    If MsgBox(msg, vbYesNo + vbQuestion, "Ledge") = vbYes Then
        ws.Run """" & dir & "\setup.bat""", 1, False
    End If
    WScript.Quit
End If

' 用 uv 创建的 .venv 里，Scripts\pythonw.exe 其实是控制台程序（会再启动带控制台的 python.exe），
' 设为默认终端的 Windows Terminal 会因此弹出一个空白窗口。
' 所以直接启动基础解释器自带的无窗口 pythonw.exe，并通过 __PYVENV_LAUNCHER__ 让它使用 .venv 的依赖。
exe = pyw
If IsConsoleExe(pyw) Then
    home = VenvHome(venv & "\pyvenv.cfg")
    If home <> "" And fso.FileExists(home & "\pythonw.exe") Then
        ws.Environment("Process")("__PYVENV_LAUNCHER__") = venv & "\Scripts\python.exe"
        exe = home & "\pythonw.exe"
    End If
End If
ws.Run """" & exe & """ """ & dir & "\run.pyw""", 0, False

' pyvenv.cfg 里的 home = 基础解释器目录
Function VenvHome(cfg)
    VenvHome = ""
    If Not fso.FileExists(cfg) Then Exit Function
    Set f = fso.OpenTextFile(cfg, 1)
    Do While Not f.AtEndOfStream
        line = f.ReadLine
        If LCase(Left(Trim(line), 4)) = "home" And InStr(line, "=") > 0 Then
            VenvHome = Trim(Mid(line, InStr(line, "=") + 1))
        End If
    Loop
    f.Close
End Function

' 读 PE 头的 Subsystem 字段：3 = 控制台程序，2 = 窗口程序
Function IsConsoleExe(path)
    IsConsoleExe = False
    On Error Resume Next
    Set st = CreateObject("ADODB.Stream")
    st.Type = 1
    st.Open
    st.LoadFromFile path
    If Err.Number <> 0 Then Exit Function
    st.Position = &H3C
    b = st.Read(4)
    pe = AscB(MidB(b, 1, 1)) + 256 * AscB(MidB(b, 2, 1)) + 65536 * AscB(MidB(b, 3, 1))
    st.Position = pe + &H5C
    s = st.Read(2)
    IsConsoleExe = (AscB(MidB(s, 1, 1)) = 3)
    st.Close
End Function
