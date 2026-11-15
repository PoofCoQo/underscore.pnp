# underscore.pnp

*At least the music stays on track.*

### Background music without the background hassle.

Every Dungeon Master knows the situation: The party is approaching the final confrontation, tension is building, and the carefully selected background music decides it's time for something completely different.

**underscore.pnp** is a lightweight audio loop player built to keep your Pen & Paper sessions sounding the way you intended.

Prepare your tracks, define loops and transitions, and switch between them using your mouse, touchscreen, or keyboard. Keep the atmosphere going while you focus on the important things. Like explaining why the obviously suspicious door was, in fact, suspicious.

No streaming service required. No unexpected playlist recommendations. Just your music, your settings, and your session.

**A few things worth knowing:**

- Your audio files remain untouched.
- Music is not included. You'll need to bring your own.
- Local playback works offline.
- Discord playback is available if your table happens to be somewhere else.
- Loop configurations can be exported and shared without distributing copyrighted audio.

This README covers installation and everyday use. If you're interested in what happens behind the scenes, the [technical documentation](TECHNICAL.md) has you covered. Currently in German, because apparently the code wasn't complicated enough.

---

## Installation

underscore.pnp is designed to be portable. Your music library stays beside the app, and Python packages go into a virtual environment in the project folder. The Discord token, logs, and optional Linux application launcher use their respective per-user locations.

Extract the entire project into a folder where you have write access. Keep `src`, `pyproject.toml`, and the launch scripts together.

The initial setup requires an internet connection to download the necessary Python packages. After that, local playback does not.

### Windows

**Requirements:**

- 64-bit Windows 10 or 11
- 64-bit Python 3.12 through 3.15
- Python accessible through `py` or `python`

**Getting started:**

1. Extract the project into a folder of your choice.
2. Double-click `setup.bat` and let it do its thing.
3. The setup window remains open afterward, so you can check whether everything went according to plan.
4. Double-click `start.bat` to launch underscore.pnp.

**Important:** Avoid installing the project under `Program Files`.

underscore.pnp keeps its portable music library in the adjacent `data` directory and needs permission to write there. Windows and protected directories have a complicated relationship.

### Linux

**Requirements:**

- A graphical Wayland or X11 session
- Python 3.11 through 3.15

On Debian or Ubuntu, install these system packages for the full feature set:

```bash
sudo apt install python3 python3-venv python3-dev libsndfile1 portaudio19-dev libopus0
```

`libopus0` is only needed for the optional Discord voice output, not for local playback.

Then open a terminal in the project directory:

```bash
bash setup.sh
bash start.sh
```

Alternatively, you can open `setup.desktop` to launch the installer in a visible terminal.

Depending on your file manager, you may first need to mark the file as trusted or executable.

**Application launcher management**

During an interactive setup, you can choose whether to install, keep, or remove the launcher in your applications menu.

For non-interactive installation, the existing launcher remains unchanged unless you explicitly request otherwise.

```bash
# Install or update the application launcher
bash setup.sh --install-launcher

# Skip the launcher prompt
bash setup.sh --no-launcher

# Remove the application launcher
bash setup.sh --remove-launcher
```

Only launchers created by underscore.pnp will be removed.

If you move the project directory, run `bash setup.sh --install-launcher` again to update the launcher's path.

---

## Setting up your music library

Before you can provide a dramatic soundtrack to your players' questionable decisions, you'll need some music.

### Step 1 — Add your audio files

Copy your music files into:

`data/music`

If the directory doesn't exist yet, launch underscore.pnp once or create it manually.

Subdirectories are supported, so you can organize your collection however you prefer.

**Supported formats:**

- FLAC
- WAV
- OGG
- MP3, provided your system's audio library supports it

Simply copying a file into the music folder does not automatically import it into your library.

### Step 2 — Import a track

1. Launch underscore.pnp.
2. Open **SETUP**.
3. Select **Open…** at the top.
4. Choose your audio file.
5. Let underscore.pnp analyze it.

Once analysis is complete, you can configure the track.

### Step 3 — Define your loops

Under **Sections**, you'll find suggested boundaries for:

- **Play** — The track's playback range.
- **Intro** — The opening section.
- **Main Loop** — The section that repeats.
- **Outro** — The section played when leaving the track.

