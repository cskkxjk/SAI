# Windows Installer

The installer is built from a clean Python 3.14 / llama.cpp b10621 portable build.
It does not package the existing `dist/SAI` user directory.

```powershell
uv sync --locked --group build
uv run --locked --group build python -m PyInstaller --noconfirm --distpath build/installer-stage build-desktop.spec
& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" installer\SAI.iss
```

The compiler path depends on where Inno Setup was installed.
Output: `dist/installer/SAI-1.0.0-Setup.exe`.
This single-file installer contains the program and runtime, but no model weights.
Old `Setup-*.bin` files are no longer needed.

The wizard offers a destination directory, Start Menu shortcuts,
an optional desktop shortcut and an uninstaller. It installs for the current user
without requiring administrator privileges. SenseVoice is the default model.
After launch, select a model and use its ModelScope download button. Files are
downloaded into `models` beside the installed EXE, with size/SHA256 verification.
The selected installation directory must be writable by the current user.
Downloads are direct (no proxy), cancellable, and reuse completed verified files.
Downloaded models are not registered as installer files and are retained on
uninstall; remove the models directory manually if it is no longer needed.

The installer ships factory configuration only. At first launch, the application
copies defaults into `%LOCALAPPDATA%\SAI` if files are missing.
Existing settings, hotwords, LLM role files and recordings are not overwritten.
Uninstall removes installer-managed program files but retains the user data directory.
The portable version continues to keep its data next to the executable.

Old portable data is not migrated automatically. Exit both versions, back up files,
then copy `config_gui.json`, `hot.txt`, `hot-rule.txt`, `hot-server.txt`, `LLM`
and desired recording directories into the installed version's user data directory.
Select a microphone again when moving to another PC.

Do not replace the new `core` or `internal` with files from an older release.
Changing Python or a DLL on the machine does not update a previously built EXE.
The executable and runtime must be rebuilt together.

`ChineseSimplified.isl` is the contributed Simplified Chinese translation from
`jrsoftware/issrc`, tag `is-6_7_3`, path
`Files/Languages/Unofficial/ChineseSimplified.isl` (Git blob
`d6a11c4490de07dad443ade668289fc954dfa1ed`). Its original attribution is retained.
