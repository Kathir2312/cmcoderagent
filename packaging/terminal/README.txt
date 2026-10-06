{product} {version} for the terminal ({platform})
=================================================

Nothing else is needed: no Python, no Node.js. (On Windows, shell commands
need Git for Windows, as before; `{command} doctor` checks it.)

Install
-------
Windows:  double-click install.cmd
          (or: powershell -ExecutionPolicy Bypass -File install.ps1)
          Installs into %LOCALAPPDATA%\Programs\cmcoder and adds it to your
          PATH. No administrator rights.
macOS, Linux:  sh install.sh
          Installs into ~/.local/share/cmcoder and links ~/.local/bin/cmcoder.

Then open a NEW terminal and run:

    {command} doctor
    {command}

Update:    run the install script of the new version.
Uninstall: uninstall.cmd (Windows) or sh uninstall.sh. Your settings,
           sessions and keys are kept.

If your company doesn't allow the script, copy the "cmcoder" folder anywhere
you like and add that folder to your PATH yourself.

The IDE plugins (VS Code, Visual Studio, Eclipse, NetBeans) contain their own
copy of the program, so you don't need this for them.
