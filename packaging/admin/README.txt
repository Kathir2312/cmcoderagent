cmcoder: for IT administrators
==============================

Managed settings are the rules developers can't change: which gateway
cmcoder uses, commands it may never run, permission modes that are off.
Every user's cmcoder on the PC reads one file, in a folder only
administrators can write:

  Windows   C:\Program Files\cmcoder\managed-settings.json
  macOS     /Library/Application Support/cmcoder/managed-settings.json
  Linux     /etc/cmcoder/managed-settings.json

1. The file
   managed-settings.json in this folder is your company's, if it was set
   before the build (branding/managed-settings.json). Otherwise start from
   managed-settings.example.json: replace the YOUR-... values, remove what
   you don't need, and save it as managed-settings.json here.

2. One PC
   Windows: right-click install-managed.cmd, Run as administrator.
   macOS, Linux: sudo sh install-managed.sh
   The script checks the file (a broken one would stop cmcoder for every
   user) and puts it in place. Run it again after changing the file.

3. Many PCs
   Deploy the same file to the same place with Intune, Group Policy,
   Configuration Manager, Jamf or your Linux configuration tool. Keep the
   folder writable by administrators only.

Check on a developer's PC:  cmcoder doctor   (it shows the managed file in force)

Developers don't need administrator rights for anything else: cmcoder
installs per user in %USERPROFILE%\.cmcoder\bin (~/.cmcoder/bin), and their
own settings are %USERPROFILE%\.cmcoder\settings.json.

The full guide: docs/admin/README.md in the repository.