Adjust the markers by dragging them or using the position controls.

You can also define alternative loops and additional exit points.

**A useful detail:** Your sections don't need to touch.

An Intro can end well before the Main Loop begins, and an Outro can start well after the loop ends.

During normal playback, underscore.pnp plays the Intro, jumps to the Main Loop, and repeats it. Audio between these sections is skipped.

The music keeps going where you want it to, regardless of what the original composer had planned.

### Step 4 — Add track details

Under **Track Details**, you can configure:

- Display name
- Artist
- Album
- Category
- Tags
- Attribution

Optional **Beat & Meter** and **Loudness** settings provide further adjustments.

These details help keep your library organized when your collection inevitably grows beyond what any reasonable person would consider necessary for a tabletop game.

### Step 5 — Save

Press **Save** at the top.

Your track will only appear in the saved library after you do this.

You'll then find its card under **PLAYER**, ready for its moment of questionable narrative importance.

---

## Running a session

This is where underscore.pnp is supposed to make your life easier.

### Playing and queueing tracks

Select a track card in **PLAYER** to start playback.

If another track is already playing, your selection becomes **NEXT** instead.

There is only one NEXT slot.

- Selecting another track replaces the current NEXT selection.
- Selecting the highlighted yellow NEXT card again removes it from the queue, as long as the current track is still in its loop. Once the loop exit has begun, the transition can no longer be cancelled this way.

This lets you prepare the next piece of music without interrupting the current one.

Because the players may be improvising wildly, but your soundtrack doesn't have to.

### Normal transitions

During a normal transition, the current track continues looping until it reaches its next exit point, or the end of its loop at the latest.

The Outro then plays once in full.

Afterward, the queued track begins its Intro without an additional pause.

You can also choose where the next track should start:

- Intro
- Main Loop
- An alternative loop
- Outro

The selected starting section is highlighted in yellow.

### Changing loops

The loop buttons on the currently playing track let you queue a different loop section.

The change takes effect at the next exit point.

If another track is queued as **NEXT**, that track takes priority at the exit point instead of the loop change.

Select the queued loop again to cancel the change.

### Immediate transitions

Sometimes the story takes an unexpected turn.

For those occasions, underscore.pnp provides several transition modes:

| Mode | Purpose |
| --- | --- |
| **Normal** | Wait for the regular loop exit and play the Outro |
| **Cut** | Switch immediately |
| **Fade** | Use a fade transition |
| **Crossfade** | Blend the transition between tracks |

Select **Cut**, **Fade**, or **Crossfade**, then press **GO NOW** to trigger the immediate transition.

You can also **Pause** playback or **Stop** it entirely.

### Finding your tracks

The track library provides several ways to organize and find your music.

- **Favorites:** Mark frequently used tracks with the star on their card.
- **Categories:** Filter tracks by their assigned category.
- **Albums:** Narrow your selection to a particular collection.

The pencil button on each track card opens that track directly in Setup for editing.

You may not be able to predict what your players will do next. At least you can find the appropriate soundtrack quickly.

---

## Keyboard shortcuts

Because taking your hands off the keyboard to operate a music player is one more thing you probably don't need.

Default shortcuts:

| Key | Action |
| --- | --- |
| `Space` | Pause / Resume |
| `S` | Stop |
| `G` | GO NOW |
| `1` | Normal |
| `2` | Cut |
| `3` | Fade |
| `4` | Crossfade |
| `M` | Mute local output |
| `Shift+M` | Mute Discord output |
| `Shift+1` through `Shift+9`, `Shift+0` | Start or queue favorites 1–10 |
| `F11` | Toggle fullscreen |

You can customize the bindings under **SETTINGS**.

---

## Audio and Discord

underscore.pnp supports local audio playback and optional Discord output.

### Local audio

Under **SETTINGS**, you can:

- Select your local audio output device.
- Adjust the audio cache size.
- Configure keyboard shortcuts.

The Player provides a dedicated **LOCAL** volume control and mute button.

### Discord

For remote sessions, underscore.pnp can connect to a Discord voice channel using a bot.

To configure Discord playback:

1. Open **SETTINGS**.
2. Enter your Discord bot token.
3. If the bot is not on your server yet, use **Add Bot to Server** to authorize it.
4. Select the server and voice channel.
5. Press **Join Channel**.

