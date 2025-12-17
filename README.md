# Launchpad Mapper
A powerful Windows desktop application built with PySide6 to map your Novation Launchpad MKII pads to custom actions, colors, and animations.

<!-- Screenshots Section -->
<!-- I should implement this soon but man I don't want to right now.>
<!-- <img src="path/to/screenshot1.png" width="32%"><img src="path/to/screenshot2.png" width="32%"> -->

> [!WARNING]
> **Disclaimer:** This software is designed specifically for the Novation Launchpad MKII. While it may work with other MIDI controllers, full compatibility is not guaranteed.

# Configuration

> [!NOTE]
> User data and presets are stored in `%APPDATA%/LaunchpadMapper`.

<details>
  <summary>Manual Installation</summary>
  
  1. **Clone the repository:**
     ```powershell
     git clone https://github.com/callei/Launchpad-MKII-Mapper.git
     cd Launchpad-MKII-Mapper
     ```

  2. **Set up Environment:**
    Ensure you have Python 3.11 installed.
    ```powershell
    # (Optional) Create and activate a virtual environment
    python -m venv .venv
    .venv\Scripts\activate  # On Windows
    # Install dependencies
    pip install -r requirements.txt
    ```
    *(All required dependencies are listed in `requirements.txt`)*

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
`--background` - Alias for `--hidden`. Starts the app hidden in the system tray (for compatibility with some launchers/scripts).  
`--preset <name>` - Load a specific preset immediately after start.  
`--debug` - Write a startup log to `%APPDATA%/LaunchpadMapper/startup.log`.  
`--autostart` - Used internally when the app is started automatically with Windows. Not intended for manual use.

## License

This project is licensed under a custom license. See the [LICENSE](LICENSE) file for details.
