# Uninstall the App

Uninstalling the app removes the program only. Your Projects and your per-user settings file stay
where they are. This page says what each one is, where it is, and how to delete it by hand.

## Before you uninstall

Write down two locations. The app shows both on its **Settings** tab:

- **Projects folder**: the folder that holds your Projects.
- **Per-user settings file**: the file that holds your API keys and the settings the app remembers.

Once the program is removed, the Settings tab is gone, so note the paths first.

Stop the app before you remove it. Click **Stop the app** on the Home screen, or press Ctrl+C in the
terminal where `prismabib gui` is running.

## Remove the program

```bash
uv tool uninstall prismabib
```

This removes the program that the one-line install added: the `prismabib` command and the app.

## What stays

### Your Projects

Each Project is a folder inside the Projects folder. A Project folder holds:

- `project.toml` and `criteria.yaml`: the query and the eligibility rules. You may have edited them
  by hand.
- `decisions/`: the Decision log.
- `raw/`: the searches as they were captured, including every sealed run.
- `store/`: the built store. The build can rebuild it from `raw/`.
- `exports/`: the citable bundle.

The Projects folder is the one you chose on the Home screen. If you never chose one, it is a folder
named `projects` in the folder you started the app from. The Settings tab shows the exact path.

Only `store/` can be rebuilt from the other folders. Deleting `raw/` or `decisions/` loses your
captured searches and your screening Decisions.

### Your per-user settings file

The app keeps a file named `settings.env` in a `prismabib` folder inside your user configuration
folder. It holds your API keys, the Projects folder you chose, and your reviewer name. The app also
keeps `gui.log` in the same folder. It records unexpected errors, and it never contains a key.

| System | Location |
| --- | --- |
| Windows | `%APPDATA%\prismabib\settings.env`, usually `C:\Users\<you>\AppData\Roaming\prismabib\settings.env` |
| macOS | `~/Library/Application Support/prismabib/settings.env` |
| Linux | `$XDG_CONFIG_HOME/prismabib/settings.env`, or `~/.config/prismabib/settings.env` when `XDG_CONFIG_HOME` is not set |

The Settings tab shows the exact path, which is the reliable source if your system differs.

## Delete them by hand

1. Copy anything you need to keep, such as exports or Project files you want outside the app. The
   steps below cannot be undone.
2. Delete the Projects folder in your file manager. This removes every Project in it.
3. In the `prismabib` folder of your user configuration folder, delete `settings.env` and `gui.log`.
   Then delete the `prismabib` folder if it is empty. Deleting `settings.env` removes your saved
   keys. A key still works at its service until you revoke it on that service's account page.
4. If you also used the command line, `prismabib credentials set` may have written a `.env` file in
   the repository folder you ran it from. The app does not touch that file. Delete it there if you
   do not need it.

## Reinstall later

If you install the app again with the same one-line install, it reads the same settings file and
remembers the same Projects folder. Your Projects open as they did before.