Use **Leave Channel** to disconnect the bot.

The Player provides separate volume and mute controls for **LOCAL** and **DISCORD**.

**Pause** and **Stop** affect both outputs.

Discord is entirely optional. Your local music player does not depend on it.

Your Discord bot token is not included in exported track configurations.

---

## Sharing loop configurations

### Good loops deserve to be shared. Music files don't have to be.

Finding the perfect loop points can take a while.

Once you've found them, there's no reason every other DM should have to repeat the process.

underscore.pnp supports exporting and importing track configurations independently of the actual music files.

That means you can share your carefully configured loops while everyone supplies their own audio.

### Exporting configurations

1. Open **SETUP**.
2. Select **Export…**.
3. Choose one or more tracks.
4. Select the destination directory.
5. Export the configurations.

By default, exports are saved in the project's `exports` directory.

The exported JSON files contain metadata only.

**They do not contain:**

- Audio files
- Waveform caches

Existing JSON files are only replaced after explicit confirmation.

If you've made changes to the current track, press **Save** before exporting.

Only tracks with a stored audio hash can be exported. If a track cannot be selected in the export list, open its audio file in Setup first so underscore.pnp can add the hash.

### Importing configurations

Received a configuration from another user?

1. Open **SETUP**.
2. Select **Import Config…**.
3. Choose the JSON configuration.
4. Assign your own copy of the corresponding audio file.

underscore.pnp checks the audio file's SHA-256 hash before analysis.

If the selected file differs from the one referenced by the configuration, you'll be offered three options:

- Cancel the import.
- Open the configuration once for testing.
- Deliberately replace the stored file association.

An existing local configuration is never replaced without your confirmation.

**Why the hash check?**

Different releases, edits, or encodings of the same music may not have identical timing.

A loop that works perfectly with one file might sound considerably less impressive with another.

The hash check helps you identify that difference before it becomes part of your session's soundtrack.

### Community configurations

The idea is simple: Configure once, share with others, and let everyone spend less time adjusting loop points.

If we open a community space, GitHub Discussions is the planned place for users to exchange configurations, compare alternatives, and share their experiences.

**Please share configurations, not copyrighted music files.**

Users are responsible for obtaining their own audio files and respecting the applicable rights.

---

## Archiving and backups

### Archiving tracks

Not every track needs to stay in your active library forever.

To archive a track:

**SETUP** → **Track Details** → **Archive Track**

Archiving removes the track from your active library without deleting its audio file.

The file remains in `data/music`.

Before archiving, save any unsaved changes. The track must be in `data/music` and cannot be the one currently playing or waiting as **NEXT**. Stop playback or remove it from NEXT first.

Use **Archived…** to restore an archived configuration.

If an audio file has been moved or is missing, use **Locate audio file** to reconnect it.

### Backing up your library

To back up your entire library, copy the complete `data` directory.

This includes:

- Music files
- Track configurations
- Waveform caches
- Archived configurations
- Player settings

**Important:** The `data` folder contains your actual audio files.

If you only want to share track settings, use the Export function instead.

underscore.pnp is released under the [MIT License](LICENSE).

That license applies to the software. It does not change the copyright or licensing conditions of your music.

---

## Troubleshooting

Occasionally, software does something unexpected.

We're as surprised as you are.

If underscore.pnp misbehaves, open:

**SETTINGS** → **Diagnostics & Logs**

From there, you can:

- Open the log directory.
- Copy a diagnostic summary.

If your audio device is missing, try refreshing the device list under audio settings or selecting **System Default**.

When reporting a problem, including relevant diagnostic information makes it much easier to investigate.

---

## About PoofCoQo

**Product Owner Of Flawed Code Of Questionable Origin**

*It works. We have questions too.*

underscore.pnp is a project by **PoofCoQo**, a one-human software workshop creating free and open-source tools for problems encountered in actual use.

All application code is generated using OpenAI Codex. The ideas, requirements, design decisions, and quality standards are defined and enforced by a human.

The software is tested in real-world use before release.

**The code is generated. The responsibility isn't.**


---

*Made by one human, with considerable help from a machine, coffee, and a colourful repertoire of curse words.*
