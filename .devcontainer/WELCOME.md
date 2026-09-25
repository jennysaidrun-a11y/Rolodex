# Supplier Rolodex

The app starts by itself. Open it from the **Ports** tab (bottom panel): port **8000**, "Supplier
Rolodex", then click the globe icon. That address also works on your phone once you're signed in
to GitHub there. Bookmark it or add it to your home screen.

**First time only: sign Claude Code in** so the app's **Done adding: analyze now** button works.
Open a terminal (Terminal menu, then New Terminal), type `claude`, and follow the sign-in link with
your Claude account. When it shows its prompt, type `/exit`. That's it.

- Everything you add is saved to GitHub every few minutes (the note at the bottom of each page
  says when), so the Codespace can be stopped or even deleted without losing anything.
- The app updates itself: when a new version is on GitHub it picks it up within a minute and
  restarts. Nothing to pull, push or rebuild.
- The Codespace stops after a while with nobody using it. Start it again from
  https://github.com/codespaces. To give long analyses time to finish, set the idle timeout
  to 240 minutes: https://github.com/settings/codespaces, under "Default idle timeout".
- If the page doesn't open, the app's log is in `/tmp/rolodex.log`
  (terminal: `tail -50 /tmp/rolodex.log`); restart it with `bash .devcontainer/start.sh`.
