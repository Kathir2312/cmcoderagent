{product} {version} for the terminal ({platform})
=================================================

Nothing else is needed: no Python, no Node.js. (On Windows, shell commands
need Git for Windows, as before; `{command} doctor` checks it.)

Install
-------
Windows:  double-click install.cmd
          (or: powershell -ExecutionPolicy Bypass -File install.ps1)
          Installs into %USERPROFILE%\.cmcoder\bin and adds it to your
          PATH. No administrator rights.
macOS, Linux:  sh install.sh
          Installs into ~/.cmcoder/bin and links ~/.local/bin/cmcoder.

That folder holds the one cmcoder for the terminal and every IDE plugin.
An install in the old place (%LOCALAPPDATA%\Programs\cmcoder, or
~/.local/share/cmcoder) is moved. The installer also makes your cmcoder
folder (%USERPROFILE%\.cmcoder, ~/.cmcoder) with your company's settings, if
they came with this package and you have none yet.

Then open a NEW terminal and run:

    {command} setup     (only if the installer said "No settings yet")
    {command} doctor
    {command}

Update:    run the install script of the new version.
Uninstall: uninstall.cmd (Windows) or sh uninstall.sh. Your settings,
           sessions and keys are kept.

The folder can't be changed while cmcoder runs from it (a terminal or an
IDE): close them first. To install elsewhere: install.ps1 -Destination DIR,
or CMCODER_INSTALL_DIR=DIR sh install.sh.

If your company doesn't allow the script, copy the "cmcoder" folder anywhere
you like and add that folder to your PATH yourself.

The IDE plugins (VS Code, Visual Studio, Eclipse, NetBeans) contain their own
copy of the program, so you don't need this for them: they put it in the same
folder themselves, and all of them use the newest copy there.
