# Launchpad Mapper
A powerful Windows desktop application built with PySide6 to map your Novation Launchpad MKII pads to custom actions, colors, and animations.

<!-- Screenshots Section -->
<!-- <img src="path/to/screenshot1.png" width="32%"><img src="path/to/screenshot2.png" width="32%"> -->

> [!WARNING]
> **Disclaimer:** This software is designed specifically for the Novation Launchpad MKII. While it may work with other MIDI controllers, full compatibility is not guaranteed.

# Configuration

> [!CAUTION]
> Always backup your existing presets before updating or making major changes.

> [!IMPORTANT]
> User data and presets are stored in `%APPDATA%/LaunchpadMapper`.
> **Crucial:** If you are manually editing YAML files, ensure valid syntax to avoid crashes on startup.

<details>
  <summary>Manual Installation (For Developers)</summary>
  
  1. **Clone the repository:**
     ```powershell
     git clone https://github.com/callei/Launchpad-MKII-Mapper.git
     cd Launchpad-MKII-Mapper
     ```

  2. **Set up Environment:**
     Ensure you have Python 3.11 installed.
     ```powershell
     python -m venv .venv
     .venv\Scripts\activate
     pip install -r requirements.txt
     ```
     *(Note: You might need to install dependencies manually if no requirements.txt exists, see "Download Suggestions" below)*

  3. **Run from source:**
     ```powershell
     python gui/launchpad_mapper.py
     ```
</details>

## Features

<details>
  <summary> Grid & Mapping</summary>
  
  ## Overview
  - **Interface**: Full 8x8 grid + control pads UI with live color preview.
  - **Layers**: Support for multiple layers with quick navigation pads.
  - **Offline Mode**: Works fully without a Launchpad connected (Virtual Mode).
</details>

<details>
  <summary> Actions & Macros</summary>
  
  ## Capabilities
  Assign various actions to any pad:
  - **Launch App**: Open any executable or file.
  - **Run Process**: Execute background commands.
  - **Hotkeys**: Send keyboard shortcuts and sequences.
  - **Media Controls**: Play/Pause, Volume, Next/Prev Track.
  - **Switch Layer**: Jump between different mapping layers.
</details>

<details>
  <summary> Animations & Colors</summary>
  
  ## Visuals
  - **Static Colors**: Simple color assignment per pad.
  - **Animations**: Create simple keyframe animations with custom durations and looping.
</details>

<details>
  <summary> System Integration</summary>
  
  ## Settings
  - **Startup**: Options for Normal, Minimized, or Hidden (Tray only).
  - **Autostart**: Toggle "Start with Windows" directly in settings.
  - **Tray Icon**: Quick access to Open, Reconnect, Settings, and Quit.
</details>

## Command Line Arguments

`--hidden` - Start the application hidden in the system tray.  
`--preset <name>` - Load a specific preset immediately after start.  
`--debug` - Write a startup log to `%APPDATA%/LaunchpadMapper/startup.log`.

## Download Suggestions (Dev Dependencies)

To build or run this project from source, you will need these packages:

```txt
PySide6
mido
python-rtmidi
pyyaml
pywin32
```

## License

This project is licensed under a custom license. See the [LICENSE](LICENSE) file for details.
