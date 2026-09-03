Option Explicit

Dim fileSystem, shell, pythonwPath, scriptPath, command, index
Set fileSystem = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

If WScript.Arguments.Count < 2 Then
    MsgBox "The local proxy launcher is missing its startup arguments.", 16, "Codex Local Proxy"
    WScript.Quit 1
End If

pythonwPath = fileSystem.GetAbsolutePathName(WScript.Arguments(0))
scriptPath = fileSystem.GetAbsolutePathName(WScript.Arguments(1))

If Not fileSystem.FileExists(pythonwPath) Then
    MsgBox "Python was not found:" & vbCrLf & pythonwPath, 16, "Codex Local Proxy"
    WScript.Quit 1
End If

If Not fileSystem.FileExists(scriptPath) Then
    MsgBox "The local proxy entry point was not found:" & vbCrLf & scriptPath, 16, "Codex Local Proxy"
    WScript.Quit 1
End If

command = QuoteArgument(pythonwPath) & " " & QuoteArgument(scriptPath)
For index = 2 To WScript.Arguments.Count - 1
    command = command & " " & QuoteArgument(WScript.Arguments(index))
Next

shell.CurrentDirectory = fileSystem.GetParentFolderName(scriptPath)
shell.Run command, 0, False

Function QuoteArgument(value)
    QuoteArgument = Chr(34) & Replace(value, Chr(34), Chr(34) & Chr(34)) & Chr(34)
End Function
