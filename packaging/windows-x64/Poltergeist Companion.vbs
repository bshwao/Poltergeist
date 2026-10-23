Option Explicit
Dim files, shell, base, python, command
Set files = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
base = files.GetParentFolderName(WScript.ScriptFullName)
python = files.BuildPath(base, "runtime\pythonw.exe")
command = Chr(34) & python & Chr(34) & " -I -B -m scripts.companion"
shell.Run command, 1, False
